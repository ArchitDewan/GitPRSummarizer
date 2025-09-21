import os
import hmac
import hashlib
import time
from typing import Dict, Any
from fastapi import FastAPI, Header, HTTPException, Request
from dotenv import load_dotenv
import httpx
import jwt
from openai import OpenAI

# --- Load environment variables early ---
load_dotenv()

# --- Required env vars ---
required_env_vars = {
    "GITHUB_APP_ID": "GitHub App ID",
    "GITHUB_PRIVATE_KEY_PATH": "Path to GitHub App Private Key PEM file",
    "GITHUB_WEBHOOK_SECRET": "GitHub Webhook Secret",
    "OPENAI_API_KEY": "OpenAI API key",
}

missing_vars = []
for var, description in required_env_vars.items():
    if var not in os.environ:
        missing_vars.append(f"{var} ({description})")

if missing_vars:
    raise ValueError(f"❌ Missing environment variables: {', '.join(missing_vars)}")

APP_ID = os.environ["GITHUB_APP_ID"]
PRIVATE_KEY_PATH = os.environ["GITHUB_PRIVATE_KEY_PATH"]
WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]

# Load private key
try:
    with open(PRIVATE_KEY_PATH, "r") as f:
        PRIVATE_KEY = f.read()
except FileNotFoundError:
    raise ValueError(f"❌ Could not find private key file at {PRIVATE_KEY_PATH}")

# OpenAI client
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# FastAPI app
app = FastAPI()

# --- Utils ---
def verify_signature(secret: str, body: bytes, sha1_sig: str | None, sha256_sig: str | None):
    """Verify GitHub webhook signatures (sha1 or sha256)."""
    if not (sha1_sig or sha256_sig):
        raise HTTPException(status_code=401, detail="Missing signature")

    if sha256_sig:
        mac = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256)
        expected = "sha256=" + mac.hexdigest()
        if hmac.compare_digest(expected, sha256_sig):
            return

    if sha1_sig:
        mac = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha1)
        expected = "sha1=" + mac.hexdigest()
        if hmac.compare_digest(expected, sha1_sig):
            return

    raise HTTPException(status_code=401, detail="Invalid signature")


def make_app_jwt(app_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": app_id}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


async def get_installation_token(installation_id: int) -> str:
    app_jwt = make_app_jwt(APP_ID, PRIVATE_KEY)
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitPRSummarizer/1.0"
        })
    r.raise_for_status()
    return r.json()["token"]


async def fetch_pr_files(token: str, owner: str, repo: str, number: int) -> list[Dict[str, Any]]:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/files"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitPRSummarizer/1.0"
        })
    r.raise_for_status()
    return r.json()


async def post_pr_comment(token: str, owner: str, repo: str, number: int, body: str):
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitPRSummarizer/1.0"
        }, json={"body": body})

        if r.status_code != 201:
            print("❌ Failed to post comment:", r.status_code, r.text, flush=True)
        r.raise_for_status()
        return r.json()


def summarize_patch(filename: str, patch: str) -> str:
    """Summarize a file diff with OpenAI."""
    if not patch:
        return f"- {filename}: No diff provided"

    prompt = f"Summarize the changes in this file: {filename}\n\n{patch}"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.choices[0].message.content
        return f"- {filename}: {summary}"
    except Exception as e:
        return f"- {filename}: ❌ Failed to summarize ({e})"


# --- Routes ---
@app.get("/healthz")
async def health():
    return {"ok": True}


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature: str | None = Header(None),
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str | None = Header(None),
):
    body = await request.body()
    verify_signature(WEBHOOK_SECRET, body, x_hub_signature, x_hub_signature_256)

    payload = await request.json()

    print("WEBHOOK REPO:", payload.get("repository", {}).get("full_name"))
    print("ACTION:", payload.get("action"))
    print("PR NUMBER:", payload.get("number"))

    if x_github_event != "pull_request":
        return {"ignored": x_github_event}

    action = payload.get("action")
    if action not in {"opened", "synchronize", "reopened"}:
        return {"ignored_action": action}

    installation_id = payload["installation"]["id"]
    repo_full = payload["repository"]["full_name"]  # "owner/repo"
    owner, repo = repo_full.split("/")
    number = payload["number"]

    token = await get_installation_token(installation_id)
    files = await fetch_pr_files(token, owner, repo, number)

    # Summarize each file
    summaries = [summarize_patch(f["filename"], f.get("patch", "")) for f in files]

    # Join into one comment
    summary = "🤖 AI PR Summary:\n\n" + "\n".join(summaries)

    print("DEBUG INSTALLATION TOKEN:", token[:20], "...", flush=True)

    await post_pr_comment(token, owner, repo, number, summary)
    return {"status": "comment_posted", "files": len(files)}
