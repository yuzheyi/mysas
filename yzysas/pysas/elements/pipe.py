"""pipe — 圆柱直管元件（Darcy 摩擦损失，不可压流量-压差特性）。

params = [长度 L m, 直径 D m, 粗糙度 eps m]（见 ElemType.PIPE docstring）

隐式形式 0 = f(m1, m2, p1, p2)（每口 1 方程，共 2 个）:
  f1 = ṁ1 + ṁ2                       （连续性）
  f2 = ṁ1 − ṁ_pipe(p1, p2, T)         （特性，ṁ1>0: 流体经口1流入 → 0→1 方向）

Darcy-Weisbach:  Δp = (f·L/D) · ρv²/2,  v = ṁ/(ρA)
  →  ṁ = ρA · √( 2ρΔp·D / (f·L) )   （取正根，方向 = 高压→低压）

摩擦系数 Swanee-Jain（显式，免 Colebrook 迭代）:
  f = 0.25 / [ log10( ε/(3.7D) + 5.74/Re^0.9 ) ]²
  Re = ṁD/(μA)  —— 依赖 ṁ 本身 → 用迭代前的当前 ṁ 估计（残差内自洽）

注意：管内 ρ 取节点压力下的气体密度（可压修正留给后续；当前用
上游节点 p0/T0 的理想气体密度，短管小压降下足够）。
"""
from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel


class PipeModel(ElementModel):
    elem_type = 2  # ElemType.PIPE

    def __init__(self, comp):
        super().__init__(comp)
        self.length, self.diameter, self.roughness = comp.params
        self.area = comp.ports[0].area

    # ---------- 物性 ----------
    def _density(self, p0, T0, ctx):
        """上游总态理想气体密度 kg/m³。"""
        return p0 / (ctx.gas_R * T0)

    def _friction(self, mdot, rho, ctx):
        """Swanee-Jain 摩擦系数（Re 依赖 ṁ，用当前值）。"""
        A = self.area
        Re = abs(mdot) * self.diameter / (ctx.mu * A)
        if Re < 1.0:  # 层流/极低速保护
            return 64.0 / max(Re, 1.0e-3)
        term = self.roughness / (3.7 * self.diameter) + 5.74 / Re ** 0.9
        return 0.25 / (np.log10(max(term, 1.0e-12)) ** 2)

    # ---------- 特性 ----------
    def mass_flow(self, p_up, p_down, T0, mdot_guess, ctx):
        """给定两端总压，返回管流量（≥0，方向高压→低压）。
        f 依赖 Re 依赖 ṁ —— 用 mdot_guess 定摩擦系数，一次估计即可
        （Swanee-Jain 对 f 的灵敏度低，f 误差 5% 只带来 ṁ 误差 ~2.5%）。
        """
        dp = p_up - p_down
        if dp <= 0.0:
            return 0.0
        rho = self._density(p_up, T0, ctx)
        f = self._friction(mdot_guess, rho, ctx)
        return self.area * np.sqrt(2.0 * rho * dp * self.diameter
                                   / (f * self.length))

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        p1 = self._p(x, ctx, 0)
        p2 = self._p(x, ctx, 1)
        m1 = x[self._m_idx[0]]
        m2 = x[self._m_idx[1]]

        # 上游 = 高压侧；流体从高压侧口流入组件（ṁ_high > 0）
        if p1 >= p2:
            T_up = self._T0_of(ctx, 0)
            m_ideal = self.mass_flow(p1, p2, T_up, m1, ctx)
            k = 0  # 高压口编号（0→1 方向流动）
        else:
            T_up = self._T0_of(ctx, 1)
            m_ideal = self.mass_flow(p2, p1, T_up, m2, ctx)
            k = 1

        return np.array([
            m1 + m2,
            x[self._m_idx[k]] - m_ideal,
        ])
