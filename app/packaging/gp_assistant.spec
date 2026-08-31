# -*- mode: python ; coding: utf-8 -*-
"""GP助手 桌面应用 PyInstaller 打包脚本（macOS / Windows / Linux 三端共用）。

用法（各平台分别构建）:
    pip install pyinstaller
    pyinstaller app/packaging/gp_assistant.spec

产物:
    macOS:   dist/GP助手.app
    Windows: dist/GP助手.exe
    Linux:   dist/GP助手

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

from PyInstaller.utils.hooks import collect_all

# 让 collect_all 能 import quant_trading_system（仓库根即包，parent 入 path）
# spec 由 PyInstaller 以 exec 执行，__file__ 不可用 → 用 SPECPATH（PyInstaller 注入）
REPO_ROOT = str(Path(SPECPATH).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

block_cipher = None

# 是否 macOS（.app bundle）
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# --------------------------------------------------------------------------- #
# 收集 quant_trading_system 包（仓库根即包，含全部子模块与函数内 import）
# --------------------------------------------------------------------------- #
_qts_datas, _qts_binaries, _qts_hidden = collect_all("quant_trading_system")

datas = _qts_datas
binaries = _qts_binaries
hiddenimports = list(_qts_hidden)

# config 模板（collect_all 不含非 py 文件）
datas += [("../../config/notify.yaml.example", "config")]

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
    # akshare / 数据
    "akshare",
    "pandas",
    "numpy",
    # 图表
    "matplotlib",
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

a = Analysis(
    ["../../app/main.py"],
    pathex=["../.."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GP助手",
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
    name="GP助手",
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
