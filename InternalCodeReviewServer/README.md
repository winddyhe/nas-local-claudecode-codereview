# InternalCodeReviewServer

内网 Code Review 服务：接收 NasWebhookServer 转发的 GitHub Webhook，在 **pull_request** 事件时克隆仓库，并在 **Claude Code 终端**中执行 `/code-review:code-review`，由 Claude Code 自动完成 PR 代码审核（如发评论等）。

## 流程

1. NasWebhookServer 收到 GitHub Webhook（如 `pull_request`），校验后向本服务 `POST /webhook/trigger` 转发（JSON：event、repo、branch、commit、payload）。
2. 本服务解析 payload，若 `event == pull_request`，提取 repo、PR 号、head_sha、base_sha。
3. 立即返回 202 Accepted，在后台：
   - 若配置了 **LOCAL_REPO_PATH** 且（未设 LOCAL_REPO_NAME 或与 webhook 的 repo 匹配）：直接在该本地仓库目录执行 code review；
   - 否则使用 `gh repo clone <repo>` 克隆到 `REPO_ROOT` 下，`git checkout <head_sha>`；
   - 在仓库目录下启动 **Claude Code 终端**，**一律执行** `/code-review:code-review` 进行审核；默认在其后附加**自然语言提示**（repo、PR 号、评审要求及「若未产生任何 PR 评论则必须发一条总结评论」等）。设 `CLAUDE_USE_NATURAL_PROMPT=0` 则仅发 slash 命令、不附加提示。若 code-review 在子目录（如 `knight-client`），可配置 **CLAUDE_WORKING_DIR** 或 **CLAUDE_SUBDIR**。

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
| `CLAUDE_USE_NATURAL_PROMPT` | 否 | 是否在 slash 命令后附加自然语言提示（1/true 默认）；0/false 则仅发 slash 命令 |
| `CLAUDE_CODE_REVIEW_CMD` | 否 | 审核一律使用的 slash 命令，默认 `/code-review:code-review` |
| `REPO_ROOT` | 否 | 克隆仓库的根目录，默认系统临时目录 |
| `LOCAL_REPO_PATH` | 否 | 本地仓库绝对路径；指定后不克隆，直接在该目录执行 code review |
| `LOCAL_REPO_NAME` | 否 | 与 webhook 的 repo 匹配时才用本地仓库（如 `owner_repo` 或 `owner/repo`）；不设则任意 PR 都用 LOCAL_REPO_PATH |
| `CLAUDE_WORKING_DIR` | 否 | Claude Code 启动目录（绝对路径）。若 code-review 在子目录（如 `knight-client`），填该目录；LOCAL_REPO_PATH 仍为 git 根目录 |
| `CLAUDE_SUBDIR` | 否 | 克隆模式下 Claude 工作子目录（相对 clone_dir），如 `knight-client`；本地仓库模式下也可用，相对 LOCAL_REPO_PATH |
| `CLAUDE_REVIEW_TIMEOUT` | 否 | Claude Code 执行超时（秒），默认 600 |

## 本地测试：跑通 Claude Code code review

不经过 Webhook，在指定本地仓库目录下执行一次 code review，用于验证 Claude Code 流程：

```bash
cd InternalCodeReviewServer
cp .env.example .env
# 编辑 .env 至少填 GH_TOKEN
python test_code_review.py --repo-path D:/path/to/your/repo
# 或对当前目录：python test_code_review.py --repo-path .
```

需已安装 Claude Code CLI、gh，且仓库为 git 仓库（若要做 PR 评论需 gh 已登录并有权限）。

## 本地运行（Webhook 服务）

```bash
cd InternalCodeReviewServer
cp .env.example .env
# 编辑 .env 填入 GH_TOKEN（ANTHROPIC_API_KEY 可选，已登录 claude 可不填）
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8009
```

## 计划任务与无窗口启动（Windows）

- **start_server.bat**：启动服务并将输出写入 `logs\server_YYYYMMDD.log`。双击会显示命令行窗口；由计划任务在「系统启动时」调用则不显示窗口。
- **start_server_hidden.vbs**：无窗口启动（内部调用 start_server.bat）。适合放入「启动」文件夹或计划任务「用户登录时」运行。
- **install_scheduled_task.bat**：以**管理员身份运行**后，会创建计划任务 **InternalCodeReviewServer**，在**系统启动时**自动运行服务且不显示命令行窗口。日志在 `InternalCodeReviewServer\logs\` 下。
- **uninstall_scheduled_task.bat**：以管理员身份运行可删除上述计划任务。

**步骤**：

1. 确认本目录下已有 `.env` 且配置正确（如 `GH_TOKEN`、`LOCAL_REPO_PATH`、`CLAUDE_WORKING_DIR`）。
2. 右键 **install_scheduled_task.bat** →「以管理员身份运行」。
3. 重启电脑或在该任务上右键「运行」，服务会在后台启动，无命令行窗口。查看日志：`InternalCodeReviewServer\logs\server_YYYYMMDD.log`。

若计划任务未启动（如 SYSTEM 下找不到 Python），可在 **start_server.bat** 中把 `python` 改为 Python 的完整路径（如 `C:\Python312\python.exe`）。

## Docker 运行

本服务需在**内网一台能执行 Claude Code CLI 和 gh 的机器**上运行。若 Claude Code 与 gh 已安装在宿主机，可挂载宿主机 PATH 或可执行文件，并在容器内设置 `GH_TOKEN`（及可选 `ANTHROPIC_API_KEY`）；否则建议直接在本机用 uvicorn 运行（不经过 Docker），以避免 CLI 安装与认证的复杂性。

## 与 NasWebhookServer 的对接

- NasWebhookServer 的 `INTERNAL_TARGET_URL` 指向本机地址，例如 `http://192.168.1.100:8009`。
- `INTERNAL_TARGET_PATH` 保持默认 `/webhook/trigger`，或与本服务路由一致。
- 本服务只处理 `event == pull_request`，其它事件返回 200 并忽略。

## Code Review 行为

- **一律使用** slash 命令（`CLAUDE_CODE_REVIEW_CMD`，默认 `/code-review:code-review`）进行审核。
- **默认**（`CLAUDE_USE_NATURAL_PROMPT=1`）：在 slash 命令后附加**自然语言提示**，说明当前 repo、PR 号，要求做 PR 代码评审；并约定：**若本次未产生任何 PR 评论，则必须发表一条总结评论**（如「已自动评审，本次未发现需反馈的问题。」）。
- 设置 `CLAUDE_USE_NATURAL_PROMPT=0` 时，仅发送 slash 命令，不附加自然语言提示。
- Claude Code 在仓库目录（或 `CLAUDE_WORKING_DIR`）下运行，可使用 gh、Bash、Read 等工具完成评审并发表评论。
