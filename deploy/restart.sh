#!/usr/bin/env bash
# GP助手 一键重启：launchd 调度器 + Streamlit 看板
#
# 用法:
#   ./deploy/restart.sh              重启调度器 + 看板
#   ./deploy/restart.sh scheduler    只重启调度器（launchd）
#   ./deploy/restart.sh dashboard    只重启看板
#   ./deploy/restart.sh status       查看运行状态与最近日志
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.gp.stock-scheduler"
PORT=8502
SCHED_LOG="$(dirname "$ROOT")/results/scheduler.log"
DASH_LOG="$ROOT/results/dashboard.log"

say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

restart_scheduler() {
  say "重启调度器 (launchd: ${LABEL}) ..."
  launchctl kickstart -k "gui/$(id -u)/${LABEL}"
  sleep 2
  pid="$(launchctl list 2>/dev/null | awk -v l="${LABEL}" '$3==l {print $1}')"
  if [ -n "${pid:-}" ] && [ "${pid}" != "-" ]; then
    say "调度器已重启 PID=${pid}（日志: ${SCHED_LOG}）"
  else
    warn "未在 launchctl 里找到 ${LABEL}，试试: python3 deploy/ctl.py scheduler start"
  fi
}

restart_dashboard() {
  say "重启看板 (Streamlit :${PORT}) ..."
  (cd "${ROOT}" && python3 deploy/ctl.py dashboard restart)
  sleep 3
  if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    say "看板已启动: http://localhost:${PORT} （日志: ${DASH_LOG}）"
  else
    warn "看板未监听 ${PORT}，请查看 ${DASH_LOG}"
  fi
}

show_status() {
  say "--- 调度器 (launchd) ---"
  launchctl list 2>/dev/null | awk -v l="${LABEL}" '$3==l {print "PID="$1"  上次退出码="$2"  标签="$3}'
  say "--- 看板 ---"
  lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | tail -1 || warn "看板未运行"
  say "--- 最近日志 ---"
  tail -n 4 "${SCHED_LOG}" 2>/dev/null || true
  tail -n 4 "${DASH_LOG}" 2>/dev/null || true
}

case "${1:-all}" in
  scheduler) restart_scheduler ;;
  dashboard) restart_dashboard ;;
  status)    show_status ;;
  all|"")    restart_scheduler; restart_dashboard ;;
  *)
    echo "用法: $0 [scheduler|dashboard|status]"
    exit 1
    ;;
esac
