"""
NasWebhookServer：接收 GitHub Webhook，校验后通过 HTTP 转发到内网 API。
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from github import verify_signature, parse_payload, EVENT_HEADER, SIGNATURE_HEADER
from internal import send_to_internal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # 可在此做关闭逻辑


app = FastAPI(title="NasWebhookServer", lifespan=lifespan)


@app.get("/")
async def root():
    return {"service": "NasWebhookServer", "webhook": "POST /webhook"}


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    body = await request.body()
    signature_256 = request.headers.get(SIGNATURE_HEADER)
    event_name = request.headers.get(EVENT_HEADER, "")
    client_host = request.client.host if request.client else ""

    if not SECRET:
        logger.error("GITHUB_WEBHOOK_SECRET 未配置")
        return JSONResponse(status_code=500, content={"error": "server misconfiguration"})

    if not verify_signature(body, signature_256, SECRET):
        logger.warning("Webhook 签名校验失败 client=%s", client_host)
        return JSONResponse(status_code=401, content={"error": "invalid signature"})

    try:
        payload = parse_payload(body)
    except Exception as e:
        logger.warning("解析 payload 失败: %s", e)
        return JSONResponse(status_code=400, content={"error": "invalid payload"})

    logger.info(
        "Webhook 校验通过 event=%s repo=%s branch=%s client=%s",
        event_name,
        payload.get("repo"),
        payload.get("branch"),
        client_host,
    )

    ok = await send_to_internal(event_name, payload)
    if not ok:
        return JSONResponse(
            status_code=502,
            content={"error": "internal relay failed", "event": event_name},
        )
    return JSONResponse(status_code=200, content={"ok": True, "event": event_name})
