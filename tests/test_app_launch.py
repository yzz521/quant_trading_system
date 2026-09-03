"""桌面应用启动路径与打包 zip 格式。"""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _load_app():
    p = _REPO / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("gp_assistant_app", p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def app():
    return _load_app()


def test_dashboard_script_exists_in_dev(app):
    script = app._dashboard_script()
    assert script.is_file()
    assert script.name == "home.py"
    assert (script.parent / "pages").is_dir()
    assert (script.parent / "pages" / "2_settings.py").is_file()


def test_parse_dashboard_argv(app):
    port, script = app._parse_dashboard_argv(
        ["GPAssistant.exe", app.DASHBOARD_FLAG, "8502", "/tmp/home.py"]
    )
    assert port == 8502
    assert script == Path("/tmp/home.py")


def test_parse_dashboard_argv_rejects_short(app):
    with pytest.raises((ValueError, IndexError)):
        app._parse_dashboard_argv(["GPAssistant.exe", app.DASHBOARD_FLAG])


def test_ensure_win_stdio_is_noop_on_posix(app):
    import os
    import sys

    if sys.platform == "win32":
        pytest.skip("Windows 上会改 os.devnull")
    before = os.devnull
    app._ensure_win_stdio()
    assert os.devnull == before


def test_spec_collects_streamlit_metadata():
    spec = (_REPO / "app" / "packaging" / "gp_assistant.spec").read_text(encoding="utf-8")
    assert "copy_metadata" in spec
    assert 'copy_metadata("streamlit"' in spec
    assert "streamlit" in spec and "collect_all" in spec
    assert 'for _pkg in ("streamlit"' in spec
    assert '_st_cfg' in spec
    assert '(str(_st_cfg), ".streamlit")' in spec


def test_spec_collects_openssl_dlls():
    spec = (_REPO / "app" / "packaging" / "gp_assistant.spec").read_text(encoding="utf-8")
    assert "libssl" in spec
    assert "libcrypto" in spec
    hook = (_REPO / "app" / "packaging" / "pyi_rth_win_stdio.py").read_text(encoding="utf-8")
    assert "add_dll_directory" in hook


def test_webview_hint_for_ssl_dll(app):
    hint = app._webview_import_hint(
        ImportError("DLL load failed while importing _ssl: 找不到指定的模块。")
    )
    assert "WebView2" in hint
    assert "vc_redist" in hint or "OpenSSL" in hint or "_ssl" in hint
    assert "不是缺 WebView2" in hint


def test_spec_repo_root_is_packaging_parents_1():
    spec_dir = _REPO / "app" / "packaging"
    repo_root = spec_dir.parents[1]
    assert repo_root == _REPO
    assert (repo_root / "config" / "notify.yaml.example").is_file()
    assert (repo_root / "dashboard" / "home.py").is_file()
    spec = (spec_dir / "gp_assistant.spec").read_text(encoding="utf-8")
    assert "_REPO_ROOT = _SPEC_DIR.parents[1]" in spec
    assert "_home_src" in spec


def test_spec_collects_stock_analysis_modules():
    spec = (_REPO / "app" / "packaging" / "gp_assistant.spec").read_text(encoding="utf-8")
    assert "collect_submodules" in spec
    assert "quant_trading_system.stock_analysis" in spec
    assert "quant_trading_system.stock_analysis.scheduler" in spec
    assert "quant_trading_system.stock_analysis.app_config" in spec
    assert "quant_trading_system.utils.app_meta" in spec
    assert "quant_trading_system.utils.updater" in spec
    assert '"akshare"' in spec
    assert "collect_all" in spec
    assert "certifi" in spec
    assert "verify_frozen_bundle" in (_REPO / "packaging" / "build-windows.bat").read_text(encoding="utf-8")
    bat = (_REPO / "packaging" / "build-windows.bat").read_text(encoding="utf-8")
    assert "pip install --prefer-binary" in bat
    assert "-e " not in bat.split("pip install")[1].split("pyinstaller")[0]
    assert "preflight OK" in bat
    assert "smoke.log" in bat
    assert "check_python.py" in bat
    assert "dashboard\\home.py" in bat


def test_verify_frozen_bundle_rejects_missing_qts_warnings(tmp_path):
    vf = importlib.util.spec_from_file_location(
        "verify_frozen_bundle", _REPO / "app" / "packaging" / "verify_frozen_bundle.py"
    )
    assert vf and vf.loader
    mod = importlib.util.module_from_spec(vf)
    vf.loader.exec_module(mod)

    root = tmp_path / "GPAssistant"
    internal = root / "_internal"
    internal.mkdir(parents=True)
    (internal / "streamlit-1.0.dist-info").mkdir()
    (internal / "cacert.pem").write_text("x", encoding="utf-8")
    (internal / "libssl-3.dll").write_bytes(b"x")
    (internal / "libcrypto-3.dll").write_bytes(b"x")
    dash = internal / "quant_trading_system" / "dashboard"
    dash.mkdir(parents=True)
    (dash / "home.py").write_text("#", encoding="utf-8")
    (dash / "pages").mkdir()
    (dash / "pages" / "0_opportunity.py").write_text("#", encoding="utf-8")
    (dash / "pages" / "1_holdings.py").write_text("#", encoding="utf-8")
    (dash / "pages" / "2_settings.py").write_text("#", encoding="utf-8")

    build = tmp_path / "build" / "gp_assistant"
    build.mkdir(parents=True)
    (build / "warn-gp_assistant.txt").write_text(
        "ERROR: Hidden import 'quant_trading_system.stock_analysis' not found\n",
        encoding="utf-8",
    )
    (build / "xref-gp_assistant.html").write_text(
        "quant_trading_system.stock_analysis.scheduler akshare certifi",
        encoding="utf-8",
    )

    cwd = Path.cwd()
    import os

    os.chdir(tmp_path)
    try:
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 1
    finally:
        os.chdir(cwd)


def test_smoke_writes_log_path(app):
    assert "smoke.log" in (_REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert "_smoke_log_path" in (_REPO / "app" / "main.py").read_text(encoding="utf-8")


def test_dashboard_paths_use_qts_data_dir():
    src = (_REPO / "dashboard" / "paths.py").read_text(encoding="utf-8")
    assert "QTS_DATA_DIR" in src
    holdings = (_REPO / "dashboard" / "pages" / "1_holdings.py").read_text(encoding="utf-8")
    assert "holdings_config" in holdings
    assert "notify_config" in holdings
    main = (_REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert 'env["QTS_DATA_DIR"]' in main
    assert "SMOKE_FLAG" in main


def test_streamlit_frozen_disables_development_mode():
    src = (_REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert "STREAMLIT_GLOBAL_DEVELOPMENT_MODE" in src
    assert "load_config_options" in src
    assert '"global.developmentMode": False' in src
    assert "webbrowser.open" in src
    cfg = (_REPO / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "developmentMode = false" in cfg


def test_scheduler_guards_sys_path_when_frozen():
    src = (_REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert 'if not getattr(sys, "frozen", False):' in src
    assert "sys.path.insert(0, str(BASE.parent))" in src


def test_make_zip_uses_posix_paths(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "gp_make_zip", _REPO / "app" / "packaging" / "make_zip.py"
    )
    assert spec and spec.loader
    mz = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mz)

    src = tmp_path / "GPAssistant"
    nested = src / "_internal" / "dashboard"
    nested.mkdir(parents=True)
    (nested / "home.py").write_text("ok", encoding="utf-8")
    (src / "GPAssistant.exe").write_bytes(b"mz")
    readme = tmp_path / "使用说明.txt"
    readme.write_text("hi", encoding="utf-8")

    out = tmp_path / "GP-Assistant-Windows.zip"
    mz.make_zip(out, [src, readme])

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "GPAssistant/GPAssistant.exe" in names
    assert "GPAssistant/_internal/dashboard/home.py" in names
    assert "使用说明.txt" in names
    assert not any("\\" in n for n in names)
