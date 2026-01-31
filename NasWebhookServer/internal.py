"""
内网通信：通过 HTTP 调用内网 API（httpx）。
"""
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

INTERNAL_TARGET_URL = os.environ.get("INTERNAL_TARGET_URL", "").rstrip("/")
INTERNAL_TARGET_PATH = os.environ.get("INTERNAL_TARGET_PATH", "/webhook/trigger")
INTERNAL_TIMEOUT = float(os.environ.get("INTERNAL_TIMEOUT", "20"))
INTERNAL_RETRIES = int(os.environ.get("INTERNAL_RETRIES", "2"))


async def send_to_internal(event_type: str, payload: dict[str, Any]) -> bool:
    """
    向内网 API 发送 POST 请求（JSON body）。
    返回 True 表示 2xx 成功，否则 False 并记录日志。
    """
    if not INTERNAL_TARGET_URL:
        logger.warning("INTERNAL_TARGET_URL 未配置，跳过内网调用")
        return False

    url = f"{INTERNAL_TARGET_URL}{INTERNAL_TARGET_PATH}"
    body = {"event": event_type, **payload}

    for attempt in range(INTERNAL_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=INTERNAL_TIMEOUT) as client:
                resp = await client.post(url, json=body)
            if 200 <= resp.status_code < 300:
                logger.info("内网调用成功 url=%s status=%s", url, resp.status_code)
                return True
            logger.warning(
                "内网调用非 2xx url=%s status=%s body=%s",
                url,
                resp.status_code,
                resp.text[:500] if resp.text else "",
            )
            return False
        except Exception as e:
            logger.warning("内网调用异常 attempt=%s url=%s error=%s", attempt + 1, url, e)
            if attempt == INTERNAL_RETRIES:
                logger.error("内网调用最终失败 url=%s", url, exc_info=True)
                return False
    return False
