"""测试套件自身的卫生检查。

为什么需要它
------------
``tests/test_holdings_quant.py`` 里曾经有 8 个测试函数被**完整粘贴了两遍**：
Python 里后一个定义直接覆盖前一个，于是第一批 8 个测试永远不会被 pytest 收集。
它们和副本内容一致，所以「测试通过」这件事看起来没变 —— 但这意味着：

* 只要有人只改了其中一份（比如修了副本没修原件，或反之），被覆盖的那份就静默失效；
* 表面上的测试数量与真实执行数量对不上，覆盖率是虚的。

这类问题**不会让任何测试失败**，因此必须专门检查。本文件覆盖三种形态的重名：

1. 顶层 ``def test_*`` 重名；
2. 顶层测试类重名（后者整体覆盖前者，里面所有测试一起消失）；
3. 同一个测试类内部 ``def test_*`` 方法重名。

以及「文件里根本没有测试」这种无声失效。

⚠️ 2026-10 补洞：上面第 1 条原先只匹配 ``test_*`` 前缀，于是**辅助函数重名完全
不可见** —— 实测有人给 ``tests/test_factor_validation.py`` 追加了一个同名
``_stats`` / ``_mono``，把先定义的那份静默覆盖掉，检查器却全绿。这类重名比
测试函数重名**更隐蔽**：连测试数量都不变，只是被测行为悄悄换成了另一份。
现在收集**所有**顶层函数/类/方法（排除 ``@x.setter`` 这类合法的同名访问器）。
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _TESTS_DIR.parent
_PROD_DIR = _PROJECT_DIR / "stock_analysis"

# ``@x.setter`` / ``@x.deleter`` / ``@overload`` 是**合法**的同名定义
_ACCESSOR_DECOS = ("setter", "deleter", "getter", "overload")


def _test_files() -> list[Path]:
    return sorted(p for p in _TESTS_DIR.glob("test_*.py") if p.name != Path(__file__).name)


def _prod_files() -> list[Path]:
    return sorted(_PROD_DIR.rglob("*.py")) if _PROD_DIR.is_dir() else []


def _deco_name(dec) -> str:
    if isinstance(dec, ast.Attribute):
        return dec.attr
    if isinstance(dec, ast.Name):
        return dec.id
    return ""


def _is_legal_same_name(node) -> bool:
    """带 ``@x.setter`` 之类的装饰器 → 同名定义是合法的，不计入重名。"""
    return any(_deco_name(d) in _ACCESSOR_DECOS for d in node.decorator_list)


def _def_names(nodes) -> list[str]:
    return [n.name for n in nodes
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not _is_legal_same_name(n)]


def _collect(path: Path) -> dict:
    """返回 ``{"funcs": [...], "classes": {cls_name: [method, ...]}}``。

    收集**所有**顶层函数与类，不再只挑 ``test_*`` —— 重名覆盖与名字前缀无关。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    funcs: list[str] = []
    classes: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _is_legal_same_name(node):
                funcs.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.setdefault(node.name, []).extend(_def_names(node.body))
    return {"funcs": funcs, "classes": classes}


def _problems_for(path: Path) -> list[str]:
    data = _collect(path)
    out: list[str] = []

    dup_funcs = {n: c for n, c in Counter(data["funcs"]).items() if c > 1}
    if dup_funcs:
        out.append("顶层函数重名：" + "、".join(
            f"{n}×{c}" for n, c in sorted(dup_funcs.items())))

    # classes 用 dict 聚合，重名的类会合并方法列表 → 方法数超常即说明类名重复。
    # 这里单独重新统计类名出现次数。
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    class_names = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    dup_classes = {n: c for n, c in Counter(class_names).items() if c > 1}
    if dup_classes:
        out.append("类重名：" + "、".join(
            f"{n}×{c}" for n, c in sorted(dup_classes.items())))

    for cls, methods in data["classes"].items():
        dup = {n: c for n, c in Counter(methods).items() if c > 1}
        if dup:
            out.append(f"类 {cls} 内方法重名：" + "、".join(
                f"{n}×{c}" for n, c in sorted(dup.items())))
    return out


def _has_tests(path: Path) -> bool:
    data = _collect(path)
    return (any(n.startswith("test_") for n in data["funcs"])
            or any(m.startswith("test_")
                   for ms in data["classes"].values() for m in ms))


