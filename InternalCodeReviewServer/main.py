"""
内网 Code Review 服务：接收 NasWebhookServer 转发的 Webhook，
在 pull_request 时克隆仓库并在 Claude Code 终端执行 /code-review:code-review 进行 PR 审核。
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from review_runner import run_code_review_async, get_pr_info

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
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
    try:
        body = await request.json()
    except Exception as e:
        logger.warning("解析 body 失败: %s", e)
        return JSONResponse(status_code=400, content={"error": "invalid json"})

    event = body.get("event", "")
    repo = body.get("repo", "")
    payload = body.get("payload", {})

    if event != "pull_request":
        logger.info("忽略非 PR 事件 event=%s repo=%s", event, repo)
        return JSONResponse(status_code=200, content={"ok": True, "skipped": "not pull_request"})

    pr_info = get_pr_info(payload)
    if not pr_info:
        logger.warning("无法从 payload 解析 PR 信息 repo=%s", repo)
        return JSONResponse(status_code=200, content={"ok": True, "skipped": "no pr info"})

    repo_full_name, pr_number, head_sha, base_sha = pr_info

    # 异步执行 code review，立即返回 202
    asyncio.create_task(run_code_review_async(repo_full_name, pr_number, head_sha, base_sha))

    logger.info("已提交 code review 任务 repo=%s pr=%s head=%s", repo_full_name, pr_number, head_sha[:7])
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "accepted": True,
            "repo": repo_full_name,
            "pr": pr_number,
            "head_sha": head_sha[:7],
        },
    )
