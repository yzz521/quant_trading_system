#!/bin/bash
# 无需 Apple 开发者账号：去掉「来自互联网」隔离属性后打开。
# 若系统仍拦截本 .command：按住 Control 点本文件 → 打开。
cd "$(dirname "$0")" || exit 1
APP="GP助手.app"
if [ ! -d "$APP" ]; then
  echo "未找到 $APP，请确认 zip 已完整解压（.app 与本脚本在同一目录）。"
  read -r -p "按回车退出..."
  exit 1
fi
xattr -cr "$APP" 2>/dev/null || true
# 本机临时签名（不需要 Apple ID），减少「已损坏，无法打开」误报
codesign --force --deep --sign - "$APP" 2>/dev/null || true
open "$APP"
