"""概率标定（probability calibration）—— 把「置信度」从打分区间映射为可解释的成功概率。

问题
----
``opportunity_engine`` 里的 confidence 是手工线性加权：

    confidence = 0.6 * (机会分 / 100) + 0.4 * min(RR, 4) / 4

它是**单调打分**，不是概率：显示「置信度 70%」并不代表这笔计划真的七成能成。
直接后果是没法理性设定阈值——「置信度低于多少就降级为 WATCH」这个 X 无从选择，
因为打分和实际成功率之间没有任何校准关系。

本模块用历史回测样本做**后验标定（post-hoc calibration）**：保持打分不变，
只学一个单调映射 s → p，让输出可以被当做概率读。

三种方法
--------
* ``identity``  —— 不标定（对照组，也是样本不足时的安全回退）
* ``platt``     —— ``p = σ(a·logit(s) + b)``，2 参数。样本少（n < 1000）时最稳，
                    推荐作为默认。
* ``isotonic``  —— 单调回归（PAVA），无参、拟合能力强，但需要 n ≥ 1000 防过拟合。

标定函数**单调不减**，因此不改变选股排序，只改变数值的解释方式——这是可以
安全上线的前提。

评估用 out-of-fold 预测，避免用训练集指标自证。若提供了时间顺序（``order``），
交叉验证按**时间分块**而非随机划分：回测样本高度重叠（stride=5、持有 60 日，
同一段行情被反复计入），随机划分会让相邻样本跨折泄漏，把 ECE 估得过于乐观。

零新依赖：只用 numpy。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np

__all__ = [
    "Calibrator",
    "IsotonicParams",
    "PlattParams",
    "auc_score",
    "brier_score",
    "default_calibrator_path",
    "ece_score",
    "fit_calibrator",
    "isotonic_apply",
    "isotonic_fit",
    "load_default_calibrator",
    "platt_apply",
    "platt_fit",
    "reliability_table",
]

# 打分可能恰好为 0.0 或 1.0（引擎在缺机会分/RR 时给 0.0），logit 会发散，故裁剪。
_EPS = 1e-6

# 样本量门槛：低于此值不做标定，直接返回 identity。
_MIN_SAMPLES = 30
# 样本量低于此值只走 Platt（2 参数），高于则可用 isotonic。
_ISOTONIC_MIN_SAMPLES = 1000
# isotonic 每个台阶最少样本数，防过拟合抖动。
_ISOTONIC_MIN_LEAF = 20

# 约定文件名，由 examples/fit_confidence_calibration.py 产出。
_CALIB_FILENAME = "confidence_calibration.json"

# 塌缩守卫：标定后 5%~95% 分位差小于此值即认为打分被压平、失去区分的显示意义。
_MIN_SPREAD = 0.05
# AUC 守卫容差：标定后 AUC 跌超过此值即否决（标定不该改变排序）。
_AUC_TOL = 0.02
# 低于此 AUC 视为「打分本身没有分辨力」，标定解决不了，出一份诊断。
_AUC_WEAK = 0.55


def _nan_safe_round(v: float, nd: int = 4):
    """四舍五入；NaN/Inf 返回 ``None``（JSON 里比 ``NaN`` 干净）。"""
    v = float(v)
    return round(v, nd) if np.isfinite(v) else None


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _as_arrays(
    scores: Sequence[float], labels: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    s = np.asarray(scores, dtype=float).ravel()
    y = np.asarray(labels, dtype=float).ravel()
    if s.shape != y.shape:
        raise ValueError(f"scores 与 labels 长度不一致: {s.shape} vs {y.shape}")
    if s.size == 0:
        raise ValueError("scores 为空，无法标定")
    if not np.all(np.isfinite(s)):
        raise ValueError("scores 含 NaN/Inf，请先清洗")
    if not np.all(np.isfinite(y)):
        raise ValueError("labels 含 NaN/Inf，请先清洗")
    if not np.all((y == 0) | (y == 1)):
        raise ValueError("labels 必须为 0/1 二值")
    return s, y


def _clip01(p: np.ndarray) -> np.ndarray:
    return np.clip(p, _EPS, 1.0 - _EPS)


def _logit(p: np.ndarray) -> np.ndarray:
    p = _clip01(np.asarray(p, dtype=float))
    return np.log(p / (1.0 - p))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    # 数值稳定：z 很负时 exp(-z) 溢出
    out = np.empty_like(z, dtype=float)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


# --------------------------------------------------------------------------- #
# 评估指标
# --------------------------------------------------------------------------- #
def brier_score(probs: Sequence[float], labels: Sequence[float]) -> float:
    """Brier 分数 = 概率预测的均方误差（越低越好，0.25 ≈ 无信息基线）。"""
    p, y = _as_arrays(probs, labels)
    return float(np.mean((_clip01(p) - y) ** 2))


def reliability_table(
    probs: Sequence[float], labels: Sequence[float], n_bins: int = 10
) -> list[dict]:
    """可靠性表：每个概率分箱的「预测均值 vs 实际频率」。

    这是给人看的审计材料——理想情况下两列应当接近相等。某箱样本为 0 时
    ``count=0``，不参与 ECE 加权。
    """
    p, y = _as_arrays(probs, labels)
    p = _clip01(p)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    # 等宽分箱；右端 1.0 归入最后一箱
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    rows: list[dict] = []
    for b in range(n_bins):
        m = idx == b
        n_b = int(m.sum())
        rows.append(
            {
                "bin": b,
                "left": round(float(edges[b]), 4),
                "right": round(float(edges[b + 1]), 4),
                "count": n_b,
                "mean_pred": round(float(p[m].mean()), 4) if n_b else None,
                "actual_freq": round(float(y[m].mean()), 4) if n_b else None,
                "gap": round(float(p[m].mean() - y[m].mean()), 4) if n_b else None,
            }
        )
    return rows


def ece_score(
    probs: Sequence[float], labels: Sequence[float], n_bins: int = 10
) -> float:
    """Expected Calibration Error（期望标定误差，越低越好）。

    等宽分箱后按样本量加权平均 ``|预测均值 - 实际频率|``。解读：ECE = 0.08
    意味着「当你看到 70% 时，实际大约是 62%~78%」。

    ⚠️ ECE 单独看会骗人：**把每个样本都预测成基础成功率，ECE 接近 0 且完美校准**，
    但打分完全失去区分力。所以必须和 ``auc_score`` 一起看——这正是
    ``fit_calibrator`` 的塌缩安全阀要做的事。
    """
    p, y = _as_arrays(probs, labels)
    p = _clip01(p)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    total = p.size
    ece = 0.0
    for b in range(n_bins):
        m = idx == b
        n_b = int(m.sum())
        if n_b == 0:
            continue
        ece += (n_b / total) * abs(float(p[m].mean()) - float(y[m].mean()))
    return float(ece)


def auc_score(scores: Sequence[float], labels: Sequence[float]) -> float:
    """ROC AUC（等价于 Mann-Whitney U 统计量）。0.5 = 打分对结果毫无区分力。

    这是标定的**约束条件**，不是目标：标定只允许改变数值解释，不允许改变排序。
    理想情况下 AUC 应当前后一致；实测下降说明映射把打分压塌了。

    并列值用平均秩处理——引擎会把 confidence 四舍五入到 2 位小数，大量并列是常态，
    不处理并列会把塌缩误判成「仍有区分力」。
    """
    s, y = _as_arrays(scores, labels)
    n_pos = float(y.sum())
    n_neg = float(y.size - n_pos)
    if n_pos == 0.0 or n_neg == 0.0:
        return float("nan")

    order = np.argsort(s, kind="mergesort")
    sorted_s = s[order]
    ranks = np.empty(s.size, dtype=float)
    i = 0
    while i < s.size:
        j = i
        while j + 1 < s.size and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0     # 1-based 平均秩
        i = j + 1
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


# --------------------------------------------------------------------------- #
# Platt scaling
# --------------------------------------------------------------------------- #
@dataclass
class PlattParams:
    """``p = σ(a · logit(s) + b)``。``a`` 为 1 且 ``b`` 为 0 即恒等映射。"""

    a: float = 1.0
    b: float = 0.0

    def to_dict(self) -> dict:
        return {"a": round(float(self.a), 6), "b": round(float(self.b), 6)}


def platt_apply(scores: Sequence[float], params: PlattParams) -> np.ndarray:
    z = _logit(np.asarray(scores, dtype=float)) * float(params.a) + float(params.b)
    return _sigmoid(z)


# 斜率下界必须 > 0：负斜率会反转打分次序，破坏「标定不改变排序」的前提。
# 上界与截距界用来兜住准完全分离（quasi-separation）——那种情况下 logistic 的
# 极大似然解在无穷远，无约束 Newton 会跑飞成 a≈1e8 的阶跃函数。
_SLOPE_BOUNDS = (1e-3, 50.0)
_OFFSET_BOUNDS = (-30.0, 30.0)


def _platt_nll(
    theta: np.ndarray, X: np.ndarray, offset: np.ndarray, y: np.ndarray, ridge: float
) -> float:
    """带岭惩罚的平均负对数似然（越小越好）。线性预测子为 ``offset + X @ theta``。"""
    p = _clip01(_sigmoid(offset + X @ theta))
    ll = -np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))
    return float(ll + 0.5 * ridge * float(theta @ theta))


def _platt_design(x: np.ndarray, free_slope: bool) -> tuple[np.ndarray, np.ndarray]:
    """构造设计矩阵与加性偏置，返回 ``(X, offset)``。

    自由斜率：``p = σ(a·x + b)`` → ``X = [x, 1]``，``offset = 0``。
    固定斜率：``p = σ(1·x + b)`` → ``X = [1]``，``offset = x``。

    ⚠️ 固定斜率时 ``x`` 必须走 ``offset``，不能当作设计列。若把它放进设计矩阵
    （``X = [[x]]``、``θ = [b]``），算出来的是 ``σ(b·x)``——斜率被锁在 0 而非 1，
    拟合结果会整体偏掉（实测同一份数据会给出 b≈1.0 而非正确的 b≈0.01）。
    """
    if free_slope:
        return np.column_stack([x, np.ones_like(x)]), np.zeros_like(x)
    return np.ones((x.size, 1)), x


def _clamp_theta(theta: np.ndarray, free_slope: bool) -> np.ndarray:
    lo_s, hi_s = _SLOPE_BOUNDS
    lo_b, hi_b = _OFFSET_BOUNDS
    if free_slope:
        return np.array([np.clip(theta[0], lo_s, hi_s), np.clip(theta[1], lo_b, hi_b)])
    return np.array([np.clip(theta[0], lo_b, hi_b)])


def platt_fit(
    scores: Sequence[float],
    labels: Sequence[float],
    *,
    free_slope: bool = True,
    max_iter: int = 100,
    tol: float = 1e-10,
    ridge: float = 1e-3,
) -> PlattParams:
    """拟合 Platt 参数（最小化带岭的 log loss）。

    用阻尼 Newton + 回溯线搜索，并对参数设界。这不是过度谨慎：回测样本里打分与
    结果常常近乎可分（高置信度的计划几乎都赚），此时无约束 Newton 会把斜率推向
    无穷，得到一个 a≈1e8 的阶跃映射——它在训练集上 ECE 反而更差，且输出非 0 即 1，
    完全失去概率含义。线搜索保证目标单调下降，参数界保证结果始终是个可用的
    S 形曲线。

    Args:
        free_slope: False 时固定 ``a = 1``，只拟合截距 ``b``。参数越少越抗过拟合，
            样本很少（n 几十）时更稳。
        ridge: 岭惩罚强度，同时加在 Hessian 与目标函数上，保证可逆且抑制发散。
    """
    s, y = _as_arrays(scores, labels)
    x = _logit(s)
    X, offset = _platt_design(x, free_slope)

    n = float(x.size)
    theta = _clamp_theta(np.array([1.0, 0.0]) if free_slope else np.array([0.0]), free_slope)
    obj = _platt_nll(theta, X, offset, y, ridge)

    for _ in range(max_iter):
        p = _sigmoid(offset + X @ theta)
        grad = X.T @ (p - y) / n
        w = p * (1.0 - p)
        H = (X * w[:, None]).T @ X / n + ridge * np.eye(X.shape[1])
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            break

        # 回溯线搜索：只接受让目标下降的步长
        accepted = None
        t = 1.0
        for _ in range(50):
            cand = _clamp_theta(theta - t * step, free_slope)
            cand_obj = _platt_nll(cand, X, offset, y, ridge)
            if cand_obj <= obj:
                accepted = (cand, cand_obj)
                break
            t *= 0.5
        if accepted is None:
            break

        cand, cand_obj = accepted
        delta = float(np.max(np.abs(cand - theta)))
        theta, obj = cand, cand_obj
        if delta < tol:
            break

    if free_slope:
        return PlattParams(a=float(theta[0]), b=float(theta[1]))
    return PlattParams(a=1.0, b=float(theta[0]))


# --------------------------------------------------------------------------- #
# Isotonic 回归（PAVA）
# --------------------------------------------------------------------------- #
@dataclass
class IsotonicParams:
    """台阶函数的三个节点数组，配合 ``np.interp`` 使用（已保证单调不减）。"""

    xs: list = field(default_factory=list)
    ys: list = field(default_factory=list)
    counts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "xs": [round(float(v), 6) for v in self.xs],
            "ys": [round(float(v), 6) for v in self.ys],
            "counts": [int(v) for v in self.counts],
        }


def _pava(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Pool Adjacent Violators：把序列拟合成单调不减，返回等长数组。"""
    vals: list[float] = []
    wts: list[float] = []
    for v, w in zip(values.tolist(), weights.tolist()):
        cv, cw = float(v), float(w)
        while vals and vals[-1] > cv:
            pv = vals.pop()
            pw = wts.pop()
            cv = (pv * pw + cv * cw) / (pw + cw)
            cw = pw + cw
        vals.append(cv)
        wts.append(cw)

    out: list[float] = []
    for v, w in zip(vals, wts):
        out.extend([v] * int(round(w)))
    # 浮点累计误差可能让长度差 1
    if len(out) < values.size:
        out.extend([out[-1]] * (values.size - len(out)))
    return np.asarray(out[: values.size], dtype=float)


