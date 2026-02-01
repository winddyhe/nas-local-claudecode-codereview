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
# 本地仓库：指定后不再克隆，直接在该目录执行 code review
# LOCAL_REPO_PATH= 本地仓库绝对路径（如 D:/repos/my-project）
# LOCAL_REPO_NAME= 可选，与 webhook 的 repo 匹配时才用本地仓库（如 owner_repo 或 owner/repo）
LOCAL_REPO_PATH = os.environ.get("LOCAL_REPO_PATH", "").strip()
LOCAL_REPO_NAME = os.environ.get("LOCAL_REPO_NAME", "").strip()
CLAUDE_CLI = os.environ.get("CLAUDE_CLI", "claude")
# 是否在 slash 命令后附加自然语言提示（1/true 启用，0/false 则仅用下方 slash 命令）
CLAUDE_USE_NATURAL_PROMPT = os.environ.get("CLAUDE_USE_NATURAL_PROMPT", "1").strip().lower() in ("1", "true", "yes")
# 审核一律使用的 slash 命令（自然语言模式会在此命令后附加提示词）
CLAUDE_CODE_REVIEW_CMD = os.environ.get("CLAUDE_CODE_REVIEW_CMD", "/code-review:code-review")
# Claude Code 启动目录：若 code-review 技能在子目录（如 knight-client），填该目录绝对路径；
# 此时 LOCAL_REPO_PATH 仍为 git 根目录，仅执行 claude 时切到此目录
CLAUDE_WORKING_DIR = os.environ.get("CLAUDE_WORKING_DIR", "").strip()
# 克隆模式下 Claude 工作子目录（相对 clone_dir），如 knight-client
CLAUDE_SUBDIR = os.environ.get("CLAUDE_SUBDIR", "").strip()
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


