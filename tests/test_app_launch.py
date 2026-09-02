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
    assert script.name == "首页.py"
    assert (script.parent / "pages").is_dir()


def test_parse_dashboard_argv(app):
    port, script = app._parse_dashboard_argv(
        ["GPAssistant.exe", app.DASHBOARD_FLAG, "8502", "/tmp/首页.py"]
    )
    assert port == 8502
    assert script == Path("/tmp/首页.py")


def test_parse_dashboard_argv_rejects_short(app):
    with pytest.raises((ValueError, IndexError)):
        app._parse_dashboard_argv(["GPAssistant.exe", app.DASHBOARD_FLAG])


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
