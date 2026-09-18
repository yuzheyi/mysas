"""scaling — 缩放层：变量列缩放 S + 方程行缩放 R → 无量纲方程组。

为什么必须缩放（开发日志·想法 3 的结论，M0 实测）:
  p~1e5 Pa 与 ṁ~1e-2 kg/s 差 7 个量级，直接牛顿有三个坑:
  ① 雅可比列尺度差 7 个量级 → 病态，线性求解误差被放大;
  ② 收敛判据 max|F| 混单位失真（1e-6 Pa 与 1e-6 kg/s 不是一个松紧）;
  ③ 差分步长对小变量是绝对步长 → 离散牛顿的 ε 失真。
  scipy lm 内部自带对角缩放，M0 侥幸能用；自研内核必须自己做。

变换（x = S·x̃，F̃ = R·F，J̃ = R·J·S，缩放坐标全 O(1)）:
  列缩放 S = diag(p_ref…, p_ref | m_ref, …, m_ref)  前 N 列压力、后 M 列流量
  行缩放 R = diag(…):  流量行 1/m_ref（kg/s）；PRESSURE_BOUNDARY 行 1/p_ref（Pa）
                       （2026-09-15 行分档：边界元件化后残差行不再单一流量纲，
                         不分档会压力行乘 1e-5 被压没、流量行乘 1e1 被放大）
                       M3 能量行（W）、M4 容腔行（kg）接入时在此扩展分档。

参考量自估（免用户输入，物理量纲天然合理）:
  p_ref = max(压力边界元件 p0_spec)   网络压力的天然上限尺度（无 → 1e5 Pa）
  m_ref = max_口(壅塞孔板容量流量)  A·p_ref/√T0·√(γ/R)·(2/(γ+1))^{(γ+1)/(2(γ−1))}
        每口面积在 p_ref 下完全壅塞的流量上限（Cd=1 的物理天花板），取全网最大口。
        流量边界元件的 |ṁ_spec| 也纳入估计（规定流量不该超过物理天花板）。
        缩放只要求量级正确不要求精确——两管算例自估 0.156 kg/s vs 真解
        0.258 kg/s（差 60%，量级一致），条件数改善照样成立。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def _choked_orifice_flow(area: float, p0: float, T0: float,
                         R: float, gamma: float) -> float:
    """壅塞孔板流量上限：面积 area 在上游总压 p0 下的物理天花板（Cd=1）。"""
    if area <= 0.0 or p0 <= 0.0:
        return 0.0
    return (area * p0 / np.sqrt(T0) * np.sqrt(gamma / R)
            * (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0))))


@dataclass
class Scaling:
    """缩放层：列缩放 S（变量）与行缩放 R（方程）的对角参考量。"""

    p_ref: float           # 压力变量缩放 Pa
    m_ref: float           # 流量变量 / 残差行缩放 kg/s
    n: int                 # 方程组规模 N + M
    n_interior: int        # 前 n_interior 个变量是节点压力（统一后=全部节点）
    row_is_pressure: list = field(default_factory=list)
                           # 残差行量纲标记（True=压力 Pa，False=流量 kg/s）

    # ---------- S / R ----------
    @property
    def col_scales(self) -> np.ndarray:
        """S 对角元：x = S·x̃（压力列 p_ref，流量列 m_ref）。"""
        s = np.empty(self.n)
        s[: self.n_interior] = self.p_ref
        s[self.n_interior:] = self.m_ref
        return s

    @property
    def row_scales(self) -> np.ndarray:
        """R 对角元：F̃ = R·F（流量行 1/m_ref，压力行 1/p_ref——行分档）。"""
        r = np.full(self.n, 1.0 / self.m_ref)
        for i, is_p in enumerate(self.row_is_pressure):
            if is_p:
                r[i] = 1.0 / self.p_ref
        return r

    # ---------- 坐标变换 ----------
    def to_scaled(self, x: np.ndarray) -> np.ndarray:
        """原始坐标 → 缩放坐标 x̃ = S⁻¹x。"""
        return x / self.col_scales

    def to_raw(self, x_tilde: np.ndarray) -> np.ndarray:
        """缩放坐标 → 原始坐标 x = S·x̃。"""
        return x_tilde * self.col_scales


def make_scaling(system, ctx, p_ref: float | None = None,
                 m_ref: float | None = None) -> Scaling:
    """从网络与物性自估参考量（显式给定 p_ref/m_ref 则覆盖自估）。

    p_ref 取压力边界元件 p0_spec 的最大值；m_ref 取全网最大口壅塞容量
    与流量边界 |ṁ_spec| 的最大值。行量纲标记从 system.row_is_pressure 取。
    """
    from pysas.datamodel import ElemType

    pb = [c.params[0] for c in system.net.comps
          if c.elem_type == ElemType.PRESSURE_BOUNDARY]
    if p_ref is None:
        pressures = [v for v in pb if v > 0.0]
        p_ref = max(pressures) if pressures else 1.0e5

    if m_ref is None:
        T_ref = max([*ctx.boundary_T0.values(), ctx.T0_default])
        caps = [_choked_orifice_flow(port.area, p_ref, T_ref,
                                     ctx.gas_R, ctx.gamma)
                for comp in system.net.comps for port in comp.ports]
        ms = [abs(c.params[0]) for c in system.net.comps
              if c.elem_type == ElemType.MASS_SOURCE]  # 规定流量纳入估计
        m_ref = max([*caps, *ms], default=0.0)
        if m_ref <= 0.0:
            m_ref = 1.0  # 全零面积等退化拓扑的兑底（缩放失去意义但不崩溃）

    return Scaling(p_ref=p_ref, m_ref=m_ref,
                   n=system.n, n_interior=system.n_interior,
                   row_is_pressure=list(system.row_is_pressure))


class ScaledProblem:
    """(system, ctx, scaling) → 缩放坐标下的纯函数问题。

    牛顿内核只认残差回调 residual(x̃) → F̃；雅可比由 discrete.py 对 F̃
    直接差分，链式自动等于 R·J·S——缩放正确性由构造保证，不需单独推导。
    """

    def __init__(self, system, ctx, scaling: Scaling):
        self.system = system
        self.ctx = ctx
        self.scaling = scaling

    def residual(self, x_tilde: np.ndarray) -> np.ndarray:
        """缩放坐标残差 F̃ = R·F(S·x̃)（迭代与判据都用它）。"""
        F = self.system.residual(self.scaling.to_raw(x_tilde), self.ctx)
        return F * self.scaling.row_scales

    def residual_raw(self, x_tilde: np.ndarray) -> np.ndarray:
        """原始量纲残差 F(S·x̃)（核对/报告用，不参与迭代）。"""
        return self.system.residual(self.scaling.to_raw(x_tilde), self.ctx)
