#!/usr/bin/env python3
"""以「脱离当前进程组」的方式启动守护进程，并把真实 PID 写进 pid 文件。

为什么不用 ``nohup ... &``
--------------------------
``nohup`` 只挡 SIGHUP，挡不住「父 shell 退出时发给整个进程组的信号」。
在 CI / 被托管执行的环境里（例如由上层工具拉起 shell 再退出），
``nohup`` 起的子进程照样被杀 —— 实测表现为启动日志正常打出、
随后进程凭空消失、``exit code 137``（SIGKILL）。

``subprocess.Popen(..., start_new_session=True)`` 让子进程调用 ``setsid()``
成为新会话首进程，彻底脱离原进程组，父 shell 无论怎么退出都带不走它。
macOS 默认没有 ``setsid`` 命令，所以用 Python 实现最可移植。

日志以 **追加** 方式打开：覆盖会抹掉上一次崩溃的现场，而崩溃现场正是
最需要保留的东西。

用法::

    python spawn.py <pidfile> <logfile> <cmd> [args...]
"""
from __future__ import annotations

import os
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3:
        print("用法: spawn.py <pidfile> <logfile> <cmd> [args...]",
              file=sys.stderr)
        return 2
    pidfile, logfile, cmd = argv[0], argv[1], argv[2:]

    with open(logfile, "ab") as fh:
        proc = subprocess.Popen(
            cmd,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,     # 关键：脱离父进程组
            cwd=os.getcwd(),
        )
    with open(pidfile, "w", encoding="utf-8") as fh:
        fh.write(str(proc.pid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
