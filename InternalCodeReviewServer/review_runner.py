"""
接收 PR 信息后克隆仓库，在 Claude Code 终端中执行 /code-review:code-review 进行 PR 审核。
依赖：本机已安装 Claude Code CLI（claude）、gh CLI，并配置 ANTHROPIC_API_KEY、GH_TOKEN。
"""
import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = os.environ.get("REPO_ROOT", tempfile.gettempdir())
CLAUDE_CLI = os.environ.get("CLAUDE_CLI", "claude")
# 在 Claude Code 终端中执行的 slash 命令，默认 /code-review:code-review
CLAUDE_CODE_REVIEW_CMD = os.environ.get("CLAUDE_CODE_REVIEW_CMD", "/code-review:code-review")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
CLAUDE_REVIEW_TIMEOUT = int(os.environ.get("CLAUDE_REVIEW_TIMEOUT", "600"))


def get_pr_info(payload: dict[str, Any]) -> tuple[str, int, str, str] | None:
    """
    从 GitHub Webhook payload 中解析 PR 信息。
    返回 (repo_full_name, pr_number, head_sha, base_sha) 或 None。
    """
    pr = payload.get("pull_request")
    repo = payload.get("repository", {})
    if not pr or not repo:
        return None
    repo_full_name = repo.get("full_name") or repo.get("name") or ""
    pr_number = pr.get("number")
    head = pr.get("head", {})
    base = pr.get("base", {})
    head_sha = head.get("sha") or ""
    base_sha = base.get("sha") or ""
    if not repo_full_name or pr_number is None or not head_sha:
        return None
    return (repo_full_name, int(pr_number), head_sha, base_sha)


def _clone_and_checkout(repo_full_name: str, head_sha: str, work_dir: Path) -> bool:
    """克隆仓库并 checkout 到 head_sha。使用 gh repo clone + git checkout。"""
    try:
        clone_dir = work_dir / repo_full_name.replace("/", "_")
        if clone_dir.exists():
            shutil.rmtree(clone_dir, ignore_errors=True)
        clone_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        if GH_TOKEN:
            env["GH_TOKEN"] = GH_TOKEN

        # gh repo clone owner/repo <dir>
        r = subprocess.run(
            ["gh", "repo", "clone", repo_full_name, str(clone_dir)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode != 0:
            logger.error("gh repo clone 失败: %s %s", r.stderr, r.stdout)
            return False

        # git checkout head_sha
        r2 = subprocess.run(
            ["git", "checkout", head_sha],
            cwd=str(clone_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r2.returncode != 0:
            logger.warning(
                "git checkout %s 失败，尝试 fetch: %s", head_sha[:7], r2.stderr
            )
            subprocess.run(
                ["git", "fetch", "origin", head_sha],
                cwd=str(clone_dir),
                env=env,
                capture_output=True,
                timeout=60,
            )
            subprocess.run(
                ["git", "checkout", head_sha],
                cwd=str(clone_dir),
                env=env,
                capture_output=True,
                timeout=60,
            )

        return True
    except Exception as e:
        logger.exception("克隆/checkout 失败: %s", e)
        return False


def _run_claude_code_review(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    work_dir: Path,
) -> bool:
    """
    在已 clone 的仓库目录中启动 Claude Code 终端，执行 /code-review:code-review，
    由 Claude Code 的 code-review 技能自动完成 PR 审核（如发评论等）。
    """
    clone_dir = work_dir / repo_full_name.replace("/", "_")
    if not clone_dir.exists():
        logger.error("仓库目录不存在: %s", clone_dir)
        return False

    env = os.environ.copy()
    if ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    if GH_TOKEN:
        env["GH_TOKEN"] = GH_TOKEN

    # claude -p "/code-review:code-review" 非交互执行 slash 命令
    cmd = [CLAUDE_CLI, "-p", CLAUDE_CODE_REVIEW_CMD]
    try:
        logger.info(
            "执行 Claude Code 终端 code review repo=%s pr=%s cwd=%s cmd=%s",
            repo_full_name,
            pr_number,
            clone_dir,
            CLAUDE_CODE_REVIEW_CMD,
        )
        r = subprocess.run(
            cmd,
            cwd=str(clone_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=CLAUDE_REVIEW_TIMEOUT,
        )
        if r.returncode != 0:
            logger.warning(
                "claude 退出码=%s stderr=%s",
                r.returncode,
                (r.stderr or "")[:500],
            )
        else:
            logger.info(
                "Claude Code code review 执行完成 repo=%s pr=%s",
                repo_full_name,
                pr_number,
            )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error(
            "claude 超时 repo=%s pr=%s", repo_full_name, pr_number
        )
        return False
    except Exception as e:
        logger.exception("Claude Code 执行异常: %s", e)
        return False


def _run_code_review_sync(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
) -> None:
    """同步执行：克隆 -> 在 Claude Code 终端执行 /code-review:code-review。"""
    work_dir = Path(REPO_ROOT)
    work_dir.mkdir(parents=True, exist_ok=True)
    if not _clone_and_checkout(repo_full_name, head_sha, work_dir):
        logger.error("克隆失败，跳过 code review repo=%s", repo_full_name)
        return
    _run_claude_code_review(
        repo_full_name, pr_number, head_sha, base_sha, work_dir
    )


async def run_code_review_async(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
) -> None:
    """异步执行 code review（在线程池中：克隆 + Claude Code 终端 /code-review）。"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _run_code_review_sync,
        repo_full_name,
        pr_number,
        head_sha,
        base_sha,
    )
