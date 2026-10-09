"""全市场快照（东财 push2 源）单元测试 —— 全部离线，monkeypatch 单页抓取。

背景：2026-09-16 新浪 ``vip.stock.finance.sina.com.cn`` 的全市场接口对该网络
直接返回「拒绝访问」HTML，``ak.stock_zh_a_spot()`` / ``stock_hk_spot()`` 全线失效，
看板「今日推荐」空掉。改用东财 push2 作为首选源后，这里守住四个关键性质：
  1. 字段映射与单位（amount=元、市值=亿）；
  2. 主域名失败自动切备用域名（东财对高频 IP 会直接拒连）；
  3. 全量请求数受控 + TTL 缓存（避免打爆东财导致整段 IP 被封）；
  4. 拿到残缺数据时宁可失败也不静默改变初筛口径。
"""
from __future__ import annotations

import pandas as pd
import pytest
from quant_trading_system.stock_analysis import data_fetcher as df


def _row(code="600036", name="招商银行", close=40.85, pct=1.2, amount=1.7e9):
    return {"f12": code, "f14": name, "f2": close, "f3": pct, "f5": 1000.0,
            "f6": amount, "f8": 1.0, "f9": 7.5, "f20": 1.0e12, "f21": 8.0e11,
            "f23": 1.1}


@pytest.fixture(autouse=True)
def _clear_em_cache():
    df._EM_SPOT_CACHE.clear()
    yield
    df._EM_SPOT_CACHE.clear()


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def test_frame_maps_fields_and_units():
    out = df._em_spot_frame([_row()])
    assert out is not None and len(out) == 1
    r = out.iloc[0]
    assert r["code"] == "600036" and r["name"] == "招商银行"
    assert r["close"] == 40.85 and r["pct_chg"] == 1.2
    assert r["amount"] == 1.7e9            # 元（下游 min_amount=5e7 直接可比）
    assert r["total_cap_yi"] == pytest.approx(1.0e4)   # 1e12 元 → 1 万亿 = 1e4 亿
    assert r["float_cap_yi"] == pytest.approx(8.0e3)
    assert r["pe"] == 7.5 and r["pb"] == 1.1


def test_frame_handles_placeholder_dash():
    """停牌/退市行字段是 "-"，必须转 NaN 而不是让整批解析失败。"""
    bad = {"f12": "000003", "f14": "PT金田A", "f2": "-", "f3": "-", "f5": "-",
           "f6": "-", "f8": "-", "f9": "-", "f20": "-", "f21": "-", "f23": "-"}
    out = df._em_spot_frame([_row(), bad])
    assert out is not None and len(out) == 2
    assert pd.isna(out.loc[out["code"] == "000003", "close"].iloc[0])


def test_frame_keeps_five_digit_hk_codes():
    """港股 5 位代码不能被补成 6 位（A/H 代码长度不同）。"""
    out = df._em_spot_frame([{"f12": "00001", "f14": "长和", "f2": 67.65,
                              "f3": 0.67, "f5": 1.0, "f6": 1.6e8, "f8": 1.0,
                              "f9": 1.0, "f20": 1.0, "f21": 1.0, "f23": 1.0}])
    assert out.iloc[0]["code"] == "00001"


def test_frame_dedupes_and_rejects_empty():
    assert df._em_spot_frame([]) is None
    assert df._em_spot_frame([{"f12": "1"}]) is None        # 缺 amount 列 → 拒绝
    dup = df._em_spot_frame([_row(), _row()])
    assert len(dup) == 1


# --------------------------------------------------------------------------- #
# 域名轮换
# --------------------------------------------------------------------------- #
def test_page_falls_back_to_second_host(monkeypatch):
    seen: list[str] = []

    def _fake(pn, fs, sort="f12", host=None, attempts=2):
        seen.append(host)
        if host == df._EM_HOSTS[0]:
            raise RuntimeError("主域名连接被拒")
        return [_row()], 1

    monkeypatch.setattr(df, "_em_clist_page", _fake)
    rows, total = df._em_get_page(1, df.EM_FS_CN)
    assert total == 1 and rows
    assert seen == [df._EM_HOSTS[0], df._EM_HOSTS[1]]


def test_page_raises_when_all_hosts_fail(monkeypatch):
    monkeypatch.setattr(df, "_em_clist_page",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("全挂")))
    with pytest.raises(RuntimeError):
        df._em_get_page(1, df.EM_FS_CN)


