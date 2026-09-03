# -*- mode: python ; coding: utf-8 -*-
"""GP助手 桌面应用 PyInstaller 打包脚本（macOS / Windows / Linux 三端共用）。

用法（各平台分别构建）:
    pip install pyinstaller
    pyinstaller app/packaging/gp_assistant.spec

产物:
    macOS:   dist/GP助手.app
    Windows: dist/GPAssistant/GPAssistant.exe
    Linux:   dist/GP助手/GP助手

说明:
  * onedir 模式（非 onefile），启动更快、调试方便
  * 数据目录（config/notify.yaml、holdings.db、results/）由 app/main.py 的
    QTS_DATA_DIR 决定，默认在可执行文件旁的 config/ —— 不要打进包
  * Streamlit 有较多动态导入，需 hiddenimports 补齐
  * 不启用 UPX（upx=False）：Windows 端曾出现启动崩溃
    "MemoryError: Unable to allocate output buffer"（pyi_rth_inspect →
    pyimod01_archive extract → zlib.decompress 分配解压缓冲区失败），
    UPX 压缩 PYZ/base_library.zip 大条目是头号嫌疑；PyInstaller 官方默认
    也不启用 UPX，关闭后体积略增但更稳。
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

# spec 由 PyInstaller 以 exec 执行，__file__ 不可用 → 用 SPECPATH（spec 所在目录）
# app/packaging → parents[0]=app, parents[1]=仓库根。parents[2] 会指到仓库的上一级
# （Windows 上变成 C:\soft\config\...，datas 全部找不到）。
_SPEC_DIR = Path(SPECPATH).resolve()
_REPO_ROOT = _SPEC_DIR.parents[1]
REPO_ROOT = str(_REPO_ROOT)
for _p in (REPO_ROOT,):
    if _p not in sys.path:
        sys.path.insert(0, _p)
print("SPEC: repo root", REPO_ROOT)

block_cipher = None

# 是否 macOS（.app bundle）
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# --------------------------------------------------------------------------- #
# quant_trading_system 必须用 pip install . （非 editable）安装，PyInstaller 才能
# 解析 quant_trading_system.*。editable (-e) 只会出现 Hidden import not found。
# --------------------------------------------------------------------------- #
try:
    import quant_trading_system as _qts_pkg
    import quant_trading_system.stock_analysis.scheduler as _qts_sched

    _QTS_ROOT = Path(_qts_pkg.__file__).resolve().parent
    print("SPEC: quant_trading_system from", _QTS_ROOT)
    print("SPEC: scheduler from", _qts_sched.__file__)
except Exception as _exc:
    raise SystemExit(
        "FATAL: quant_trading_system not importable. "
        "Run: pip uninstall -y quant-trading-system && "
        'pip install ".[data,dashboard,gui]" (NO -e editable). '
        f"Detail: {_exc}"
    )


def _collect_installed_modules(pkg_name: str) -> list:
    import importlib

    pkg = importlib.import_module(pkg_name)
    root = Path(getattr(pkg, "__file__", "") or "").resolve().parent
    if not root.is_dir():
        return [pkg_name]
    mods = [pkg_name]
    for py in root.rglob("*.py"):
        if py.name == "__init__.py":
            continue
        rel = py.relative_to(root.parent)
        mod = str(rel.with_suffix("")).replace("\\", ".").replace("/", ".")
        if all(part.isidentifier() for part in mod.split(".")):
            mods.append(mod)
    return mods


_qts_datas, _qts_binaries, _qts_hidden = collect_all("quant_trading_system")

datas = _qts_datas
binaries = _qts_binaries
hiddenimports = list(_qts_hidden)
hiddenimports += _collect_installed_modules("quant_trading_system")

# 显式 collect_submodules（仅在包已正确安装时有效）
for _subpkg in (
    "quant_trading_system",
    "quant_trading_system.stock_analysis",
    "quant_trading_system.stock_analysis.opportunity",
    "quant_trading_system.stock_analysis.market",
    "quant_trading_system.stock_analysis.ai",
    "quant_trading_system.stock_analysis.backtest",
    "quant_trading_system.stock_analysis.scoring",
    "quant_trading_system.utils",
    "quant_trading_system.dashboard",
):
    try:
        hiddenimports += collect_submodules(_subpkg)
    except Exception as _exc:
        print("WARN collect_submodules(%s): %s" % (_subpkg, _exc))

hiddenimports += [
    "quant_trading_system.stock_analysis",
    "quant_trading_system.stock_analysis.scheduler",
    "quant_trading_system.stock_analysis.app_config",
    "quant_trading_system.stock_analysis.notifier",
    "quant_trading_system.stock_analysis.holdings",
    "quant_trading_system.stock_analysis.data_fetcher",
    "quant_trading_system.stock_analysis.indicators",
    "quant_trading_system.stock_analysis.patterns",
    "quant_trading_system.stock_analysis.news",
    "quant_trading_system.utils",
    "quant_trading_system.utils.logger",
    "quant_trading_system.utils.helpers",
    "quant_trading_system.utils.calendar",
    "quant_trading_system.utils.app_meta",
    "quant_trading_system.utils.updater",
    "certifi",
]

# config 模板 + 看板脚本（Streamlit 必须读磁盘上的 .py；collect_all 虽会带上，
# 但显式拷到 quant_trading_system/dashboard 以免漏 pages/*.py）
_notify_src = _REPO_ROOT / "config" / "notify.yaml.example"
_dash_src = _REPO_ROOT / "dashboard"
_home_src = _dash_src / "home.py"
if not _notify_src.is_file() or not _home_src.is_file():
    raise SystemExit(
        "FATAL: packaging data missing. repo=%s notify=%s home=%s"
        % (REPO_ROOT, _notify_src, _home_src)
    )
print("SPEC: dashboard", _dash_src)
_st_cfg = _REPO_ROOT / ".streamlit"
datas += [
    (str(_notify_src), "config"),
    (str(_dash_src), "quant_trading_system/dashboard"),
]
if _st_cfg.is_dir():
    datas += [(str(_st_cfg), ".streamlit")]

# Streamlit 用 importlib.metadata.version("streamlit") 读版本；只 hiddenimport
# 不会带上 *.dist-info，冻结后会 PackageNotFoundError。collect_all 同时带上
# streamlit/static 前端资源。产物是 win-amd64 onedir，和 GitHub Release 相同。
for _pkg in ("streamlit", "pyarrow", "webview"):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception as _exc:
        print("WARN collect_all(%s): %s" % (_pkg, _exc))

try:
    datas += copy_metadata("streamlit", recursive=True)
except TypeError:
    datas += copy_metadata("streamlit")
    for _meta_pkg in (
        "altair",
        "pandas",
        "numpy",
        "pyarrow",
        "protobuf",
        "packaging",
        "click",
        "tornado",
        "watchdog",
        "blinker",
        "cachetools",
        "jsonschema",
        "pillow",
        "tenacity",
        "toml",
        "typing_extensions",
        "requests",
        "narwhals",
        "GitPython",
        "pydeck",
        "rich",
        "pywebview",
    ):
        try:
            datas += copy_metadata(_meta_pkg)
        except Exception:
            pass
except Exception as _exc:
    print("WARN copy_metadata(streamlit): %s" % _exc)

# 数据/网络/看板运行时 lazy import — 单条 hiddenimport 不够，必须 collect_all + submodules
for _pkg in (
    "akshare",
    "yfinance",
    "certifi",
    "curl_cffi",
    "lxml",
    "py_mini_racer",
    "uvicorn",
    "starlette",
    "anyio",
    "httptools",
    "websockets",
    "yaml",
    "requests",
    "urllib3",
    "charset_normalizer",
    "idna",
    "bs4",
    "html5lib",
    "jsonpath",
    "openpyxl",
    "xlrd",
    "tabulate",
    "tqdm",
    "decorator",
    "multitasking",
    "peewee",
    "platformdirs",
    "pytz",
    "tzdata",
    "bottle",
    "proxy_tools",
    "pythonnet",
    "git",
    "google.protobuf",
    "altair",
    "narwhals",
    "pydeck",
    "PIL",
    "blinker",
    "click",
    "packaging",
    "tenacity",
    "toml",
    "rich",
):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception as _exc:
        print("WARN collect_all(%s): %s" % (_pkg, _exc))

for _sub in ("akshare", "yfinance"):
    try:
        hiddenimports += collect_submodules(_sub)
    except Exception as _exc:
        print("WARN collect_submodules(%s): %s" % (_sub, _exc))

hiddenimports += [
    "quant_trading_system.stock_analysis.screener",
    "quant_trading_system.stock_analysis.sector",
    "quant_trading_system.stock_analysis.sell_zone",
    "quant_trading_system.stock_analysis.holdings_action",
    "quant_trading_system.stock_analysis.trade_monitor",
    "quant_trading_system.stock_analysis.indicators",
    "quant_trading_system.stock_analysis.ai_client",
    "quant_trading_system.stock_analysis.ai.ai_analyst",
    "quant_trading_system.stock_analysis.scheduler_state",
    "quant_trading_system.stock_analysis.market.index_data",
    "quant_trading_system.stock_analysis.market.market_breadth",
    "quant_trading_system.stock_analysis.market.market_regime",
    "quant_trading_system.stock_analysis.opportunity.batch_scanner",
    "quant_trading_system.stock_analysis.opportunity.opportunity_engine",
    "quant_trading_system.stock_analysis.backtest.trading_plan_backtest",
    "quant_trading_system.dashboard.paths",
    "streamlit.components.v1",
    "clr",
    "Python.Runtime",
    "email.mime.multipart",
    "email.mime.text",
    "email.mime.application",
    "smtplib",
    "zoneinfo",
]

hiddenimports += ["ssl", "_ssl", "_hashlib", "_socket"]
hiddenimports = list(dict.fromkeys(hiddenimports))

# Windows: _ssl.pyd 依赖 libssl/libcrypto；NuGet/embeddable CPython 常把它们
# 放在 DLLs\ 且不被 bindepend 跟踪，冻结后 ImportError: DLL load failed _ssl。
# 同时收集 vcruntime，供 ARM Windows 上跑 x64 exe（系统可能没有 x64 VC 运行库）。
if IS_WIN:
    _ssl_dirs = []
    try:
        import _ssl as _ssl_mod

        _ssl_dirs.append(Path(_ssl_mod.__file__).resolve().parent)
    except Exception as _exc:
        print("WARN import _ssl during spec:", _exc)
    for _base in (sys.base_prefix, sys.exec_prefix, str(Path(sys.executable).resolve().parent)):
        _ssl_dirs.append(Path(_base))
        _ssl_dirs.append(Path(_base) / "DLLs")
    _dll_pats = (
        "libssl*.dll",
        "libcrypto*.dll",
        "libffi*.dll",
        "vcruntime*.dll",
        "msvcp*.dll",
        "python3*.dll",
    )
    _seen_dll = set()
    for _dir in _ssl_dirs:
        if not _dir.is_dir():
            continue
        for _pat in _dll_pats:
            for _dll in _dir.glob(_pat):
                _key = _dll.name.lower()
                if _key in _seen_dll:
                    continue
                _seen_dll.add(_key)
                binaries.append((str(_dll), "."))
                print("COLLECT DLL", _dll)
        if _dir.name.lower() == "dlls":
            for _dll in _dir.glob("*.dll"):
                _key = _dll.name.lower()
                if _key in _seen_dll:
                    continue
                _seen_dll.add(_key)
                binaries.append((str(_dll), "."))
                print("COLLECT DLL", _dll)
    if not any(n.startswith("libssl") for n in _seen_dll):
        for _pat in ("libssl*.dll", "libcrypto*.dll"):
            for _dll in Path(sys.base_prefix).rglob(_pat):
                _key = _dll.name.lower()
                if _key in _seen_dll:
                    continue
                _seen_dll.add(_key)
                binaries.append((str(_dll), "."))
                print("COLLECT DLL (rglob)", _dll)
    if not any(n.startswith("libssl") for n in _seen_dll):
        print("WARN: no libssl*.dll found next to this Python; frozen _ssl may fail")
    try:
        from PyInstaller.utils.hooks import collect_dynamic_libs

        binaries += collect_dynamic_libs("pythonnet")
    except Exception as _exc:
        print("WARN collect_dynamic_libs(pythonnet): %s" % _exc)

# --------------------------------------------------------------------------- #
# 隐藏导入：Streamlit 动态加载的模块（追加到 collect_all 结果之后）
# --------------------------------------------------------------------------- #
hiddenimports += [
    # streamlit
    "streamlit",
    "streamlit.web.cli",
    "streamlit.web.bootstrap",
    "streamlit.runtime.scriptrunner",
    "streamlit.runtime.scriptrunner.script_cache",
    "streamlit.runtime.secrets",
    "streamlit.runtime.caching",
    "streamlit.runtime.caching.cache_data_api",
    "streamlit.runtime.caching.cache_resource_api",
    "streamlit.elements.image",
    "streamlit.elements.arrow",
    "streamlit.elements.lib",
    "streamlit.elements.lib.column_config_utils",
    "streamlit.elements.lib.mutable_column_config",
    "streamlit.elements.lib.streamlit_plotly_theme",
    "streamlit.elements.vega_charts",
    "streamlit.elements.plotly_chart",
    "streamlit.elements.bokeh_chart",
    "streamlit.elements.graphviz_chart",
    "streamlit.elements.deck_gl_json_chart",
    "streamlit.elements.map",
    "streamlit.elements.iframe",
    "streamlit.elements.media",
    "streamlit.elements.file_uploader",
    "streamlit.elements.widgets",
    "streamlit.elements.widgets.file_uploader",
    "streamlit.elements.widgets.time_widgets",
    "streamlit.elements.widgets.media",
    "streamlit.elements.widgets.color_picker",
    "streamlit.elements.widgets.button",
    "streamlit.elements.widgets.button_group",
    "streamlit.elements.widgets.camera_input",
    "streamlit.elements.widgets.chat",
    "streamlit.elements.widgets.checkbox",
    "streamlit.elements.widgets.data_editor",
    "streamlit.elements.widgets.head_widgets",
    "streamlit.elements.widgets.image",
    "streamlit.elements.widgets.multiselect",
    "streamlit.elements.widgets.number_input",
    "streamlit.elements.widgets.radio",
    "streamlit.elements.widgets.select_slider",
    "streamlit.elements.widgets.selectbox",
    "streamlit.elements.widgets.slider",
    "streamlit.elements.widgets.text_widgets",
    "streamlit.elements.widgets.toggle",
    "streamlit.elements.widgets.audio",
    "streamlit.elements.widgets.video",
    "streamlit.elements.widgets.toast",
    "streamlit.elements.widgets.metric",
    "streamlit.elements.widgets.progress",
    "streamlit.elements.widgets.link_button",
    "streamlit.elements.widgets.slider",
    "streamlit.elements.widgets.time_widgets",
    "streamlit.elements.widgets.download_button",
    "streamlit.elements.widgets.download_button",
    "streamlit.elements.widgets.multiselect",
    "streamlit.elements.widgets.file_uploader",
    "streamlit.elements.widgets.camera_input",
    "streamlit.elements.widgets.metric",
    "streamlit.elements.widgets.toast",
    "streamlit.elements.widgets.text_widgets",
    "streamlit.elements.widgets.head_widgets",
    "streamlit.elements.widgets.button",
    "streamlit.elements.widgets.button_group",
    "streamlit.elements.widgets.checkbox",
    "streamlit.elements.widgets.color_picker",
    "streamlit.elements.widgets.data_editor",
    "streamlit.elements.widgets.slider",
    "streamlit.elements.widgets.selectbox",
    "streamlit.elements.widgets.radio",
    "streamlit.elements.widgets.select_slider",
    "streamlit.elements.widgets.multiselect",
    "streamlit.elements.widgets.number_input",
    "streamlit.elements.widgets.toggle",
    "streamlit.elements.widgets.link_button",
    "streamlit.elements.widgets.download_button",
    "streamlit.elements.widgets.audio",
    "streamlit.elements.widgets.video",
    "streamlit.elements.widgets.image",
    "streamlit.elements.widgets.media",
    "streamlit.elements.widgets.chat",
    "streamlit.elements.widgets.file_uploader",
    # 数据库驱动
    "sqlite3",
    "pandas",
    "numpy",
    "pyarrow",
]

# 平台特异的隐藏导入
if IS_WIN:
    hiddenimports += [
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "win32timezone",
    ]
elif IS_MAC:
    hiddenimports += [
        "webview.platforms.cocoa",
    ]
else:
    hiddenimports += [
        "webview.platforms.gtk",
    ]

hiddenimports = list(dict.fromkeys(hiddenimports))

a = Analysis(
    ["../../app/main.py"],
    pathex=["../.."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[
        os.path.join(SPECPATH, "pyi_rth_win_stdio.py"),
        os.path.join(SPECPATH, "pyi_rth_qts_pkg.py"),
    ],
    excludes=["tkinter", "test"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Windows 用 ASCII 文件名：中文 GP助手.exe 在部分英文系统/解压工具下无法启动，
# 且 GitHub Release 会把「助手」从资源名里剥掉变成 GP.-Windows.zip
EXE_NAME = "GPAssistant" if IS_WIN else "GP助手"
DIR_NAME = "GPAssistant" if IS_WIN else "GP助手"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False if IS_WIN else True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=DIR_NAME,
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="GP助手.app",
        icon=None,
        bundle_identifier="com.yzz521.gpassistant",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleDisplayName": "GP助手",
            "CFBundleShortVersionString": "0.3.0",
            "CFBundleVersion": "0.3.0",
            "LSMinimumSystemVersion": "11.0",
        },
    )
