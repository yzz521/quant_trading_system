#!/bin/bash
# 量化交易系统 · 后台服务管理脚本
#
# 守护进程由 deploy/spawn.py 拉起（start_new_session=True 脱离父进程组），
# 不用 `nohup ... &` —— 后者挡不住父 shell 退出时发给进程组的信号。
#
# 用法:
#   ./ctl.sh dashboard                          # 启动持仓管理页面 (简写)
#   ./ctl.sh dashboard start                    # 同上
#   ./ctl.sh dashboard stop|status|log|restart  # 管理持仓管理页面
#   ./ctl.sh scheduler                          # 启动分析推送调度器 (简写)
#   ./ctl.sh scheduler start                    # 同上
#   ./ctl.sh scheduler stop|status|log|restart  # 管理调度器
#   ./ctl.sh start|stop|status|log|restart      # 兼容旧用法: 操作调度器
#
# 说明: 可用环境变量 PYTHON_BIN 指定 Python 解释器；未指定时自动探测
#       (优先 >=3.10 的 python3，其次 ~/.workbuddy 环境)。
#       持仓管理页面端口默认 8502，可用环境变量 PORT 覆盖。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"
DIR="$(dirname "$SCRIPT_DIR")"          # 项目根目录
PORT="${PORT:-8502}"
mkdir -p "$DIR/results"

