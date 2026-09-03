# PyInstaller runtime hook（在主脚本之前执行）。
# Windows 无控制台 exe 的 stdin/stdout/stderr 为 None；pywebview/bottle/pythonnet
# 会 open(os.devnull) 即 'nul'，在部分环境下报 FileNotFoundError。
# Win10+ 不再从 PATH 搜索扩展模块的 DLL，需 add_dll_directory(_MEIPASS)。
import os
import sys

if sys.platform == "win32":
    nul = r"\\.\NUL"
    os.devnull = nul
    for _name, _mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, _name, None) is None:
            try:
                setattr(sys, _name, open(nul, _mode, encoding="utf-8", errors="replace"))
            except OSError:
                pass
    if hasattr(os, "add_dll_directory"):
        _dirs = []
        if getattr(sys, "frozen", False):
            _dirs.append(getattr(sys, "_MEIPASS", ""))
            _dirs.append(os.path.dirname(sys.executable))
        for _d in _dirs:
            if _d and os.path.isdir(_d):
                try:
                    os.add_dll_directory(_d)
                except OSError:
                    pass
