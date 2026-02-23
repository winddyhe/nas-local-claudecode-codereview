"""
内网 Code Review 服务：接收 NasWebhookServer 转发的 Webhook，
在 pull_request 时克隆仓库并在 Claude Code 终端执行 /code-review:code-review 进行 PR 审核。
"""
import asyncio
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

# 启动时加载 .env，使 LOCAL_REPO_PATH、CLAUDE_WORKING_DIR 等生效（在 import review_runner 前）
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path)

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from review_runner import run_code_review_async, get_pr_info

# 配置日志格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 记录启动配置
def _log_startup_config():
    """记录启动时的配置信息"""
    logger.info("=" * 60)
    logger.info("InternalCodeReviewServer 启动配置:")
    logger.info("  LOCAL_REPO_PATH: %s", os.environ.get("LOCAL_REPO_PATH", "(未设置)"))
    logger.info("  LOCAL_REPO_NAME: %s", os.environ.get("LOCAL_REPO_NAME", "(未设置)"))
    logger.info("  CLAUDE_WORKING_DIR: %s", os.environ.get("CLAUDE_WORKING_DIR", "(未设置)"))
    logger.info("  CLAUDE_SUBDIR: %s", os.environ.get("CLAUDE_SUBDIR", "(未设置)"))
    logger.info("  CLAUDE_CLI: %s", os.environ.get("CLAUDE_CLI", "claude"))
    logger.info("  CLAUDE_REVIEW_TIMEOUT: %s 秒", os.environ.get("CLAUDE_REVIEW_TIMEOUT", "600"))
    logger.info("  REPO_ROOT: %s", os.environ.get("REPO_ROOT", "(系统临时目录)"))
    logger.info("  GH_TOKEN: %s", "已配置" if os.environ.get("GH_TOKEN") else "未配置")
    logger.info("=" * 60)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    _log_startup_config()
    yield


app = FastAPI(title="InternalCodeReviewServer", lifespan=lifespan)


@app.get("/")
async def root():
    return {"service": "InternalCodeReviewServer", "webhook": "POST /webhook/trigger"}


@app.post("/webhook/trigger")
async def webhook_trigger(request: Request) -> JSONResponse:
    """
    接收 NasWebhookServer 转发的 payload：event, repo, branch, commit, payload。
    当 event 为 pull_request 时，在后台启动 Claude Code 终端执行 /code-review:code-review。
    """
    client_host = request.client.host if request.client else "unknown"

    try:
        body = await request.json()
    except Exception as e:
        logger.warning("[%s] 解析 body 失败: %s", client_host, e)
        return JSONResponse(status_code=400, content={"error": "invalid json"})

    event = body.get("event", "")
    repo = body.get("repo", "")
    payload = body.get("payload", {})

    logger.info("[%s] 收到 webhook event=%s repo=%s", client_host, event, repo)

    if event != "pull_request":
        logger.info("[%s] 忽略非 PR 事件 event=%s repo=%s", client_host, event, repo)
        return JSONResponse(status_code=200, content={"ok": True, "skipped": "not pull_request"})

    action = payload.get("action", "")
    if action == "closed":
        logger.info("[%s] 忽略已关闭的 PR event=%s repo=%s action=%s", client_host, event, repo, action)
        return JSONResponse(status_code=200, content={"ok": True, "skipped": "pull_request closed"})

    pr_info = get_pr_info(payload)
    if not pr_info:
        logger.warning("[%s] 无法从 payload 解析 PR 信息 repo=%s payload_keys=%s", client_host, repo, list(payload.keys())[:10])
        return JSONResponse(status_code=200, content={"ok": True, "skipped": "no pr info"})

    repo_full_name, pr_number, head_sha, base_sha = pr_info

    # 获取 PR 标题和作者（如果有）
    pr_data = payload.get("pull_request", {})
    pr_title = pr_data.get("title", "(无标题)")
    pr_author = pr_data.get("user", {}).get("login", "(未知)")
    pr_url = pr_data.get("html_url", "")

    logger.info(
        "[%s] PR 信息: repo=%s pr=#%s title='%s' author=%s",
        client_host, repo_full_name, pr_number, pr_title[:50], pr_author
    )
    logger.info(
        "[%s] PR SHA: head=%s base=%s",
        client_host, head_sha[:7], base_sha[:7]
    )
    if pr_url:
        logger.info("[%s] PR URL: %s", client_host, pr_url)

    # 异步执行 code review，立即返回 202
    def _on_done(t):
        if t.cancelled():
            logger.error("[callback] code review 任务被取消 repo=%s pr=%s", repo_full_name, pr_number)
        else:
            ex = t.exception()
            if ex is not None:
                logger.exception("[callback] code review 任务异常 repo=%s pr=%s: %s", repo_full_name, pr_number, ex)
            else:
                logger.info("[callback] code review 任务完成 repo=%s pr=%s", repo_full_name, pr_number)

    task = asyncio.create_task(run_code_review_async(repo_full_name, pr_number, head_sha, base_sha, pr_title, pr_author))
    task.add_done_callback(_on_done)

    logger.info("[%s] 已提交 code review 后台任务 repo=%s pr=%s head=%s", client_host, repo_full_name, pr_number, head_sha[:7])
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "accepted": True,
            "repo": repo_full_name,
            "pr": pr_number,
            "head_sha": head_sha[:7],
            "title": pr_title,
        },
    )
