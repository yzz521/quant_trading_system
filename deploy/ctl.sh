#!/bin/bash
# 股票分析调度器 · 后台启停脚本 (nohup 方式)
# 用法: ./ctl.sh {start|stop|status|log|restart}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="$(dirname "$SCRIPT_DIR")"          # 项目根目录（quant_trading_system）
PY="${PYTHON_BIN:-python3}"             # 可用环境变量 PYTHON_BIN 指定解释器
SCRIPT="$DIR/examples/run_scheduler.py"
PIDFILE="$DIR/results/scheduler.pid"
LOG="$DIR/results/scheduler.log"

mkdir -p "$DIR/results"

is_running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "$1" in
    start)
        if is_running; then
            echo "调度器已在运行 PID=$(cat "$PIDFILE")"
            exit 0
        fi
        cd "$DIR" || exit 1
        nohup "$PY" "$SCRIPT" > "$LOG" 2>&1 &
        echo $! > "$PIDFILE"
        sleep 1
        if is_running; then
            echo "✅ 调度器已启动 PID=$(cat "$PIDFILE")"
            echo "   日志: $LOG"
            echo "   停止: ./ctl.sh stop"
        else
            echo "❌ 启动失败，查看日志: $LOG"
            rm -f "$PIDFILE"
        fi
        ;;
    stop)
        if is_running; then
            kill "$(cat "$PIDFILE")" 2>/dev/null
            sleep 1
            kill -9 "$(cat "$PIDFILE")" 2>/dev/null
            echo "✅ 调度器已停止"
        else
            echo "调度器未在运行"
        fi
        rm -f "$PIDFILE"
        ;;
    status)
        if is_running; then
            echo "✅ 运行中 PID=$(cat "$PIDFILE")"
        else
            echo "❌ 未运行"
        fi
        ;;
    log)
        tail -f "$LOG"
        ;;
    restart)
        "$0" stop
        sleep 1
        "$0" start
        ;;
    *)
        echo "用法: $0 {start|stop|status|log|restart}"
        echo
        echo "  start   后台启动调度器"
        echo "  stop    停止调度器"
        echo "  status  查看运行状态"
        echo "  log     实时查看日志 (Ctrl+C 退出)"
        echo "  restart 重启"
        ;;
esac