def isotonic_fit(
    scores: Sequence[float],
    labels: Sequence[float],
    *,
    min_leaf: int = _ISOTONIC_MIN_LEAF,
) -> IsotonicParams:
    """拟合单调不减的台阶映射。

    以**台阶右端**作为该段的 x 节点：``np.interp`` 在节点之间线性插值，等价于
    在台阶上取该段右边界值，符合「分数 >= 某门槛即进入该档」的语义。
    """
    s, y = _as_arrays(scores, labels)
    order = np.argsort(s, kind="mergesort")   # 稳定排序，同分保持原序
    s_sorted = s[order]
    y_sorted = y[order]
    fitted = _pava(y_sorted, np.ones_like(y_sorted))

    # 坍缩成台阶：连续相同拟合值归为一段
    xs: list[float] = []
    ys: list[float] = []
    cnts: list[int] = []
    i = 0
    n = s_sorted.size
    while i < n:
        j = i
        while j + 1 < n and fitted[j + 1] == fitted[i]:
            j += 1
        xs.append(float(s_sorted[j]))
        ys.append(float(fitted[i]))
        cnts.append(j - i + 1)
        i = j + 1

    # 合并样本量不足的台阶（并入前一段），避免用个位数样本支撑一个台阶
    m_x: list[float] = []
    m_y: list[float] = []
    m_c: list[int] = []
    for x, yv, c in zip(xs, ys, cnts):
        if m_y and c < min_leaf:
            pc = m_c[-1]
            m_y[-1] = (m_y[-1] * pc + yv * c) / (pc + c)
            m_c[-1] = pc + c
            m_x[-1] = x
        else:
            m_x.append(x)
            m_y.append(yv)
            m_c.append(c)

    # 合并过程可能破坏单调性，强制修正
    arr_y = np.maximum.accumulate(np.asarray(m_y, dtype=float))
    return IsotonicParams(xs=m_x, ys=arr_y.tolist(), counts=m_c)


