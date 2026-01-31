# NasWebhookServer

在 NAS 上运行的 GitHub Webhook 中继服务：接收 GitHub Webhook，校验签名后通过 HTTP 将事件转发到内网 API。

## 流程

1. GitHub 向 NAS 公网地址发送 `POST /webhook`（带 `X-Hub-Signature-256` 和 `X-GitHub-Event`）。
2. 本服务校验签名，解析 payload，提取 `repo`、`branch`、`commit` 等。
3. 本服务向配置的内网 URL 发送 `POST`（JSON body：`event`、`repo`、`branch`、`commit`、`payload` 等）。
4. 内网服务按需执行操作（如拉代码、部署）。

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `GITHUB_WEBHOOK_SECRET` | 是 | GitHub Webhook 的 Secret，用于校验签名 |
| `INTERNAL_TARGET_URL` | 是 | 内网 API 基础 URL，如 `http://192.168.1.100:8080` |
| `INTERNAL_TARGET_PATH` | 否 | 内网路径，默认 `/webhook/trigger` |
| `INTERNAL_TIMEOUT` | 否 | 内网请求超时（秒），默认 20 |
| `INTERNAL_RETRIES` | 否 | 内网请求失败重试次数，默认 2 |

## 本地运行

```bash
cd NasWebhookServer
cp .env.example .env
# 编辑 .env 填入 GITHUB_WEBHOOK_SECRET 和 INTERNAL_TARGET_URL
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Docker 构建与运行

```bash
cd NasWebhookServer
docker build -t nas-webhook-server .
docker run -d --name nas-webhook \
  -p 8000:8000 \
  -e GITHUB_WEBHOOK_SECRET=your_secret \
  -e INTERNAL_TARGET_URL=http://192.168.1.100:8080 \
  nas-webhook-server
```

或使用 docker-compose，将上述环境变量写在 `.env` 或 `environment` 中。

## 发布到 Docker Hub（或其它镜像仓库）

可以把自己的镜像推到 Docker Hub 等仓库，供他人或其它机器拉取。镜像名使用**你的用户名/镜像名**（Docker 官方的 `library/*` 由 Docker 维护，个人/项目用自己命名空间即可）。

**示例：推送到 Docker Hub**

```bash
cd NasWebhookServer
docker build -t your-dockerhub-username/nas-webhook-server:latest .
docker login
docker push your-dockerhub-username/nas-webhook-server:latest
```

他人或 NAS 上可直接拉取运行：

```bash
docker pull your-dockerhub-username/nas-webhook-server:latest
docker run -d -p 8000:8000 \
  -e GITHUB_WEBHOOK_SECRET=xxx \
  -e INTERNAL_TARGET_URL=http://192.168.x.x:8080 \
  your-dockerhub-username/nas-webhook-server:latest
```

若使用其它仓库（如 GitHub Container Registry、阿里云容器镜像等），把 `your-dockerhub-username` 换成对应仓库地址和命名空间即可。

## GitHub Webhook 配置

1. 仓库 → Settings → Webhooks → Add webhook。
2. **Payload URL**：你的 NAS 公网地址，如 `https://your-nas.com:8443/webhook`（需与 NAS 端口转发或反向代理一致）。
3. **Content type**：`application/json`。
4. **Secret**：与 `GITHUB_WEBHOOK_SECRET` 一致。
5. 选择需要触发的事件（如 push、workflow_run 等）。

## 内网 API 约定

内网服务需提供一个 HTTP 接口（默认路径 `/webhook/trigger`），接收 `POST`，body 为 JSON，例如：

```json
{
  "event": "push",
  "repo": "owner/repo",
  "branch": "main",
  "commit": "abc123...",
  "commit_message": "fix: ...",
  "payload": { ... }
}
```

根据 `event`、`repo`、`branch` 等执行相应逻辑（如拉取代码、重启服务等）。
