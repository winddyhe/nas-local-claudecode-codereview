# InternalCodeReviewServer

内网 Code Review 服务：接收 NasWebhookServer 转发的 GitHub Webhook，在 **pull_request** 事件时克隆仓库，并在 **Claude Code 终端**中执行 `/code-review:code-review`，由 Claude Code 自动完成 PR 代码审核（如发评论等）。

## 流程

1. NasWebhookServer 收到 GitHub Webhook（如 `pull_request`），校验后向本服务 `POST /webhook/trigger` 转发（JSON：event、repo、branch、commit、payload）。
2. 本服务解析 payload，若 `event == pull_request`，提取 repo、PR 号、head_sha、base_sha。
3. 立即返回 202 Accepted，在后台：
   - 使用 `gh repo clone <repo>` 克隆到 `REPO_ROOT` 下；
   - `git checkout <head_sha>`；
   - 在仓库目录下启动 **Claude Code 终端**，非交互执行：`claude -p "/code-review:code-review"`，由 Claude Code 的 code-review 技能完成 PR 审核（如使用 gh 发评论等）。

## 前置条件

- **本机已安装并可用**：
  - [Claude Code CLI](https://code.claude.com/docs)（`claude` 在 PATH 中，或通过 `CLAUDE_CLI` 指定）。
  - [GitHub CLI](https://cli.github.com/)（`gh`），已登录且有权访问目标仓库、发表 PR 评论。
- **环境变量**：
  - `GH_TOKEN`：必填，供 `gh` 克隆、拉 PR；Claude Code 内发评论也依赖 gh。
  - `ANTHROPIC_API_KEY`：可选。本机若已用 `claude` 登录过可不填；CI 或未登录环境需填。

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `GH_TOKEN` | 是 | GitHub Token（repo、pull_request 权限） |
| `ANTHROPIC_API_KEY` | 否 | Anthropic API Key；已用 claude 登录过可不填 |
| `CLAUDE_CLI` | 否 | Claude Code 可执行名或路径，默认 `claude` |
| `CLAUDE_CODE_REVIEW_CMD` | 否 | 在 Claude Code 终端中执行的 slash 命令，默认 `/code-review:code-review` |
| `REPO_ROOT` | 否 | 克隆仓库的根目录，默认系统临时目录 |
| `CLAUDE_REVIEW_TIMEOUT` | 否 | Claude Code 执行超时（秒），默认 600 |

## 本地运行

```bash
cd InternalCodeReviewServer
cp .env.example .env
# 编辑 .env 填入 GH_TOKEN（ANTHROPIC_API_KEY 可选，已登录 claude 可不填）
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8009
```

## Docker 运行

本服务需在**内网一台能执行 Claude Code CLI 和 gh 的机器**上运行。若 Claude Code 与 gh 已安装在宿主机，可挂载宿主机 PATH 或可执行文件，并在容器内设置 `GH_TOKEN`（及可选 `ANTHROPIC_API_KEY`）；否则建议直接在本机用 uvicorn 运行（不经过 Docker），以避免 CLI 安装与认证的复杂性。

## 与 NasWebhookServer 的对接

- NasWebhookServer 的 `INTERNAL_TARGET_URL` 指向本机地址，例如 `http://192.168.1.100:8009`。
- `INTERNAL_TARGET_PATH` 保持默认 `/webhook/trigger`，或与本服务路由一致。
- 本服务只处理 `event == pull_request`，其它事件返回 200 并忽略。

## Code Review 行为

- 通过 **Claude Code 终端** 执行 slash 命令 `/code-review:code-review`（可由 `CLAUDE_CODE_REVIEW_CMD` 覆盖）。
- Claude Code 的 code-review 技能在仓库目录下运行，可使用 gh、读写文件等工具完成 PR 审核并提交评论；具体行为由 Claude Code 的 code-review 技能定义。
