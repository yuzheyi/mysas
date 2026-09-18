"""pipe — 圆柱直管元件（Darcy 摩阻，层流/过渡/湍流三段流量特性）。

params = [长度 L m, 直径 D m, 粗糙度 eps m]（见 ElemType.PIPE docstring）

隐式形式 0 = f(m1, m2, p1, p2)（每口 1 方程，共 2 个）:
  f1 = ṁ1 + ṁ2                       （连续性）
  f2 = ṁ1 − ṁ_pipe(p1, p2, T)         （特性，ṁ1>0: 流体经口1流入 → 0→1 方向）

Darcy-Weisbach:  Δp = (f·L/D) · ρv²/2,  v = ṁ/(ρA)
  →  湍流  ṁ = ρA·√( 2ρΔp·D / (f·L) )   （f 依赖 Re → 依赖 ṁ 本身）
  →  层流  f = 64/Re 代回得线性律（Hagen-Poiseuille）:
          Δp = 32μLv/D²  →  ṁ = Δp·ρA·D²/(32μL)   【无 ṁ 依赖】

为什么层流段必须写显式线性律、不能沿用 "f=64/Re 代入湍流式"：
  后者是 ṁ 的隐式函数，在 ṁ→0 处 ∂ṁ_ideal/∂ṁ_guess ∝ √(1/ṁ) → ∞，
  差分雅可比在此爆炸（M1 坏初值实验实证：默认零流量初值下牛顿方向
  不可用）。线性律根部完全一致，但对 ṁ_guess 的导数恒为 0——雅可比干净。

分档（按 ṁ_guess 的 Re 选取权重 w，根部自动自洽）:
  Re(ṁ) ≤ 2000        层流：纯线性律
  2000 < Re(ṁ) < 4000 过渡：m = (1−w)·线性 + w·湍流（w 线性插值）
  Re(ṁ) ≥ 4000        湍流：Swanee-Jain（显式，免 Colebrook 迭代）
    f = 0.25 / [ log10( ε/(3.7D) + 5.74/Re^0.9 ) ]²
  收敛点自洽：层流根 Re≤2000 → w=0；湍流根 Re≥4000 → w=1；
  过渡带内 w 连续变化，物理上过渡区本就不唯一（经验区间），原型可接受。

注意：管内 ρ 取上游节点 p0/T0 的理想气体密度（短管小压降下足够，
可压修正留给后续）。
"""
from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel

RE_LAM = 2000   # 层流上限 Re（工程惯例 2000~2300，取整便于插值分档）
RE_TURB = 4000  # 过渡区上限 Re


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

    def _re(self, mdot, ctx):
        """雷诺数（|ṁ| 无关符号）。"""
        return abs(mdot) * self.diameter / (ctx.mu * self.area)

    def _friction(self, Re):
        """Swanee-Jain 摩擦系数（仅湍流段调用）。"""
        term = self.roughness / (3.7 * self.diameter) + 5.74 / Re ** 0.9
        return 0.25 / (np.log10(max(term, 1.0e-12)) ** 2)

    # ---------- 特性 ----------
    def mass_flow(self, p_up, p_down, T0, mdot_guess, ctx):
        """给定两端总压，返回管流量（≥0，方向高压→低压）。

        mdot_guess 只用于湍流/过渡段的 f(Re) 估计（Swanee-Jain 对 f 灵敏度
        低，f 误差 5% 只带来 ṁ 误差 ~2.5%）；层流段完全不用它。
        """
        dp = p_up - p_down
        if dp <= 0.0:
            return 0.0
        rho = self._density(p_up, T0, ctx)
        A, D, L = self.area, self.diameter, self.length

        m_lam = rho * dp * A * D * D / (32.0 * ctx.mu * L)  # Hagen-Poiseuille
        re_guess = self._re(mdot_guess, ctx)
        if re_guess <= RE_LAM:
            return m_lam

        f = self._friction(re_guess)
        m_turb = A * np.sqrt(2.0 * rho * dp * D / (f * L))
        w = min(max((re_guess - RE_LAM) / (RE_TURB - RE_LAM), 0.0), 1.0)
        return (1.0 - w) * m_lam + w * m_turb

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
