"""runtime — 代理模型运行时：无量纲特性 Φ(PR) 的插值/推理后端。

数学契约（训练侧 tools/surrogate 与本文件共享的唯一真源）:
  ṁ = C_ref · p_hi/√T0 · Φ(PR)
  PR = p_lo/p_hi ∈ (0, 1]，C_ref = A_ref·√(γ/R)·(2/(γ+1))^((γ+1)/(2(γ−1)))
  —— C_ref 与 orifice 壅塞流量系数同源（元件 flow.py 按运行时 gas 计算，
     R/A/T0 依赖被物理形式析出，模型文件不携带物性数值）
  Φ 性质（两后端都必须遵守，构造期校验）:
  单调不增；Φ(1) = 0（零压差零流量）；PR → 0⁺ 平台 = Φ_max（壅塞）

后端:
  TableRuntime  npz 一维表 + PCHIP 单调保形三次插值（纯 numpy 自研，
                ~60 行；scipy.interpolate.PchipInterpolator 同算法，
                训练侧用它对拍——元件路径不引 scipy，表后端零依赖）
  OnnxRuntime   onnxruntime 推理单输入 PR → Φ（lazy import；缺装时抛
                ImportError 附 pip 指引——表后端是零依赖主路径）

设计取舍（开发日志·想法 8 "NN 只猜、物理兜底" 的运行侧落点）:
  模型只学一维无量纲函数 Φ——p/√T/A 的依赖被物理标度析出，训练无需
  扫描 (p, T, A) 维度；单调性由数据+构造保证，特性行对 p_lo 单调 →
  牛顿解唯一性同 orifice。
"""
from __future__ import annotations

import numpy as np


# ---------- PCHIP 单调保形三次插值（纯 numpy 自研） ----------
def _pchip_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """节点导数（Fritsch–Carlson 加权调和平均；单调数据的 PCHIP 保证
    插值单调——正是 Φ 特性需要的保形性质）。"""
    h = np.diff(x)
    d = np.diff(y) / h
    n = x.size
    m = np.zeros(n)
    for k in range(1, n - 1):
        if d[k - 1] == 0.0 or d[k] == 0.0 or d[k - 1] * d[k] <= 0.0:
            m[k] = 0.0                       # 极值/平台 → 零导数（保形）
            continue
        w1 = 2.0 * h[k] + h[k - 1]
        w2 = h[k] + 2.0 * h[k - 1]
        m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])   # 加权调和平均
    m[0] = _pchip_edge(h[0], h[1], d[0], d[1])
    m[-1] = _pchip_edge(h[-1], h[-2], d[-1], d[-2])
    return m


def _pchip_edge(h0: float, h1: float, m0: float, m1: float) -> float:
    """端点单侧三点估计 + 保形钳位（scipy _edge_case 的标量化）。"""
    d = ((2.0 * h0 + h1) * m0 - h0 * m1) / (h0 + h1)
    if np.sign(d) != np.sign(m0):
        return 0.0
    if np.sign(m0) != np.sign(m1) and abs(d) > 3.0 * abs(m0):
        return 3.0 * m0
    return d


def _pchip_eval(x, y, m, xv: float) -> float:
    """Hermite 三次求值（标量；区间由 searchsorted 定位）。"""
    k = int(np.searchsorted(x, xv, side="right")) - 1
    k = min(max(k, 0), x.size - 2)
    h = x[k + 1] - x[k]
    t = (xv - x[k]) / h
    t2 = t * t
    t3 = t2 * t
    return float(
        y[k] * (2.0 * t3 - 3.0 * t2 + 1.0)
        + h * m[k] * (t3 - 2.0 * t2 + t)
        + y[k + 1] * (-2.0 * t3 + 3.0 * t2)
        + h * m[k + 1] * (t3 - t2))


