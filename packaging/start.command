#!/bin/bash
# GP助手 一键启动（macOS）：双击本文件即可。
cd "$(dirname "$0")" || exit 1

# 尝试解除下载隔离（Gatekeeper），失败不影响启动
xattr -dr com.apple.quarantine "$PWD" 2>/dev/null || true

PORT="${PORT:-8502}"
PY="$PWD/runtime/bin/python3"

if [ ! -x "$PY" ]; then
  echo "❌ 未找到运行时：$PY"
  echo "请确认解压完整（runtime/ 目录存在）。"
  read -r -p "按回车退出..."
  exit 1
fi

echo "启动 GP助手看板 → http://127.0.0.1:${PORT}（首次启动约需 10-30 秒）"
(sleep 3; open "http://127.0.0.1:${PORT}") &

"$PY" -m streamlit run "quant_trading_system/dashboard/首页.py" \
  --server.port "$PORT" --server.headless true

echo "看板已停止。"
