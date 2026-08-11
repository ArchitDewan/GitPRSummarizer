import hashlib
import hmac
import importlib

import pytest
from fastapi import HTTPException


@pytest.fixture()
def app_module(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv(
        "GITHUB_PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
    )

    import main

    return importlib.reload(main)


def test_verify_signature_accepts_valid_sha256(app_module):
    body = b'{"action":"opened"}'
    digest = hmac.new(
        b"test-secret",
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    app_module.verify_signature(
        "test-secret",
        body,
        sha1_signature=None,
        sha256_signature=f"sha256={digest}",
    )


def test_verify_signature_rejects_invalid_signature(app_module):
    with pytest.raises(HTTPException) as exc:
        app_module.verify_signature(
            "test-secret",
            b"{}",
            sha1_signature=None,
            sha256_signature="sha256=bad",
        )

    assert exc.value.status_code == 401


def test_build_pr_summary_handles_missing_patch(app_module, monkeypatch):
    def fake_summarize(filename, patch):
        return f"- {filename}: patch={patch!r}"

    monkeypatch.setattr(app_module, "summarize_patch", fake_summarize)

    summary = app_module.build_pr_summary(
        [
            {"filename": "main.py", "patch": "+print('hello')"},
            {"filename": "image.png"},
        ]
    )

    assert summary == (
        "AI PR Summary\n\n"
        "- main.py: patch=\"+print('hello')\"\n"
        "- image.png: patch=''"
    )


@pytest.mark.asyncio
async def test_fetch_pr_files_reads_all_pages(app_module, monkeypatch):
    pages = {
        1: [{"filename": f"file-{index}.py"} for index in range(100)],
        2: [{"filename": "last.py"}],
    }

    async def fake_github_request(method, url, token, json_body=None, params=None):
        assert method == "GET"
        assert token == "token"
        return pages[params["page"]]

    monkeypatch.setattr(app_module, "github_request", fake_github_request)

    files = await app_module.fetch_pr_files("token", "owner", "repo", 1)

    assert len(files) == 101
    assert files[-1]["filename"] == "last.py"