# --------------------------------------------------------------------------- #
# 请求数控制 / 缓存 / 完整性
# --------------------------------------------------------------------------- #
def test_candidate_path_stops_early(monkeypatch):
    """成交额降序：某页最低成交额低于阈值即停，不再继续翻页。"""
    calls: list[int] = []

    def _fake(pn, fs, sort="f12", host=None, attempts=2):
        calls.append(pn)
        if pn == 1:
            return [_row(code="600001", amount=9e8), _row(code="600002", amount=6e8)], 5915
        return [_row(code="600003", amount=9e6), _row(code="600004", amount=8e6)], 5915  # 已低于 5e7

    monkeypatch.setattr(df, "_em_clist_page", _fake)
    out = df.fetch_spot_candidates_em(df.EM_FS_CN, min_amount=5e7, max_pages=8)
    assert out is not None and len(out) == 4
    assert calls == [1, 2]        # 第 3 页因早停未发起


def test_candidate_path_caps_pages(monkeypatch):
    calls: list[int] = []

    def _fake(pn, fs, sort="f12", host=None, attempts=2):
        calls.append(pn)
        return [_row(code=f"6000{pn:02d}", amount=9e8)], 5915

    monkeypatch.setattr(df, "_em_clist_page", _fake)
    df.fetch_spot_candidates_em(df.EM_FS_CN, min_amount=5e7, max_pages=3)
    assert calls == [1, 2, 3]


def test_candidate_uses_amount_sort(monkeypatch):
    sorts: list[str] = []

    def _fake(pn, fs, sort="f12", host=None, attempts=2):
        sorts.append(sort)
        return [_row(code=f"6000{pn:02d}", amount=9e8)], 5915

    monkeypatch.setattr(df, "_em_clist_page", _fake)
    df.fetch_spot_candidates_em(df.EM_FS_CN, min_amount=5e7, max_pages=2)
    assert set(sorts) == {"f6"}   # 必须按成交额降序，才能只取前几页


def test_full_sweep_cached_within_ttl(monkeypatch):
    calls: list[int] = []

    def _fake(pn, fs, sort="f12", host=None, attempts=2):
        calls.append(pn)
        base = (pn - 1) * 100
        return [_row(code=f"{600000 + base + i}") for i in range(100)], 200

    monkeypatch.setattr(df, "_em_clist_page", _fake)
    first = df.fetch_spot_snapshot_em(df.EM_FS_CN)
    n_after_first = len(calls)
    second = df.fetch_spot_snapshot_em(df.EM_FS_CN)
    assert first is not None and second is not None
    assert len(first) == len(second) == 200
    assert n_after_first == 2 and len(calls) == 2   # 第二次命中缓存，零请求


def test_partial_sweep_is_rejected(monkeypatch):
    """只取到 total 的 60% 以下 → 整轮失败（宁可用旧源也不要用残缺全市场）。"""

    def _fake(pn, fs, sort="f12", host=None, attempts=2):
        if pn == 1:
            return [_row(code=f"{600000 + i}") for i in range(100)], 5915
        raise RuntimeError("后续页全挂")

    monkeypatch.setattr(df, "_em_clist_page", _fake)
    assert df.fetch_spot_snapshot_em(df.EM_FS_CN) is None


def test_fetch_spot_snapshot_prefers_candidates_when_min_amount(monkeypatch):
    """初筛（带阈值）走轻量候选路径；宽度（无阈值）才拉全市场。"""
    called: list[str] = []

    monkeypatch.setattr(df, "fetch_spot_candidates_em",
                        lambda *a, **k: called.append("cand") or pd.DataFrame([_row()]))
    monkeypatch.setattr(df, "fetch_spot_snapshot_em",
                        lambda *a, **k: called.append("full") or pd.DataFrame([_row()]))
    monkeypatch.setattr(df, "_fetch_spot_snapshot_sina", lambda: None)

    assert df.fetch_spot_snapshot(min_amount=5e7) is not None
    assert called == ["cand"]
    called.clear()
    assert df.fetch_spot_snapshot() is not None
    assert called == ["full"]


def test_fetch_spot_snapshot_falls_back_to_sina(monkeypatch):
    """东财全挂时回退旧新浪源，行为与改动前一致。"""
    monkeypatch.setattr(df, "fetch_spot_candidates_em", lambda *a, **k: None)
    monkeypatch.setattr(df, "fetch_spot_snapshot_em", lambda *a, **k: None)
    monkeypatch.setattr(df, "_fetch_spot_snapshot_sina",
                        lambda: pd.DataFrame([{"code": "600036", "amount": 1e9}]))
    out = df.fetch_spot_snapshot(min_amount=5e7)
    assert out is not None and out.iloc[0]["code"] == "600036"