def isotonic_apply(scores: Sequence[float], params: IsotonicParams) -> np.ndarray:
    xs = np.asarray(params.xs, dtype=float)
    ys = np.asarray(params.ys, dtype=float)
    s = np.asarray(scores, dtype=float)
    if xs.size == 0:
        return _clip01(s)
    if xs.size == 1:
        return np.full_like(s, float(np.clip(ys[0], _EPS, 1 - _EPS)))
    # 超出两端时夹住（np.interp 的 left/right 参数）
    return np.interp(s, xs, ys, left=float(ys[0]), right=float(ys[-1]))


# --------------------------------------------------------------------------- #
# Calibrator
# --------------------------------------------------------------------------- #
class Calibrator:
    """已拟合的标定器。``apply`` 单调不减，可直接替换引擎里的 confidence。"""

    def __init__(
        self,
        method: str = "identity",
        platt: Optional[PlattParams] = None,
        isotonic: Optional[IsotonicParams] = None,
        meta: Optional[dict] = None,
    ) -> None:
        self.method = method
        self.platt = platt
        self.isotonic = isotonic
        self.meta = dict(meta or {})

    # ---------------------------------------------------------------- #
    def apply(self, score: Optional[float]) -> float:
        """把单个打分映射为概率。``None`` 原样返回 ``None``（保持引擎语义）。"""
        if score is None:
            return None
        return float(self.apply_many([score])[0])

    def apply_many(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=float)
        if self.method == "platt" and self.platt is not None:
            return np.clip(platt_apply(s, self.platt), 0.0, 1.0)
        if self.method == "isotonic" and self.isotonic is not None:
            return np.clip(isotonic_apply(s, self.isotonic), 0.0, 1.0)
        return np.clip(s, 0.0, 1.0)

    # ---------------------------------------------------------------- #
    def to_dict(self) -> dict:
        d: dict = {"method": self.method, "meta": self.meta}
        if self.platt is not None:
            d["platt"] = self.platt.to_dict()
        if self.isotonic is not None:
            d["isotonic"] = self.isotonic.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Calibrator":
        platt = PlattParams(**d["platt"]) if d.get("platt") else None
        iso_d = d.get("isotonic")
        isotonic = (
            IsotonicParams(xs=iso_d["xs"], ys=iso_d["ys"], counts=iso_d.get("counts", []))
            if iso_d
            else None
        )
        return cls(method=d.get("method", "identity"), platt=platt,
                   isotonic=isotonic, meta=d.get("meta"))

    def save(self, path: Union[str, Path]) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return p

    @classmethod
    def load(cls, path: Union[str, Path]) -> Optional["Calibrator"]:
        """读取标定文件；不存在或损坏时返回 ``None``（调用方应回退到原始打分）。"""
        p = Path(path)
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None


