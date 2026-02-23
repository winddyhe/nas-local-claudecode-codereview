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
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ===== 配置变量 =====
REPO_ROOT = os.environ.get("REPO_ROOT", tempfile.gettempdir())
LOCAL_REPO_PATH = os.environ.get("LOCAL_REPO_PATH", "").strip()
LOCAL_REPO_NAME = os.environ.get("LOCAL_REPO_NAME", "").strip()
CLAUDE_CLI = os.environ.get("CLAUDE_CLI", "claude")
CLAUDE_USE_NATURAL_PROMPT = os.environ.get("CLAUDE_USE_NATURAL_PROMPT", "1").strip().lower() in ("1", "true", "yes")
CLAUDE_CODE_REVIEW_CMD = os.environ.get("CLAUDE_CODE_REVIEW_CMD", "/code-review:code-review")
CLAUDE_WORKING_DIR = os.environ.get("CLAUDE_WORKING_DIR", "").strip()
CLAUDE_SUBDIR = os.environ.get("CLAUDE_SUBDIR", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
CLAUDE_REVIEW_TIMEOUT = int(os.environ.get("CLAUDE_REVIEW_TIMEOUT", "600"))

# 记录配置加载情况
def _log_config():
    """记录配置信息"""
    logger.info("=" * 60)
    logger.info("[config] 配置信息:")
    logger.info("[config]   CLAUDE_CLI: %s", CLAUDE_CLI)
    logger.info("[config]   CLAUDE_USE_NATURAL_PROMPT: %s", CLAUDE_USE_NATURAL_PROMPT)
    logger.info("[config]   CLAUDE_CODE_REVIEW_CMD: %s", CLAUDE_CODE_REVIEW_CMD)
    logger.info("[config]   CLAUDE_REVIEW_TIMEOUT: %s 秒", CLAUDE_REVIEW_TIMEOUT)
    logger.info("[config]   LOCAL_REPO_PATH: %s", LOCAL_REPO_PATH or "(未设置)")
    logger.info("[config]   LOCAL_REPO_NAME: %s", LOCAL_REPO_NAME or "(未设置)")
    logger.info("[config]   CLAUDE_WORKING_DIR: %s", CLAUDE_WORKING_DIR or "(未设置)")
    logger.info("[config]   CLAUDE_SUBDIR: %s", CLAUDE_SUBDIR or "(未设置)")
    logger.info("[config]   REPO_ROOT: %s", REPO_ROOT)
    logger.info("[config]   GH_TOKEN: %s", "已配置" if GH_TOKEN else "未配置")
    logger.info("[config]   ANTHROPIC_API_KEY: %s", "已配置" if ANTHROPIC_API_KEY else "未配置")
    logger.info("=" * 60)

# 启动时记录配置
_log_config()


def get_pr_info(payload: dict[str, Any]) -> tuple[str, int, str, str, str, str] | None:
    """
    从 GitHub Webhook payload 中解析 PR 信息。
    返回 (repo_full_name, pr_number, head_sha, base_sha, head_ref, base_ref) 或 None。
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
    head_ref = head.get("ref") or ""  # PR 的 head 分支名
    base_ref = base.get("ref") or ""  # PR 的 base 分支名
    if not repo_full_name or pr_number is None or not head_sha:
        return None
    return (repo_full_name, int(pr_number), head_sha, base_sha, head_ref, base_ref)


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


def _fetch_and_checkout(repo_dir: Path, head_sha: str, head_ref: str = "") -> bool:
    """
    在本地仓库中拉取最新代码并切换到 PR 的 head SHA。
    1. git fetch origin --prune 获取最新远程分支
    2. git checkout head_sha 切换到 PR 的 head commit
    """
    start_time = time.time()
    logger.info("[git] 开始拉取最新代码...")

    env = os.environ.copy()
    if GH_TOKEN:
        env["GH_TOKEN"] = GH_TOKEN

    try:
        # 1. git fetch origin --prune
        logger.info("[git] 执行: git fetch origin --prune")
        r1 = subprocess.run(
            ["git", "fetch", "origin", "--prune"],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if r1.returncode != 0:
            logger.warning("[git] git fetch 警告: %s", r1.stderr)
        else:
            logger.info("[git] git fetch 完成")

        # 2. 尝试直接 checkout 到 head_sha
        logger.info("[git] 切换到 PR head: %s%s", head_sha[:7], f" (分支: {head_ref})" if head_ref else "")
        r2 = subprocess.run(
            ["git", "checkout", head_sha],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

        if r2.returncode != 0:
            # 如果 SHA 不存在，尝试 fetch 该 ref
            logger.info("[git] SHA 不存在本地，尝试 fetch: %s", head_ref or head_sha[:7])
            if head_ref:
                r3 = subprocess.run(
                    ["git", "fetch", "origin", f"{head_ref}:{head_ref}"],
                    cwd=str(repo_dir),
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                logger.info("[git] git fetch origin %s: returncode=%s", head_ref, r3.returncode)

            # 再次尝试 fetch 该 SHA
            r4 = subprocess.run(
                ["git", "fetch", "origin", head_sha],
                cwd=str(repo_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            logger.info("[git] git fetch origin %s: returncode=%s", head_sha[:7], r4.returncode)

            # 重试 checkout
            r5 = subprocess.run(
                ["git", "checkout", head_sha],
                cwd=str(repo_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if r5.returncode != 0:
                logger.error("[git] checkout 失败: %s", r5.stderr)
                return False

        # 验证当前 HEAD
        r6 = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        current_head = r6.stdout.strip()[:7] if r6.returncode == 0 else "(unknown)"
        elapsed = time.time() - start_time
        logger.info("[git] 已切换到 %s (目标: %s)，耗时 %.1f 秒", current_head, head_sha[:7], elapsed)

        return True

    except subprocess.TimeoutExpired:
        logger.error("[git] 操作超时")
        return False
    except Exception as e:
        logger.exception("[git] 异常: %s", e)
        return False


def _clone_and_checkout(repo_full_name: str, head_sha: str, work_dir: Path) -> bool:
    """克隆仓库并 checkout 到 head_sha。使用 gh repo clone + git checkout。"""
    start_time = time.time()
    clone_dir = work_dir / repo_full_name.replace("/", "_")

    logger.info("[clone] 开始克隆 repo=%s -> %s", repo_full_name, clone_dir)

    try:
        if clone_dir.exists():
            logger.info("[clone] 删除已存在的目录: %s", clone_dir)
            shutil.rmtree(clone_dir, ignore_errors=True)
        clone_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        if GH_TOKEN:
            env["GH_TOKEN"] = GH_TOKEN

        # gh repo clone owner/repo <dir>（Windows 下用 utf-8 解码输出，避免 cp950 报错）
        logger.info("[clone] 执行: gh repo clone %s %s", repo_full_name, clone_dir)
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
            logger.error("[clone] gh repo clone 失败: returncode=%s stderr=%s stdout=%s", r.returncode, r.stderr, r.stdout)
            return False

        elapsed = time.time() - start_time
        logger.info("[clone] 克隆完成，耗时 %.1f 秒", elapsed)

        # git checkout head_sha
        logger.info("[clone] 切换到 SHA: %s", head_sha[:7])
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
            logger.warning("[clone] git checkout %s 失败，尝试 fetch: %s", head_sha[:7], r2.stderr)
            # 尝试 fetch 后再 checkout
            r3 = subprocess.run(
                ["git", "fetch", "origin", head_sha],
                cwd=str(clone_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            logger.info("[clone] git fetch 结果: returncode=%s", r3.returncode)
            r4 = subprocess.run(
                ["git", "checkout", head_sha],
                cwd=str(clone_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if r4.returncode != 0:
                logger.error("[clone] git checkout 最终失败: %s", r4.stderr)
                return False

        total_elapsed = time.time() - start_time
        logger.info("[clone] 完成，总耗时 %.1f 秒，目录: %s", total_elapsed, clone_dir)
        return True
    except subprocess.TimeoutExpired:
        logger.error("[clone] 超时 repo=%s", repo_full_name)
        return False
    except Exception as e:
        logger.exception("[clone] 异常: %s", e)
        return False


def _run_claude_code_review_in_dir(
    repo_dir: Path,
    repo_full_name: str | None = None,
    pr_number: int | None = None,
    head_sha: str = "",
    base_sha: str = "",
    pr_title: str = "",
    pr_author: str = "",
) -> bool:
    """
    在指定仓库目录中执行 Claude Code：一律使用 /code-review:code-review 命令进行审核。
    若 CLAUDE_USE_NATURAL_PROMPT 且提供了 repo_full_name、pr_number，则在命令后附加自然语言提示词。
    """
    start_time = time.time()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    logger.info("=" * 60)
    logger.info("[claude] %s - 开始执行代码审查", timestamp)
    logger.info("[claude] 仓库: %s", repo_full_name or "(未知)")
    logger.info("[claude] PR: #%s - '%s'", pr_number, pr_title[:50] if pr_title else "(无标题)")
    logger.info("[claude] 作者: %s", pr_author or "(未知)")
    logger.info("[claude] HEAD: %s", head_sha[:7] if head_sha else "(未知)")
    logger.info("[claude] BASE: %s", base_sha[:7] if base_sha else "(未知)")
    logger.info("[claude] 工作目录: %s", repo_dir)
    logger.info("=" * 60)

    if not repo_dir.is_dir():
        logger.error("[claude] 仓库目录不存在或不是目录: %s", repo_dir)
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
        logger.info("[claude] 执行模式: slash 命令 + 自然语言提示")
        logger.info("[claude] 命令: %s -p '<prompt len=%d>'", CLAUDE_CLI, len(prompt))
    else:
        cmd = [CLAUDE_CLI, "-p", CLAUDE_CODE_REVIEW_CMD]
        logger.info("[claude] 执行模式: 仅 slash 命令")
        logger.info("[claude] 命令: %s -p '%s'", CLAUDE_CLI, CLAUDE_CODE_REVIEW_CMD)

    logger.info("[claude] 超时设置: %d 秒", CLAUDE_REVIEW_TIMEOUT)
    logger.info("[claude] 开始执行...")

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

        elapsed = time.time() - start_time
        logger.info("-" * 60)
        logger.info("[claude] 执行完成")
        logger.info("[claude] 返回码: %d", r.returncode)
        logger.info("[claude] 执行耗时: %.1f 秒", elapsed)

        if r.stdout:
            stdout_preview = r.stdout[:500] + "..." if len(r.stdout) > 500 else r.stdout
            logger.info("[claude] 输出长度: %d 字符", len(r.stdout))
            logger.info("[claude] 输出预览:\n%s", stdout_preview)
        if r.stderr:
            logger.warning("[claude] 错误输出: %s", r.stderr[:500])

        if r.returncode != 0:
            logger.warning("[claude] 执行失败，返回码非 0")
        else:
            logger.info("[claude] 执行成功 ✓")

        logger.info("=" * 60)
        return r.returncode == 0

    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - start_time
        logger.error("[claude] 执行超时！已运行 %.1f 秒（超时设置: %d 秒）", elapsed, CLAUDE_REVIEW_TIMEOUT)
        logger.error("[claude] PR #%s 代码审查超时", pr_number)
        return False
    except Exception as e:
        elapsed = time.time() - start_time
        logger.exception("[claude] 执行异常（已运行 %.1f 秒）: %s", elapsed, e)
        return False


def _run_claude_code_review(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    work_dir: Path,
    pr_title: str = "",
    pr_author: str = "",
) -> bool:
    """
    在已 clone 的仓库目录中启动 Claude Code 终端，执行 /code-review:code-review。
    """
    clone_dir = work_dir / repo_full_name.replace("/", "_")
    if not clone_dir.exists():
        logger.error("[review] 仓库目录不存在: %s", clone_dir)
        return False
    return _run_claude_code_review_in_dir(
        clone_dir,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        head_sha=head_sha,
        base_sha=base_sha,
        pr_title=pr_title,
        pr_author=pr_author,
    )


def _run_code_review_sync(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    pr_title: str = "",
    pr_author: str = "",
    head_ref: str = "",
    base_ref: str = "",
) -> None:
    """
    同步执行：若配置了 LOCAL_REPO_PATH 且匹配则直接用；否则克隆后在 Claude Code 终端执行。
    """
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("[review] 开始代码审查任务")
    logger.info("[review] 仓库: %s", repo_full_name)
    logger.info("[review] PR: #%s", pr_number)
    logger.info("[review] 标题: %s", pr_title[:50] if pr_title else "(无)")
    logger.info("[review] 作者: %s", pr_author or "(未知)")
    logger.info("[review] HEAD: %s (%s)", head_sha[:7], head_ref or "detached")
    logger.info("[review] BASE: %s (%s)", base_sha[:7], base_ref or "unknown")
    logger.info("=" * 60)

    repo_dir_local = Path(LOCAL_REPO_PATH).resolve() if LOCAL_REPO_PATH else None
    if not LOCAL_REPO_PATH:
        logger.info("[review] LOCAL_REPO_PATH 未设置，将克隆仓库")
        repo_dir_local = None
    elif not repo_dir_local or not repo_dir_local.is_dir():
        logger.warning("[review] LOCAL_REPO_PATH 目录不存在: %s，将克隆仓库", LOCAL_REPO_PATH)
        repo_dir_local = None

    if repo_dir_local and repo_dir_local.is_dir():
        # 若设置了 LOCAL_REPO_NAME，仅当 webhook 的 repo 与之匹配时才用本地仓库
        name_normalized = repo_full_name.replace("/", "_")
        if LOCAL_REPO_NAME:
            if name_normalized != LOCAL_REPO_NAME and repo_full_name != LOCAL_REPO_NAME:
                logger.info("[review] 仓库名不匹配 (webhook=%s, config=%s)，将克隆仓库",
                           repo_full_name, LOCAL_REPO_NAME)
                repo_dir_local = None

        if repo_dir_local:
            logger.info("[review] 使用本地仓库: %s", repo_dir_local)

            # ★ 拉取最新代码并切换到 PR head
            if not _fetch_and_checkout(repo_dir_local, head_sha, head_ref):
                logger.error("[review] 拉取代码失败，跳过代码审查")
                return

            # Claude 启动目录：优先 CLAUDE_WORKING_DIR，否则 repo 根（或 repo/CLAUDE_SUBDIR）
            if CLAUDE_WORKING_DIR and Path(CLAUDE_WORKING_DIR).is_dir():
                claude_cwd = Path(CLAUDE_WORKING_DIR).resolve()
                logger.info("[review] Claude 工作目录 (CLAUDE_WORKING_DIR): %s", claude_cwd)
            elif CLAUDE_SUBDIR:
                claude_cwd = (repo_dir_local / CLAUDE_SUBDIR).resolve()
                if not claude_cwd.is_dir():
                    logger.error("[review] CLAUDE_SUBDIR 目录不存在: %s", claude_cwd)
                    return
                logger.info("[review] Claude 工作目录 (CLAUDE_SUBDIR): %s", claude_cwd)
            else:
                claude_cwd = repo_dir_local
                logger.info("[review] Claude 工作目录 (仓库根): %s", claude_cwd)

            ok = _run_claude_code_review_in_dir(
                claude_cwd,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
                pr_title=pr_title,
                pr_author=pr_author,
            )
            elapsed = time.time() - start_time
            logger.info("[review] 完成，总耗时: %.1f 秒，结果: %s", elapsed, "成功" if ok else "失败")
            return

    # 克隆模式
    logger.info("[review] 克隆模式，目标目录: %s", REPO_ROOT)
    work_dir = Path(REPO_ROOT)
    work_dir.mkdir(parents=True, exist_ok=True)

    if not _clone_and_checkout(repo_full_name, head_sha, work_dir):
        logger.error("[review] 克隆失败，跳过代码审查")
        return

    clone_dir = work_dir / repo_full_name.replace("/", "_")
    logger.info("[review] 克隆成功: %s", clone_dir)

    # 克隆模式下也可指定 Claude 工作子目录
    if CLAUDE_SUBDIR:
        claude_dir = (clone_dir / CLAUDE_SUBDIR).resolve()
        if claude_dir.is_dir():
            logger.info("[review] Claude 工作目录 (CLAUDE_SUBDIR): %s", claude_dir)
            ok = _run_claude_code_review_in_dir(
                claude_dir,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
                pr_title=pr_title,
                pr_author=pr_author,
            )
        else:
            logger.warning("[review] CLAUDE_SUBDIR 不存在: %s，使用克隆目录", claude_dir)
            ok = _run_claude_code_review(
                repo_full_name, pr_number, head_sha, base_sha, work_dir, pr_title, pr_author
            )
    else:
        ok = _run_claude_code_review(
            repo_full_name, pr_number, head_sha, base_sha, work_dir, pr_title, pr_author
        )

    elapsed = time.time() - start_time
    logger.info("[review] 完成，总耗时: %.1f 秒，结果: %s", elapsed, "成功" if ok else "失败")


async def run_code_review_async(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    pr_title: str = "",
    pr_author: str = "",
    head_ref: str = "",
    base_ref: str = "",
) -> None:
    """异步执行 code review（在线程池中：克隆 + Claude Code 终端 /code-review）。"""
    logger.info("[async] 提交后台任务: repo=%s pr=#%s head=%s", repo_full_name, pr_number, head_sha[:7])
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _run_code_review_sync,
        repo_full_name,
        pr_number,
        head_sha,
        base_sha,
        pr_title,
        pr_author,
        head_ref,
        base_ref,
    )
