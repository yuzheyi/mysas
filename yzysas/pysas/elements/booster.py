"""booster — 升压元件（风机/泵，环路驱动物）。

params = [压力升 Δp Pa, T0_spec K]

隐式形式（两口，每口 1 方程）:
  f1 = ṁ1 + ṁ2                    连续性（流量纲）
  f2 = p2 − p1 − Δp                特性：出口总压 = 进口总压 + Δp（压力纲）

约定口 1 为进口（低压）、口 2 为出口（高压）——升压方向由端口顺序定义，
逆着接（ṁ 从口 2 流入）时元件表现为降压 Δp。
"""

from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel


class BoosterModel(ElementModel):
    """理想升压元件：恒定压力升（原型级；压升-流量特性线 M5 扩展）。"""
    elem_type = 7  # ElemType.BOOSTER

    def __init__(self, comp):
        super().__init__(comp)
        self.dp_spec, self.T0_spec = comp.params

    @property
    def row_is_pressure(self) -> list[bool]:
        return [False, True]  # f1 连续性=流量纲，f2 特性=压力纲

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        p1 = self._p(x, ctx, 0)
        p2 = self._p(x, ctx, 1)
        m1 = x[self._m_idx[0]]
        m2 = x[self._m_idx[1]]
        return np.array([
            m1 + m2,                    # f1 连续性
            p2 - p1 - self.dp_spec,     # f2 升压特性（口1→口2 升 Δp）
        ])
