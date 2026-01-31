#!/usr/bin/env python3
"""
在本地指定仓库目录下执行 Claude Code /code-review:code-review，用于验证流程。
不经过 Webhook，直接在当前仓库跑一遍 code review。

用法：
  cd InternalCodeReviewServer
  cp .env.example .env   # 编辑 .env 至少填 GH_TOKEN
  python test_code_review.py --repo-path D:/path/to/your/repo
  python test_code_review.py --repo-path .
"""
import argparse
import logging
import os
import sys
from pathlib import Path

# 加载 .env（当前目录）
def _load_dotenv():
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.is_file():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k.strip(), v)


def main():
    _load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="在本地仓库目录执行 Claude Code code review（测试用）"
    )
    parser.add_argument(
        "--repo-path",
        required=True,
        type=Path,
        help="本地仓库根目录（绝对或相对路径，如 . 表示当前目录）",
    )
    args = parser.parse_args()

    repo_dir = args.repo_path.resolve()
    if not repo_dir.is_dir():
        print(f"错误：目录不存在或不是目录: {repo_dir}", file=sys.stderr)
        sys.exit(1)

    # 可选：检查是否为 git 仓库
    if not (repo_dir / ".git").exists():
        print(f"警告：{repo_dir} 不是 git 仓库，claude 可能无法做 PR 相关操作", file=sys.stderr)

    from review_runner import _run_claude_code_review_in_dir

    print(f"在目录 {repo_dir} 下执行 Claude Code code review ...")
    ok = _run_claude_code_review_in_dir(repo_dir)
    print("完成" if ok else "失败")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
