import hashlib
import hmac
import os
import time
from typing import Any

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from openai import OpenAI

load_dotenv()

GITHUB_API_BASE = "https://api.github.com"
GITHUB_ACCEPT = "application/vnd.github.v3+json"
USER_AGENT = "GitPrSummarizer/1.0"
ALLOWED_PR_ACTIONS = {"opened", "synchronize", "reopened"}


def require_env(name: str, description: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(f"Missing environment variable {name} ({description})")


def load_private_key() -> str:
    inline_key = os.getenv("GITHUB_PRIVATE_KEY")
    if inline_key:
        return inline_key

    private_key_path = os.getenv("GITHUB_PRIVATE_KEY_PATH")
    if not private_key_path:
        raise ValueError(
            "Missing GitHub private key. Set GITHUB_PRIVATE_KEY or "
            "GITHUB_PRIVATE_KEY_PATH."
        )

    try:
        with open(private_key_path, "r", encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError as exc:
        raise ValueError(
            f"Could not find private key file at {private_key_path}"
        ) from exc


APP_ID = require_env("GITHUB_APP_ID", "GitHub App ID")
WEBHOOK_SECRET = require_env("GITHUB_WEBHOOK_SECRET", "GitHub webhook secret")
OPENAI_API_KEY = require_env("OPENAI_API_KEY", "OpenAI API key")
PRIVATE_KEY = load_private_key()

openai_client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI()


def verify_signature(
    secret: str,
    body: bytes,
    sha1_signature: str | None,
    sha256_signature: str | None,
) -> None:
    if not (sha1_signature or sha256_signature):
        raise HTTPException(status_code=401, detail="Missing signature")

    if sha256_signature:
        sha256 = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256)
        expected_sha256 = f"sha256={sha256.hexdigest()}"
        if hmac.compare_digest(expected_sha256, sha256_signature):
            return

    if sha1_signature:
        sha1 = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha1)
        expected_sha1 = f"sha1={sha1.hexdigest()}"
        if hmac.compare_digest(expected_sha1, sha1_signature):
            return

    raise HTTPException(status_code=401, detail="Invalid signature")


def make_app_jwt(app_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (9 * 60),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": GITHUB_ACCEPT,
        "User-Agent": USER_AGENT,
    }


async def github_request(
    method: str,
    url: str,
    token: str,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        response = await http_client.request(
            method=method,
            url=url,
            headers=github_headers(token),
            json=json_body,
        )
    response.raise_for_status()
    return response.json()


async def get_installation_token(installation_id: int) -> str:
    app_jwt = make_app_jwt(APP_ID, PRIVATE_KEY)
    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    response = await github_request("POST", url, token=app_jwt)
    if not isinstance(response, dict) or "token" not in response:
        raise RuntimeError("GitHub did not return an installation token")
    return response["token"]


async def fetch_pr_files(
    installation_token: str,
    owner: str,
    repo: str,
    number: int,
) -> list[dict[str, Any]]:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/files"
    response = await github_request("GET", url, token=installation_token)
    if not isinstance(response, list):
        raise RuntimeError("Unexpected GitHub response for PR files")
    return response


async def post_pr_comment(
    installation_token: str,
    owner: str,
    repo: str,
    number: int,
    body: str,
) -> dict[str, Any]:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{number}/comments"
    response = await github_request(
        "POST",
        url,
        token=installation_token,
        json_body={"body": body},
    )
    if not isinstance(response, dict):
        raise RuntimeError("Unexpected GitHub response for comment creation")
    return response


def summarize_patch(filename: str, patch: str) -> str:
    if not patch:
        return f"- {filename}: No textual diff available."

    truncated_patch = patch[:12000]
    prompt = (
        "Summarize the code changes in 1-2 concise sentences.\n"
        "Focus on behavior and intent, not formatting.\n\n"
        f"File: {filename}\n\n"
        f"{truncated_patch}"
    )

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        summary = (response.choices[0].message.content or "").strip()
        if not summary:
            return f"- {filename}: Summary unavailable."
        return f"- {filename}: {summary}"
    except Exception as exc:
        return f"- {filename}: Failed to summarize ({exc})"


def build_pr_summary(files: list[dict[str, Any]]) -> str:
    summaries = [
        summarize_patch(file_info["filename"], file_info.get("patch", ""))
        for file_info in files
    ]
    return "AI PR Summary\n\n" + "\n".join(summaries)


@app.get("/healthz")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict[str, Any]:
    body = await request.body()
    verify_signature(
        WEBHOOK_SECRET,
        body,
        x_hub_signature,
        x_hub_signature_256,
    )
    payload = await request.json()

    if x_github_event != "pull_request":
        return {"ignored_event": x_github_event}

    action = payload.get("action")
    if action not in ALLOWED_PR_ACTIONS:
        return {"ignored_action": action}

    installation = payload.get("installation") or {}
    installation_id = installation.get("id")
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    pull_request_number = payload.get("number")

    if not installation_id or not repo_full_name or not pull_request_number:
        raise HTTPException(status_code=400, detail="Incomplete pull_request payload")

    owner, repo = repo_full_name.split("/", maxsplit=1)
    installation_token = await get_installation_token(installation_id)
    files = await fetch_pr_files(installation_token, owner, repo, pull_request_number)
    summary_body = build_pr_summary(files)
    await post_pr_comment(installation_token, owner, repo, pull_request_number, summary_body)

    return {"status": "comment_posted", "files": len(files)}
