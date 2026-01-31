"""
GitHub Webhook 签名校验与 payload 解析。
"""
import hashlib
import hmac
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"


def verify_signature(body: bytes, signature_256: str | None, secret: str) -> bool:
    """
    使用 HMAC-SHA256 校验 GitHub Webhook 签名。
    GitHub 发送的 X-Hub-Signature-256 格式为 "sha256=<hex>"。
    """
    if not secret or not signature_256 or not signature_256.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_256)


def parse_payload(body: bytes) -> dict[str, Any]:
    """
    解析 Webhook body：返回包含 repo、branch、commit 等字段的 payload 字典。
    event 类型由请求头 X-GitHub-Event 提供，不在此返回。
    """
    data = json.loads(body) if body else {}

    repo = data.get("repository", {})
    repo_name = repo.get("full_name") or repo.get("name") or ""
    branch = ""
    commit_sha = ""
    commit_message = ""

    if "ref" in data and data.get("ref", "").startswith("refs/heads/"):
        branch = data["ref"].replace("refs/heads/", "")
    if "head_commit" in data:
        commit_sha = data["head_commit"].get("id") or data["head_commit"].get("sha") or ""
        commit_message = (data["head_commit"].get("message") or "").strip()
    if "after" in data:
        commit_sha = commit_sha or data.get("after", "")
    if "pull_request" in data:
        pr = data["pull_request"]
        branch = pr.get("head", {}).get("ref") or branch
        commit_sha = pr.get("head", {}).get("sha") or commit_sha
    if "workflow_run" in data:
        wr = data["workflow_run"]
        branch = wr.get("head_branch") or branch
        commit_sha = wr.get("head_sha") or commit_sha

    return {
        "repo": repo_name,
        "branch": branch,
        "commit": commit_sha,
        "commit_message": commit_message,
        "payload": data,
    }