class TableRuntime:
    """npz 一维 Φ(PR) 表 + PCHIP 单调保形插值。

    网格约定: pr_grid 升序 [pr_min … 1.0]（末点必须为 1，构造期校验，
    否则零压差处 Φ 不为零）；phi_grid 对应单调不增。PR < pr_min 一段是
    壅塞平台——求值时夹到网格左端点（平台值 Φ_max），不做外推（与
    orifice 超临界分支同语义）。校验全在构造期：坏文件在 build_models
    即报，不进牛顿迭代。
    """

    backend = "table"

    def __init__(self, pr_grid, phi_grid, pr_crit=None, meta: dict | None = None):
        pr = np.asarray(pr_grid, dtype=float)
        phi = np.asarray(phi_grid, dtype=float)
        if pr.ndim != 1 or phi.ndim != 1 or pr.size != phi.size:
            raise ValueError(f"表模型数组形状不合法: pr{pr.shape}"
                             f" phi{phi.shape}（应为一维等长）")
        if pr.size < 4:
            raise ValueError(f"表模型点数过少: {pr.size}（PCHIP 至少 4 点）")
        if not np.all(np.isfinite(pr)) or not np.all(np.isfinite(phi)):
            raise ValueError("表模型含 NaN/Inf")
        if np.any(np.diff(pr) <= 0.0):
            raise ValueError("pr_grid 必须严格升序")
        if pr[0] <= 0.0 or pr[-1] < 1.0 - 1.0e-9:
            raise ValueError(f"pr_grid 范围应覆盖 (0, 1]：实际 "
                             f"[{pr[0]:.4g}, {pr[-1]:.6g}]（末点须为 1）")
        if np.any(phi < -1.0e-12):
            raise ValueError(f"phi_grid 含负值（min={phi.min():.3e}）——Φ ≥ 0")
        phi = np.maximum(phi, 0.0)
        if np.any(np.diff(phi) > 1.0e-10):
            raise ValueError("phi_grid 必须单调不增（PCHIP 保形前提；"
                             "训练侧应做单调投影 np.minimum.accumulate）")
        phi = np.minimum.accumulate(phi)   # 容差内微升拉平（保险）

        pc = float(pr[0] if pr_crit is None else pr_crit)
        if not (pr[0] - 1.0e-12 <= pc <= pr[-1] + 1.0e-12):
            raise ValueError(f"pr_crit={pc:.4g} 不在网格范围内")

        self.pr = pr
        self.phi = phi
        self.pr_crit = pc
        self.phi_max = float(phi[0])       # 壅塞平台值（升序网格左端）
        self.meta = dict(meta or {})
        self._m = _pchip_slopes(pr, phi)

    def flow_coefficient(self, pr: float) -> float:
        """Φ(PR)——PCHIP 保形插值；PR 夹到网格范围（左端=壅塞平台）。"""
        return _pchip_eval(self.pr, self.phi, self._m,
                           min(max(pr, self.pr[0]), self.pr[-1]))


class OnnxRuntime:
    """ONNX 单输入 (PR)→Φ 推理后端（lazy import onnxruntime）。

    输出夹到 [0, Φ_max]：MLP 拟合单调数据的微小非单调摆动不进残差
    （Φ_max 由初始化时 PR∈[0,1] 网格上的最大原始输出自报）。元数据
    （gamma_train 等）从同名 .meta.json 边车读取（train.py 写出）。
    """

    backend = "onnx"

    def __init__(self, model_path: str, meta: dict | None = None):
        try:
            import onnxruntime as _ort
        except ImportError as e:
            raise ImportError(
                "ONNX 后端需要 onnxruntime（未安装）——pip install "
                "onnxruntime，或改用零依赖的表后端（.npz）") from e
        self.meta = dict(meta or {})
        self.sess = _ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"])
        inp = self.sess.get_inputs()[0]
        if list(inp.shape)[-1] != 1:
            raise ValueError(f"ONNX 模型输入形状 {inp.shape} 不符"
                             "（应为 [None, 1]：单输入 PR）")
        self._input = inp.name
        # 壅塞平台自报：PR∈[0,1] 扫描最大原始输出（兼作夹上界）
        probe = np.linspace(0.0, 1.0, 33)
        raw = self._raw(probe)
        self.phi_max = float(raw.max())
        self.pr_crit = float(self.meta.get(
            "pr_crit", float(probe[int(np.argmax(raw))])))

    def _raw(self, pr) -> np.ndarray:
        out = self.sess.run([self.sess.get_outputs()[0].name],
                            {self._input: np.asarray(
                                pr, dtype=np.float32).reshape(-1, 1)})
        return np.asarray(out[0], dtype=float).ravel()

    def flow_coefficient(self, pr: float) -> float:
        v = float(self._raw(np.array([min(max(pr, 0.0), 1.0)]))[0])
        return min(max(v, 0.0), self.phi_max)