# 合成数据拟合出的参数不是真实标定结果，不能悄悄上线。
_SYNTHETIC_MARKERS = ("合成", "synthetic")


def default_calibrator_path() -> Path:
    """约定的标定文件位置：``<数据目录>/results/confidence_calibration.json``。

    由 ``examples/fit_confidence_calibration.py`` 产出。解析顺序与仓库其它数据目录
    保持一致（对齐 ``app/main.py`` 的 ``RESULTS_DIR``），否则打包后引擎会去找
    PyInstaller 解包目录、永远读不到 exe 旁的参数：

      1. ``QTS_DATA_DIR`` 已设置（打包运行时的常态）→ 取其同级 ``results/``
         （若它本身以 ``config`` 结尾），否则直接用该目录；
      2. 开发态 → ``<仓库根>/results/``。

    文件不存在即表示「不标定」，引擎保持原有行为。
    """
    env = os.environ.get("QTS_DATA_DIR")
    if env:
        d = Path(env)
        base = d.parent if d.name == "config" else d
        return base / "results" / _CALIB_FILENAME
    return Path(__file__).resolve().parents[1] / "results" / _CALIB_FILENAME


def load_default_calibrator(*, allow_synthetic: bool = False) -> Optional["Calibrator"]:
    """加载约定位置的标定器。缺失、损坏、或来源是合成数据时返回 ``None``。

    拒绝合成来源是为了防呆：``fit_confidence_calibration.py --synthetic`` 已经默认
    写到带 ``SYNTHETIC`` 后缀的文件，这里再挡一道，避免有人手动改名上线。
    """
    cal = Calibrator.load(default_calibrator_path())
    if cal is None:
        return None
    source = str(cal.meta.get("source", "")).lower()
    if not allow_synthetic and any(m in source for m in _SYNTHETIC_MARKERS):
        return None
    return cal


