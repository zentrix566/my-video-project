#!/usr/bin/env bash
# 一行命令跑「南明李定国」样例
# 用法：从 my-video-project 根目录运行  bash examples/run_li_dingguo.sh
#
# 默认走 epic 风格 · 8 场景 · 强调「两蹶名王 / 磨盘山血战 / 忠贞殉国」。
# 想仅估费用：加 --dry-run

set -euo pipefail

cd "$(dirname "$0")/.."

PY=".venv/Scripts/python.exe"
if [ ! -x "$PY" ]; then
  PY="python"
fi

"$PY" make_video.py \
  --topic "南明李定国" \
  --brief "重点：两蹶名王（孔有德、尼堪）、磨盘山血战、忠贞殉国" \
  --style epic \
  --scenes 8 \
  --workers 2 \
  "$@"
# 提示：默认会在 Step 0 / Step 1 后各停一次让你过文案，直接回车即继续；
# 想一路跑到底不停：把 -y 传进来，例如  bash examples/run_li_dingguo.sh -y