# 自然语言 code review 提示词模板（占位符：repo, pr_number, head_sha, base_sha）
# 要求：做 PR 代码评审；若本次未产生任何 PR 评论，则必须发一条总结评论表示已自动评审
_DEFAULT_CODE_REVIEW_PROMPT = """你正在对本 PR 做自动代码评审。当前仓库为 {repo}，PR 编号为 {pr_number}，head_sha={head_sha}，base_sha={base_sha}。

请按以下步骤执行（可使用 gh、Bash、Read 等工具）：
1. 使用 gh pr diff 等获取本 PR 的变更内容，进行代码评审。
2. 若发现需要反馈的问题，请在对应位置发表 inline 评论或总结评论（通过 gh api 或 gh pr review 等）。
3. **若本次评审没有发现需要反馈的问题、因而没有发表任何 PR 评论**，则你必须至少发表一条总结评论到本 PR，内容表示“已自动评审过”，例如：
   - 「已自动评审，本次未发现需反馈的问题。」
   - 或英文："Automatic review completed; no issues to report this time."

即：本次执行结束时，本 PR 上必须有至少一条由你发表的评论，以表示已经自动评审过了。"""
CODE_REVIEW_PROMPT_TEMPLATE = os.environ.get(
    "CODE_REVIEW_PROMPT_TEMPLATE", _DEFAULT_CODE_REVIEW_PROMPT
)


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

        # gh repo clone owner/repo <dir>（Windows 下用 utf-8 解码输出，避免 cp950 报错）
        r = subprocess.run(
            ["gh", "repo", "clone", repo_full_name, str(clone_dir)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
            encoding="utf-8",
            errors="replace",
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
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            subprocess.run(
                ["git", "checkout", head_sha],
                cwd=str(clone_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )

        return True
    except Exception as e:
        logger.exception("克隆/checkout 失败: %s", e)
        return False


def _run_claude_code_review_in_dir(
    repo_dir: Path,
    repo_full_name: str | None = None,
    pr_number: int | None = None,
    head_sha: str = "",
    base_sha: str = "",
) -> bool:
    """
    在指定仓库目录中执行 Claude Code：一律使用 /code-review:code-review 命令进行审核。
    若 CLAUDE_USE_NATURAL_PROMPT 且提供了 repo_full_name、pr_number，则在命令后附加自然语言提示词。
    """
    if not repo_dir.is_dir():
        logger.error("仓库目录不存在或不是目录: %s", repo_dir)
        return False

    env = os.environ.copy()
    if ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    if GH_TOKEN:
        env["GH_TOKEN"] = GH_TOKEN

    use_natural = CLAUDE_USE_NATURAL_PROMPT and repo_full_name is not None and pr_number is not None
    if use_natural:
        extra_prompt = CODE_REVIEW_PROMPT_TEMPLATE.format(
            repo=repo_full_name,
            pr_number=pr_number,
            head_sha=head_sha,
            base_sha=base_sha,
        )
        # 先发 slash 命令，再附上自然语言说明
        prompt = CLAUDE_CODE_REVIEW_CMD + "\n\n" + extra_prompt
        cmd = [CLAUDE_CLI, "-p", prompt]
        logger.info(
            "[claude] 即将执行（/code-review:code-review + 自然语言） cwd=%s repo=%s pr=%s prompt_len=%s",
            repo_dir,
            repo_full_name,
            pr_number,
            len(prompt),
        )
    else:
        cmd = [CLAUDE_CLI, "-p", CLAUDE_CODE_REVIEW_CMD]
        logger.info(
            "[claude] 即将执行（slash 命令） cwd=%s cmd=%s",
            repo_dir,
            CLAUDE_CODE_REVIEW_CMD,
        )
    try:
        r = subprocess.run(
            cmd,
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLAUDE_REVIEW_TIMEOUT,
        )
        logger.info(
            "[claude] 进程结束 returncode=%s stdout_len=%s stderr_len=%s",
            r.returncode,
            len(r.stdout or ""),
            len(r.stderr or ""),
        )
        if r.stdout:
            logger.info("[claude] stdout 完整输出:\n%s", r.stdout)
        if r.stderr:
            logger.warning("[claude] stderr 完整输出:\n%s", r.stderr)
        if r.returncode != 0:
            logger.warning(
                "[claude] 退出码非 0 returncode=%s",
                r.returncode,
            )
        else:
            logger.info("[claude] code review 执行完成 cwd=%s", repo_dir)
        return r.returncode == 0
    except subprocess.TimeoutExpired as e:
        logger.error("[claude] 超时 cwd=%s timeout=%s", repo_dir, CLAUDE_REVIEW_TIMEOUT, exc_info=True)
        return False
    except Exception as e:
        logger.exception("[claude] 执行异常 cwd=%s error=%s", repo_dir, e)
        return False


def _run_claude_code_review(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    work_dir: Path,
) -> bool:
    """
    在已 clone 的仓库目录中启动 Claude Code 终端，执行 /code-review:code-review。
    """
    clone_dir = work_dir / repo_full_name.replace("/", "_")
    if not clone_dir.exists():
        logger.error("仓库目录不存在: %s", clone_dir)
        return False
    return _run_claude_code_review_in_dir(
        clone_dir,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        head_sha=head_sha,
        base_sha=base_sha,
    )


def _run_code_review_sync(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
) -> None:
    """
    同步执行：若配置了 LOCAL_REPO_PATH 且匹配则直接用；否则克隆后在 Claude Code 终端执行。
    """
    logger.info(
        "[code_review_sync] 开始 repo=%s pr=%s head_sha=%s base_sha=%s",
        repo_full_name,
        pr_number,
        head_sha[:7],
        base_sha[:7],
    )

    repo_dir_local = Path(LOCAL_REPO_PATH).resolve() if LOCAL_REPO_PATH else None
    if not LOCAL_REPO_PATH:
        logger.info(
            "[code_review_sync] LOCAL_REPO_PATH 未设置，走克隆 repo=%s",
            repo_full_name,
        )
        repo_dir_local = None
    elif not repo_dir_local or not repo_dir_local.is_dir():
        logger.warning(
            "[code_review_sync] LOCAL_REPO_PATH 目录不存在或不可用 path=%s，走克隆 repo=%s",
            LOCAL_REPO_PATH,
            repo_full_name,
        )
        repo_dir_local = None
    if repo_dir_local and repo_dir_local.is_dir():
        # 若设置了 LOCAL_REPO_NAME，仅当 webhook 的 repo 与之匹配时才用本地仓库
        name_normalized = repo_full_name.replace("/", "_")
        if LOCAL_REPO_NAME:
            if name_normalized != LOCAL_REPO_NAME and repo_full_name != LOCAL_REPO_NAME:
                logger.info(
                    "[code_review_sync] 本地仓库名不匹配 repo=%s local_name=%s，走克隆",
                    repo_full_name,
                    LOCAL_REPO_NAME,
                )
                repo_dir_local = None
        if repo_dir_local:
            logger.info(
                "[code_review_sync] 使用本地仓库 path=%s repo=%s",
                repo_dir_local,
                repo_full_name,
            )
            # Claude 启动目录：优先 CLAUDE_WORKING_DIR，否则 repo 根（或 repo/CLAUDE_SUBDIR）
            if CLAUDE_WORKING_DIR and Path(CLAUDE_WORKING_DIR).is_dir():
                claude_cwd = Path(CLAUDE_WORKING_DIR).resolve()
                logger.info("[code_review_sync] Claude 工作目录 CLAUDE_WORKING_DIR=%s", claude_cwd)
            elif CLAUDE_SUBDIR:
                claude_cwd = (repo_dir_local / CLAUDE_SUBDIR).resolve()
                if not claude_cwd.is_dir():
                    logger.error("[code_review_sync] CLAUDE_SUBDIR 目录不存在: %s", claude_cwd)
                    return
                logger.info("[code_review_sync] Claude 工作子目录 cwd=%s", claude_cwd)
            else:
                claude_cwd = repo_dir_local
            ok = _run_claude_code_review_in_dir(
                claude_cwd,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
            )
            logger.info("[code_review_sync] 结束 repo=%s 使用本地仓库 ok=%s", repo_full_name, ok)
            return

    logger.info("[code_review_sync] 开始克隆 repo=%s work_dir=%s", repo_full_name, REPO_ROOT)
    work_dir = Path(REPO_ROOT)
    work_dir.mkdir(parents=True, exist_ok=True)
    if not _clone_and_checkout(repo_full_name, head_sha, work_dir):
        logger.error("[code_review_sync] 克隆失败，跳过 code review repo=%s", repo_full_name)
        return
    clone_dir = work_dir / repo_full_name.replace("/", "_")
    logger.info("[code_review_sync] 克隆成功 clone_dir=%s", clone_dir)

    # 克隆模式下也可指定 Claude 工作子目录（如 monorepo 下的 knight-client）
    if CLAUDE_SUBDIR:
        claude_dir = (clone_dir / CLAUDE_SUBDIR).resolve()
        if claude_dir.is_dir():
            logger.info("[code_review_sync] Claude 工作子目录 cwd=%s", claude_dir)
            ok = _run_claude_code_review_in_dir(
                claude_dir,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
            )
        else:
            logger.warning("[code_review_sync] CLAUDE_SUBDIR 不存在: %s，使用 clone_dir", claude_dir)
            ok = _run_claude_code_review(
                repo_full_name, pr_number, head_sha, base_sha, work_dir
            )
    else:
        ok = _run_claude_code_review(
            repo_full_name, pr_number, head_sha, base_sha, work_dir
        )
    logger.info("[code_review_sync] 结束 repo=%s pr=%s ok=%s", repo_full_name, pr_number, ok)


async def run_code_review_async(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
) -> None:
    """异步执行 code review（在线程池中：克隆 + Claude Code 终端 /code-review）。"""
    logger.info(
        "[run_code_review_async] 进入后台任务 repo=%s pr=%s head_sha=%s",
        repo_full_name,
        pr_number,
        head_sha[:7],
    )
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _run_code_review_sync,
        repo_full_name,
        pr_number,
        head_sha,
        base_sha,
    )