# --------------------------------------------------------------------------- #
# 拟合 + 评估
# --------------------------------------------------------------------------- #
def _cv_folds(n: int, k: int, order: Optional[Sequence[float]]) -> list[np.ndarray]:
    """生成交叉验证折。

    提供 ``order``（如日期序）时按**时间连续分块**，否则随机等分。回测样本重叠
    严重，随机划分会把邻近期样本拆到不同折造成泄漏。
    """
    k = max(2, min(int(k), n))
    if order is not None:
        o = np.asarray(order, dtype=float).ravel()
        if o.size != n:
            raise ValueError("order 长度与样本数不一致")
        rank = np.argsort(o, kind="mergesort")
    else:
        rank = np.random.default_rng(20260922).permutation(n)
    return [np.sort(chunk) for chunk in np.array_split(rank, k)]


def _walk_forward_folds(
    n: int,
    k: int,
    order: Optional[Sequence[float]],
    embargo: int = 0,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """滚动前瞻折 ``[(训练索引, 测试索引)]``，训练集**严格早于**测试集。

    为什么需要它：``_cv_folds`` 的「时间连续分块」只保证**同折内**样本相邻，
    但每折的训练集仍是「其余所有折」—— 其中包含**测试折之后**的样本。于是早期
    测试折的评估用到了未来市场的信息，标定出来的 ECE/AUC 偏乐观。真实数据上
    这种泄漏会让「标定有效」的结论站不住。

    做法：按时间切成 k 段，第 i 段作测试、**只用前 i-1 段**训练；第 0 段没有可用
    训练数据，跳过。``embargo`` 用于剔除紧邻测试段之前的那部分训练样本 ——
    交易计划最长持有 60 日，相邻样本的结果彼此相关，不隔离同样算泄漏。
    """
    k = max(2, min(int(k), n))
    if order is not None:
        o = np.asarray(order, dtype=float).ravel()
        if o.size != n:
            raise ValueError("order 长度与样本数不一致")
        rank = np.argsort(o, kind="mergesort")
    else:
        rank = np.arange(n)

    chunks = np.array_split(rank, k)
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for i in range(1, len(chunks)):
        test = np.sort(chunks[i])
        train_rank = np.concatenate(chunks[:i])
        if embargo > 0:
            train_rank = train_rank[: max(0, train_rank.size - int(embargo))]
        if train_rank.size < 2 or test.size == 0:
            continue
        folds.append((np.sort(train_rank), test))
    return folds


def fit_calibrator(
    scores: Sequence[float],
    labels: Sequence[float],
    *,
    method: str = "auto",
    n_folds: int = 5,
    order: Optional[Sequence[float]] = None,
    n_bins: int = 10,
    cv: str = "auto",
    embargo: int = 0,
) -> tuple[Calibrator, dict]:
    """拟合标定器并返回 ``(calibrator, report)``。

    Args:
        scores: 原始置信度（引擎里未标定的 confidence）。
        labels: 0/1 —— 1 表示该计划按规则执行后盈利。
        method: ``auto`` / ``platt`` / ``isotonic`` / ``identity``。
            ``auto``：n < 1000 用 Platt，否则用 isotonic。
        order: 时间序（如交易日期），用于时间序交叉验证。
        n_bins: ECE 与可靠性表的分箱数。
        cv: 交叉验证策略。``auto``（默认）在给了 ``order`` 时用
            ``walk_forward``（滚动前瞻，训练集严格早于测试集），否则用 ``random``。
            显式传 ``time_block`` 可复现旧口径（**存在前视泄漏**，仅用于对照）。
        embargo: 滚动前瞻中从训练集尾部剔除的样本数（按时间序）。交易计划最长
            持有 60 日，相邻样本结果相关，默认不隔离会低估泄漏。

    样本少于 ``_MIN_SAMPLES`` 或标签只有单一类别时，返回 identity 标定器并在
    report 里说明原因——这种数据无法学出概率，硬拟合只会给出虚假的自信。
    """
    s, y = _as_arrays(scores, labels)
    n = int(s.size)
    base_rate = float(y.mean())

    strategy = cv
    if strategy == "auto":
        strategy = "walk_forward" if order is not None else "random"
    if strategy not in ("walk_forward", "time_block", "random"):
        raise ValueError(f"未知 cv 策略: {cv!r}")

    report: dict = {
        "n_samples": n,
        "base_rate": round(base_rate, 4),
        "n_bins": n_bins,
        "method": method,
        "fell_back": None,
        "ece_before": round(ece_score(s, y, n_bins), 4),
        "brier_before": round(brier_score(s, y), 4),
        # 区分力度量：AUC 0.5 = 打分对结果无分辨力；spread = 5%~95% 分位差
        "auc_before": _nan_safe_round(auc_score(np.round(s, 2), y)),
        "spread_before": round(float(np.percentile(s, 95) - np.percentile(s, 5)), 4),
    }

    reason = None
    if n < _MIN_SAMPLES:
        reason = f"样本量 {n} < {_MIN_SAMPLES}，不足以标定"
    elif y.min() == y.max():
        reason = f"标签只有单一类别（全为 {int(y.max())}），无法学出概率"

    if reason is not None:
        report["fell_back"] = reason
        report["method"] = "identity"
        report["ece_after_in_sample"] = report["ece_before"]
        report["brier_after_in_sample"] = report["brier_before"]
        report["ece_after_cv"] = report["ece_before"]
        report["brier_after_cv"] = report["brier_before"]
        report["auc_after_cv"] = report["auc_before"]
        report["spread_after_cv"] = report["spread_before"]
        return Calibrator(method="identity", meta=report), report

    resolved = method
    if resolved == "auto":
        resolved = "isotonic" if n >= _ISOTONIC_MIN_SAMPLES else "platt"
    if resolved not in ("platt", "isotonic", "identity"):
        raise ValueError(f"未知标定方法: {method!r}")

    def _fit_on(idx: np.ndarray) -> Calibrator:
        if resolved == "isotonic":
            return Calibrator("isotonic", isotonic=isotonic_fit(s[idx], y[idx]))
        if resolved == "platt":
            # 样本很少时锁死斜率，只学截距
            return Calibrator("platt", platt=platt_fit(s[idx], y[idx],
                                                       free_slope=idx.size >= 100))
        return Calibrator("identity")

    full = _fit_on(np.arange(n))

    # ---- out-of-fold 预测：用没见过的样本评估，避免自证 ----
    # walk_forward：训练集严格早于测试集（无前视泄漏）
    # time_block / random：训练集包含测试折之外的所有折
    oof = np.full(n, np.nan, dtype=float)
    cv_note = ""
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    if strategy == "walk_forward":
        folds = _walk_forward_folds(n, n_folds, order, embargo)
        if len(folds) < 2:
            strategy = "time_block" if order is not None else "random"
            cv_note = "样本太少，滚动前瞻折不足 2 个，退回分块交叉验证"

    if strategy == "walk_forward":
        for train, test in folds:
            oof[test] = _fit_on(train).apply_many(s[test])
    else:
        for fold in _cv_folds(n, n_folds, order):
            mask = np.ones(n, dtype=bool)
            mask[fold] = False
            sub = _fit_on(np.flatnonzero(mask))
            oof[fold] = sub.apply_many(s[fold])

    covered = ~np.isnan(oof)
    if int(covered.sum()) < max(2, n // 4):
        # 覆盖不足（walk_forward 的第一段没有可用训练集）→ 退化为样本内评估，
        # 并明确标注口径偏乐观，不假装是样本外结果。
        oof = full.apply_many(s)
        covered = np.ones(n, dtype=bool)
        cv_note = (cv_note + "；" if cv_note else "") + \
            "CV 覆盖不足，指标为样本内口径（偏乐观）"

    s_eval, y_eval, oof_eval = s[covered], y[covered], oof[covered]
    ece_cv = ece_score(oof_eval, y_eval, n_bins)
    brier_cv = brier_score(oof_eval, y_eval)

    # ---- 区分力守卫的度量 ----
    # 按引擎展示精度（2 位小数）取整后再算 AUC：标定映射在数学上单调，但若把斜率
    # 压到 1e-3 这种量级，输出在小数点后第 4 位才分化，取整后全部并成同一个数——
    # 数学上「排序未变」，实际显示上区分力已经归零。
    #
    # 前后必须在**同一子集**上比较：walk_forward 只覆盖部分样本，用全样本的
    # auc_before 去比子集的 auc_after 不是同一把尺子，会误判安全阀。
    _prec = 2
    auc_before = auc_score(np.round(s, _prec), y)
    auc_before_cv = auc_score(np.round(s_eval, _prec), y_eval)
    auc_after = auc_score(np.round(oof_eval, _prec), y_eval)
    spread_before = float(np.percentile(s, 95) - np.percentile(s, 5))
    spread_before_cv = float(np.percentile(s_eval, 95) - np.percentile(s_eval, 5))
    spread_after = float(np.percentile(oof_eval, 95) - np.percentile(oof_eval, 5))
    ece_before_cv = ece_score(s_eval, y_eval, n_bins)
    brier_before_cv = brier_score(s_eval, y_eval)
    reliability_before_cv = reliability_table(s_eval, y_eval, n_bins)

    # 塌缩守卫必须量的是**实际交付的标定器**（full 在全部样本上拟合后映射出的跨度），
    # 而不是 out-of-fold 预测。反例（真实踩到）：walk_forward 每折各学一个不同的常数，
    # oof 跨度看着正常，但全样本拟合出的映射把所有输入压成同一个值——上线后所有标的
    # 显示同一个置信度，这正是「推荐怪怪的」的来源。
    delivered = full.apply_many(s)
    spread_delivered = float(np.percentile(delivered, 95) - np.percentile(delivered, 5))

    report.update(
        {
            "method": resolved,
            "ece_before": round(ece_score(s, y, n_bins), 4),
            "brier_before": round(brier_score(s, y), 4),
            "ece_after_in_sample": round(ece_score(full.apply_many(s), y, n_bins), 4),
            "brier_after_in_sample": round(brier_score(full.apply_many(s), y), 4),
            "ece_after_cv": round(ece_cv, 4),
            "brier_after_cv": round(brier_cv, 4),
            "auc_before": _nan_safe_round(auc_before),
            "auc_before_cv": _nan_safe_round(auc_before_cv),
            "auc_after_cv": _nan_safe_round(auc_after),
            "spread_before": round(spread_before, 4),
            "spread_before_cv": round(spread_before_cv, 4),
            "spread_after_cv": round(spread_after, 4),
            "spread_delivered": round(spread_delivered, 4),
            "ece_before_cv": round(ece_before_cv, 4),
            "brier_before_cv": round(brier_before_cv, 4),
            "cv_folds": int(max(2, min(n_folds, n))),
            "cv_strategy": strategy,
            "cv_covered": int(covered.sum()),
            "cv_embargo": int(embargo),
            "cv_note": cv_note,
            "reliability_before": reliability_table(s, y, n_bins),
            "reliability_before_cv": reliability_before_cv,
            "reliability_after_cv": reliability_table(oof_eval, y_eval, n_bins),
            "params": full.to_dict(),
        }
    )

    # 诊断（不是否决理由，但要让人看见）：打分本身没有分辨力时，标定帮不上忙。
    if np.isfinite(auc_before) and auc_before < _AUC_WEAK:
        report["diagnosis"] = (
            f"原始打分对交易结果几乎没有区分力（AUC {auc_before:.3f}，0.5 = 掷硬币），"
            f"标定无法解决这个问题——需要改打分公式本身（机会分因子 / 引入新特征）。"
        )

    # ---- 安全阀：标定只许改数值解释，不许改排序、也不许把打分压塌 ----
    # 注意：所有比较都在**同一子集**（CV 覆盖到的样本）上进行。walk_forward 只覆盖
    # 部分样本，用全样本 auc_before 去比子集 auc_after 会拿两把尺子量，误判安全阀。
    reject = None
    if spread_delivered < _MIN_SPREAD:
        reject = (
            f"标定把置信度压塌了：交付的标定器在样本上 5%~95% 分位只差 "
            f"{spread_delivered:.3f}（阈值 {_MIN_SPREAD}），所有标的会显示同一个数值"
        )
    elif (
        np.isfinite(auc_before_cv)
        and np.isfinite(auc_after)
        and auc_after < auc_before_cv - _AUC_TOL
    ):
        reject = f"标定损失了区分力：AUC {auc_before_cv:.3f} → {auc_after:.3f}"
    elif ece_cv > ece_before_cv:
        reject = f"标定后 CV ECE {round(ece_cv, 4)} 劣于原始打分 {round(ece_before_cv, 4)}"

    if reject is not None:
        report["ece_after_cv_rejected"] = round(ece_cv, 4)
        report["brier_after_cv_rejected"] = round(brier_cv, 4)
        report["rejected_method"] = resolved
        report["fell_back"] = reject + "，回退为不标定"
        # 契约：ece_after_cv / brier_after_cv / reliability_after_cv 永远描述
        # 「实际交付的东西」。回退时交付的是 identity，指标就等于标定前——且必须是
        # **同一子集**的标定前数值（*_before_cv），否则子集与全样本混用会自相矛盾。
        report["ece_after_cv"] = report["ece_before_cv"]
        report["brier_after_cv"] = report["brier_before_cv"]
        report["reliability_after_cv"] = reliability_before_cv
        report["auc_after_cv"] = report["auc_before_cv"]
        report["spread_after_cv"] = report["spread_before_cv"]
        report["method"] = "identity"
        return Calibrator(method="identity", meta=report), report

    full.meta = dict(report)
    return full, report