def test_no_duplicate_test_definitions():
    """任何测试文件都不得出现重名的定义（函数 / 类 / 类内方法，含辅助函数）。

    重名 = 后者覆盖前者 = 前者永不执行，而 pytest 依然全绿 —— 最隐蔽的失效。
    """
    problems = []
    for path in _test_files():
        for msg in _problems_for(path):
            problems.append(f"{path.name}: {msg}")
    assert not problems, (
        "存在重名测试定义（后者覆盖前者，前者永不执行）：\n  " + "\n  ".join(problems)
    )


def test_no_duplicate_definitions_in_production_code():
    """生产代码同样不允许重名的顶层函数 / 类 / 类内方法。

    同一个类里定义两遍同名方法 = 前一份逻辑永不执行；如果测试恰好只覆盖后一份
    的行为，整套测试依然全绿。这类缺陷只能靠静态检查发现。
    """
    problems = []
    for path in _prod_files():
        for msg in _problems_for(path):
            problems.append(f"{path.relative_to(_PROJECT_DIR)}: {msg}")
    assert not problems, (
        "生产代码存在重名定义（后者覆盖前者）：\n  " + "\n  ".join(problems)
    )


def test_no_duplicate_test_definitions():
    """任何测试文件都不得出现重名的测试函数 / 测试类 / 类内方法。

    重名 = 后者覆盖前者 = 前者永不执行，而 pytest 依然全绿 —— 最隐蔽的失效。
    """
    problems = []
    for path in _test_files():
        for msg in _problems_for(path):
            problems.append(f"{path.name}: {msg}")
    assert not problems, (
        "存在重名测试定义（后者覆盖前者，前者永不执行）：\n  " + "\n  ".join(problems)
    )


def test_test_files_are_discoverable():
    """目录里应当有测试文件，且文件名符合 pytest 发现规则。"""
    files = _test_files()
    assert files, "tests/ 下没有可发现的测试文件"
    for p in files:
        assert p.name.startswith("test_") and p.suffix == ".py"


def test_every_test_file_defines_at_least_one_test():
    """空测试文件是无声的失效 —— 要么写测试，要么别建文件。"""
    empty = [p.name for p in _test_files() if not _has_tests(p)]
    assert not empty, f"以下测试文件没有任何测试函数或测试类：{empty}"


@pytest.mark.parametrize("name", ["conftest.py"])
def test_conftest_is_not_a_test_file(name):
    """``conftest.py`` 不应被当作测试文件收集（防止有人改名成 test_conftest）。"""
    assert not (_TESTS_DIR / f"test_{name}").exists()


def test_detector_actually_catches_duplicates(tmp_path):
    """自检：把已知重名的样本喂给检查器，必须能报出来。

    否则「检查通过」可能只是检查器自己坏了 —— 这是所有静态检查的通病。
    """
    bad = tmp_path / "test_dup.py"
    bad.write_text(
        "def test_a():\n    assert True\n\n\n"
        "def test_a():\n    assert True\n\n\n"
        "class TestX:\n    def test_m(self):\n        assert True\n\n"
        "class TestX:\n    def test_m(self):\n        assert True\n",
        encoding="utf-8",
    )
    msgs = _problems_for(bad)
    assert any("顶层函数重名" in m and "test_a" in m for m in msgs)
    assert any("类重名" in m and "TestX" in m for m in msgs)
    assert any("方法重名" in m and "test_m" in m for m in msgs)


def test_detector_catches_duplicate_helpers(tmp_path):
    """自检：**辅助函数**重名也必须报出来（2026-10 补洞）。

    回归：原先只匹配 ``test_*`` 前缀，于是给 ``test_factor_validation.py``
    追加一个同名 ``_stats`` 时，检查器全绿 —— 而先定义的那份已被静默覆盖，
    依赖它的既有测试悄悄换了被测行为。
    """
    bad = tmp_path / "test_dup_helper.py"
    bad.write_text(
        "def _stats(a=1):\n    return {'a': a}\n\n\n"
        "def _stats(a=1, b=2):\n    return {'a': a, 'b': b}\n\n\n"
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    msgs = _problems_for(bad)
    assert any("顶层函数重名" in m and "_stats" in m for m in msgs)


def test_detector_allows_property_setters(tmp_path):
    """``@x.setter`` 是合法的同名定义，不得误报。"""
    ok = tmp_path / "test_prop.py"
    ok.write_text(
        "class TestP:\n"
        "    @property\n"
        "    def v(self):\n        return 1\n\n"
        "    @v.setter\n"
        "    def v(self, x):\n        pass\n\n"
        "    def test_ok(self):\n        assert True\n",
        encoding="utf-8",
    )
    assert _problems_for(ok) == []
