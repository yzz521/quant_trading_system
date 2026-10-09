#!/usr/bin/env bash
# GP助手 一键重启：调度器 + Streamlit 看板 + 实时盯盘（快轨）
#
# 用法:
#   ./deploy/restart.sh              重启调度器 + 看板 + 快轨
#   ./deploy/restart.sh scheduler    只重启调度器
#   ./deploy/restart.sh dashboard    只重启看板
#   ./deploy/restart.sh realtime     只重启实时盯盘（快轨）
#   ./deploy/restart.sh status       查看运行状态与最近日志
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.gp.stock-scheduler"
PORT=8502
# 日志统一放在项目内 results/。历史版本写成 "$(dirname "$ROOT")/results/..."，
# 于是 launchd 与 ctl.sh 各写一份，排障时看错文件会得出相反结论。
SCHED_LOG="$ROOT/results/scheduler.log"
SCHED_PID_FILE="$ROOT/results/scheduler.pid"
DASH_LOG="$ROOT/results/dashboard.log"
RT_LOG="$ROOT/results/realtime.log"
RT_PID_FILE="$ROOT/results/realtime.pid"

# 查快轨状态要用带 pandas/yaml 的解释器；ctl.py 自己挑，这里跟着优先 .venv。
# 注意：下面所有调 ctl.py 的地方都必须用 "${PY}" —— 用裸 python3 会选中
# 「版本够但没装依赖」的解释器，服务启动即 ModuleNotFoundError 并静默退出。
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

scheduler_state() {
  # 进程活着 ≠ 在发邮件。两级判据：①进程/launchd ②最近一次实际推送成功的时间。
  local pid last
  pid="$(cat "${SCHED_PID_FILE}" 2>/dev/null || true)"
  if [ -n "${pid:-}" ] && kill -0 "${pid}" 2>/dev/null; then
    say "调度器进程运行中 PID=${pid}（日志: ${SCHED_LOG}）"
  elif launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
    say "调度器由 launchd 托管（日志: ${SCHED_LOG}）"
  else
    warn "❌ 调度器未运行 —— 定时邮件不会发出"
    warn "   启动: ${PY} deploy/ctl.py scheduler start"
  fi
  last="$(grep '推送成功' "${SCHED_LOG}" 2>/dev/null | tail -1 || true)"
  if [ -n "${last:-}" ]; then
    say "最近一次推送成功: $(printf '%s' "${last}" | cut -d'|' -f1 | sed 's/ *$//')"
  fi
  return 0
}

restart_scheduler() {
  say "重启调度器 ..."
  local started=0
  # 优先 launchd（能开机自启）；未 bootstrap 时回退 ctl.py 的 nohup 方式。
  # 不设这个回退，「重启成功」就是假的 —— 症状正是「邮件突然没了」。
  if launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null; then
    started=1
  fi
  if [ "${started}" -eq 0 ]; then
    warn "launchd job ${LABEL} 不可用（未 bootstrap？），回退到 nohup 方式"
    (cd "${ROOT}" && "${PY}" deploy/ctl.py scheduler restart) || true
  fi
  sleep 2
  scheduler_state
}

restart_dashboard() {
  say "重启看板 (Streamlit :${PORT}) ..."
  (cd "${ROOT}" && "${PY}" deploy/ctl.py dashboard restart)
  sleep 3
  if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    say "看板已启动: http://localhost:${PORT} （日志: ${DASH_LOG}）"
  else
    warn "看板未监听 ${PORT}，请查看 ${DASH_LOG}"
  fi
}

restart_realtime() {
  say "重启实时盯盘 (快轨 / RealtimeWatcher) ..."
  (cd "${ROOT}" && "${PY}" deploy/ctl.py realtime restart)
  sleep 1
  local rt_pid
  rt_pid="$(cat "${RT_PID_FILE}" 2>/dev/null || true)"
  if [ -n "${rt_pid:-}" ] && kill -0 "${rt_pid}" 2>/dev/null; then
    say "快轨进程已启动 PID=${rt_pid}（日志: ${RT_LOG}）"
    show_realtime_state
  else
    warn "快轨未在运行，请查看 ${RT_LOG}"
  fi
}

show_realtime_state() {
  # 进程活着 ≠ 在盯票：真正的判据是心跳。这里直接调引擎的对外状态接口。
  "${PY}" "${ROOT}/examples/run_realtime.py" --status 2>/dev/null | sed -n '1,2p' || true
}

show_status() {
  # status 是诊断命令：任何一段查不到都得继续往下走，不能中途退出
  say "--- 调度器（定时邮件）---"
  scheduler_state
  say "--- 看板 ---"
  lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | tail -1 || warn "看板未运行"
  say "--- 实时盯盘（快轨）---"
  local rt_pid
  rt_pid="$(cat "${RT_PID_FILE}" 2>/dev/null || true)"
  if [ -n "${rt_pid:-}" ] && kill -0 "${rt_pid}" 2>/dev/null; then
    say "进程运行中 PID=${rt_pid}"
  else
    warn "进程未运行（快轨由 ./deploy/restart.sh realtime 拉起）"
  fi
  show_realtime_state
  say "--- 最近日志 ---"
  tail -n 4 "${SCHED_LOG}" 2>/dev/null || true
  tail -n 4 "${DASH_LOG}" 2>/dev/null || true
  tail -n 4 "${RT_LOG}" 2>/dev/null || true
}

case "${1:-all}" in
  scheduler) restart_scheduler ;;
  dashboard) restart_dashboard ;;
  realtime)  restart_realtime ;;
  status)    show_status ;;
  all|"")
    restart_scheduler
    restart_dashboard
    restart_realtime
    say ""
    say "=== 服务总览 ==="
    scheduler_state
    ;;
  *)
    echo "用法: $0 [scheduler|dashboard|realtime|status]"
    exit 1
    ;;
esac
