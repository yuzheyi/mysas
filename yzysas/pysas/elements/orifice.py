"""orifice — 孔板/喷嘴元件（最简单的 2 口元件，验证全流程的标尺）。

params = [面积比, 流量系数 Cd]（见 ElemType.ORIFICE docstring）

隐式形式 0 = f(m1, m2, p1, p2)（每口 1 方程，共 2 个）:
  f1 = ṁ1 + ṁ2                          （连续性：流入为正 → 净流入为零）
  f2 = ṁ_k − ṁ_ideal(p_hi, p_lo, T_hi)   （特性：k = 低压侧口，流入方向为正）

可压喷嘴公式（上游 = 高压侧）:
  亚临界  ṁ = Cd·A·p01/√T01 · √(2γ/(R(γ−1))) · β^{1/γ} · √(1 − β^{(γ−1)/γ})
  超临界  ṁ = Cd·A·p01/√T01 · √(γ/R) · (2/(γ+1))^{(γ+1)/(2(γ−1))}
  β = p02/p01
"""
from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel


class OrificeModel(ElementModel):
    elem_type = 0  # ElemType.ORIFICE

    def __init__(self, comp):
        super().__init__(comp)
        area_ratio, self.cd = comp.params
        self.area = area_ratio * comp.ports[0].area  # 有效流通面积

    def _ideal_mass_flow(self, p01, p02, T01, R, gamma):
        """理想流量，恒为正，方向 = 高压侧 → 低压侧。
        β 夹在 [1e-8, 1]：防止迭代跑到负压区时分数幂出 NaN。"""
        p01 = max(p01, 1.0)
        beta = min(max(p02 / p01, 1.0e-8), 1.0)
        beta_crit = (2.0 / (gamma + 1.0)) ** (gamma / (gamma - 1.0))
        factor = self.cd * self.area * p01 / np.sqrt(T01)
        if beta <= beta_crit:  # 超临界
            return factor * np.sqrt(gamma / R) \
                * (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
        return factor * np.sqrt(2.0 * gamma / (R * (gamma - 1.0))) \
            * beta ** (1.0 / gamma) \
            * np.sqrt(max(1.0 - beta ** ((gamma - 1.0) / gamma), 0.0))

    def _T0_of(self, ctx, i: int) -> float:
        """第 i 口上游总温（先用 ctx 边界/默认，能量方程接入后细化）。"""
        return ctx.boundary_T0.get(self._node_ids[i], ctx.T0_default)

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        p1 = self._p(x, ctx, 0)
        p2 = self._p(x, ctx, 1)
        m1 = x[self._m_idx[0]]
        m2 = x[self._m_idx[1]]

        # 高压侧为上游：流体从高压侧口流入组件（ṁ_high > 0）
        if p1 >= p2:
            k, p_hi, p_lo, T_hi = 0, p1, p2, self._T0_of(ctx, 0)
        else:
            k, p_hi, p_lo, T_hi = 1, p2, p1, self._T0_of(ctx, 1)

        m_ideal = self._ideal_mass_flow(p_hi, p_lo, T_hi, ctx.gas_R, ctx.gamma)
        return np.array([
            m1 + m2,                       # f1 连续性
            x[self._m_idx[k]] - m_ideal,   # f2 特性：高压口流入 = +ṁ_ideal
        ])
