#!/usr/bin/env python3
"""
向 NasWebhookServer 的 /webhook 发送测试请求，用于验证签名与转发。
用法：
  export GITHUB_WEBHOOK_SECRET=你的密钥   # 或写在 .env 中
  python test_webhook.py
  python test_webhook.py --url http://gitwebhook.iepose.cn/webhook --event pull_request
"""
import argparse
import hashlib
import hmac
import json
import os
import sys

try:
    import httpx
except ImportError:
    print("需要 httpx：pip install httpx", file=sys.stderr)
    sys.exit(1)

# 可选：从 .env 加载
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# 示例：push 事件
SAMPLE_PUSH = {
    "ref": "refs/heads/main",
    "repository": {"full_name": "owner/repo", "name": "repo"},
    "head_commit": {
        "id": "abc123def456",
        "message": "test: webhook",
        "sha": "abc123def456",
    },
    "after": "abc123def456",
}

# 示例：pull_request 事件（给内网 Code Review 用）
SAMPLE_PULL_REQUEST = {
    "action": "opened",
    "repository": {"full_name": "owner/repo", "name": "repo"},
    "pull_request": {
        "number": 1,
        "head": {"sha": "head123abc", "ref": "feature-branch"},
        "base": {"sha": "base456def"},
    },
}

EVENTS = {
    "push": ("push", SAMPLE_PUSH),
    "pull_request": ("pull_request", SAMPLE_PULL_REQUEST),
}


def main():
    _load_dotenv()
    parser = argparse.ArgumentParser(description="发送 Webhook 测试请求")
    parser.add_argument(
        "--url",
        default="http://gitwebhook.iepose.cn/webhook",
        help="Webhook 地址",
    )
    parser.add_argument(
        "--event",
        default="push",
        choices=list(EVENTS),
        help="事件类型",
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get("GITHUB_WEBHOOK_SECRET"),
        help="GITHUB_WEBHOOK_SECRET（默认从环境或 .env 读取）",
    )
    args = parser.parse_args()

    secret = args.secret
    if not secret:
        print("未设置 GITHUB_WEBHOOK_SECRET，请用 --secret 或环境变量或 .env", file=sys.stderr)
        sys.exit(1)

    event_name, payload = EVENTS[args.event]
    body = json.dumps(payload).encode("utf-8")
    signature = _sign(body, secret)

    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature,
        "X-GitHub-Event": event_name,
    }

    print(f"POST {args.url}  event={event_name}")
    resp = httpx.post(args.url, content=body, headers=headers, timeout=30.0)
    print(f"状态: {resp.status_code}")
    print(resp.text[:500] if resp.text else "(无 body)")
    sys.exit(0 if resp.is_success else 1)


if __name__ == "__main__":
    main()