# ------------------------------------------------------------------ #
# Python 解释器自动探测（不硬编码具体用户路径，保证可移植/可推送）
# ------------------------------------------------------------------ #
detect_python() {
    if [ -n "${PYTHON_BIN:-}" ]; then
        echo "$PYTHON_BIN"
        return
    fi
    # 判断解释器是否真能跑本项目。只看版本号会选中「>=3.10 但没装 pandas」的
    # 另一个 python3（例如各种工具链自带的托管解释器），服务启动后立刻
    # ModuleNotFoundError: No module named 'pandas' 并静默退出。
    _usable() {
        case "$1" in
            */*) [ -x "$1" ] || return 1 ;;
            *)   command -v "$1" >/dev/null 2>&1 || return 1 ;;
        esac
        "$1" -c 'import sys, pandas; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' \
            >/dev/null 2>&1
    }
    # 1) 项目虚拟环境优先 —— 本项目依赖都装在这里
    if _usable "$DIR/.venv/bin/python"; then
        echo "$DIR/.venv/bin/python"
        return
    fi
    # 2) 其它候选兜底（$HOME 展开，不泄露具体用户名）
    for cand in python3 \
                "$HOME/.workbuddy/binaries/python/envs/default/bin/python"; do
        if _usable "$cand"; then
            echo "$cand"
            return
        fi
    done
    # 3) 都不行时原样返回，让启动日志把真实错误暴露出来
    echo "python3"
}
PY="$(detect_python)"

# ------------------------------------------------------------------ #
# 通用进程管理
# ------------------------------------------------------------------ #
manage() {
    local name="$1" pidfile="$2" logfile="$3" cmd="$4" start_cmd="$5"

    is_running() { [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; }

    case "$cmd" in
        start)
            if is_running; then
                echo "$name 已在运行 PID=$(cat "$pidfile")"
                return 0
            fi
            cd "$DIR" || exit 1
            # 用 spawn.py 而不是 `nohup ... &`：nohup 只挡 SIGHUP，挡不住
            # 「父 shell 退出时发给整个进程组的信号」，守护进程会被连带杀掉
            # （实测：启动日志正常、随后进程消失、exit code 137）。
            # spawn.py 用 start_new_session=True 彻底脱离进程组。
            # $start_cmd 故意不加引号以按空格分词（项目路径不含空格）。
            # shellcheck disable=SC2086
            "$PY" "$SCRIPT_DIR/spawn.py" "$pidfile" "$logfile" $start_cmd
            sleep 2
            if is_running; then
                echo "✅ $name 已启动 PID=$(cat "$pidfile")"
                echo "   日志: $logfile"
                echo "   停止: ./ctl.sh $1 stop"
                return 0
            fi
            echo "❌ $name 启动失败，查看日志: $logfile"
            rm -f "$pidfile"
            ;;
        stop)
            if is_running; then
                kill "$(cat "$pidfile")" 2>/dev/null
                sleep 1
                kill -9 "$(cat "$pidfile")" 2>/dev/null
                echo "✅ $name 已停止"
            else
                echo "$name 未在运行"
            fi
            rm -f "$pidfile"
            ;;
        status)
            if is_running; then
                echo "✅ $name 运行中 PID=$(cat "$pidfile")"
            else
                echo "❌ $name 未运行"
            fi
            ;;
        log)
            tail -f "$logfile"
            ;;
        restart)
            # 用 `bash "$SELF"` 而非 `"$0"`：脚本可能没有执行权限（仓库里
            # 就是 644），直接 exec 会 Permission denied；且 `bash script`
            # 不依赖 x 位，是最稳的自我调用方式。
            bash "$SELF" "$1" stop
            sleep 1
            bash "$SELF" "$1" start
            ;;
        *)
            echo "用法: ./ctl.sh $1 {start|stop|status|log|restart}"
            ;;
    esac
}

# ------------------------------------------------------------------ #
# 子命令: 调度器
# ------------------------------------------------------------------ #
run_scheduler() {
    local cmd="${1:-start}"
    manage scheduler "$DIR/results/scheduler.pid" "$DIR/results/scheduler.log" \
        "$cmd" "$PY $DIR/examples/run_scheduler.py"
}

# ------------------------------------------------------------------ #
# 子命令: 持仓管理页面 (Streamlit)
# ------------------------------------------------------------------ #

run_holdings() {
    local cmd="${1:-start}"
    manage holdings "$DIR/results/holdings.pid" "$DIR/results/holdings.log" \
        "$cmd" "$PY -m streamlit run $DIR/dashboard/holdings_app.py --server.port ${HOLDINGS_PORT:-8503} --server.headless true"
}

run_stock() {
    local cmd="${1:-start}"
    manage stock "$DIR/results/stock.pid" "$DIR/results/stock.log" \
        "$cmd" "$PY -m streamlit run $DIR/dashboard/stock_app.py --server.port ${STOCK_PORT:-8504} --server.headless true"
}

run_dashboard() {
    local cmd="${1:-start}"
    manage dashboard "$DIR/results/dashboard.pid" "$DIR/results/dashboard.log" \
        "$cmd" "$PY -m streamlit run $DIR/dashboard/unified_app.py --server.port $PORT --server.headless true"
}

# ------------------------------------------------------------------ #
usage() {
    cat <<'EOF'
量化交易系统服务管理

用法:
  ./ctl.sh dashboard [start|stop|status|log|restart]   持仓管理页面 (:8502)
  ./ctl.sh scheduler [start|stop|status|log|restart]   分析推送调度器
  ./ctl.sh dashboard / scheduler                       简写 = 启动对应服务
  ./ctl.sh {start|stop|status|log|restart}             兼容旧用法: 操作调度器

示例:
  ./ctl.sh dashboard          # 打开持仓管理页面（浏览器访问 http://localhost:8502）
  ./ctl.sh dashboard log      # 查看页面日志
  ./ctl.sh scheduler stop     # 停止调度器

环境变量:
  PYTHON_BIN  指定 Python 解释器（默认自动探测 >=3.10 环境）
  PORT        持仓管理页面端口（默认 8502）
EOF
}

case "$1" in
    dashboard|page|ui)      run_dashboard "${2:-start}" ;;
    holdings|holdings_app) run_holdings "${2:-start}" ;;
    stock|stock_app)      run_stock "${2:-start}" ;;
    scheduler|scheduler.py) run_scheduler "${2:-start}" ;;
    start|stop|status|log|restart) run_scheduler "$1" ;;   # 兼容旧用法
    help|-h|--help)         usage ;;
    *)                      usage ;;
esac
