"""heater — 发热元件（两口，恒功率热源）。

params = [q W]（>0 加热；负值即冷却）

隐式形式（两口，每口 1 方程）:
  f1 = ṁ1 + ṁ2          连续性（流量纲）
  f2 = p2 − p1           零压降（热源不产生流体动量效应；压力纲）

能量去哪了：不在本元件残差里——heat_input() 报 q，由 assembly 的
节点能量平衡把温升 q/(ṁ·cp) 并入注入项 T_out（见 system.py ②）。
这样 q 只计一次、只落在解出流向的真正下游节点（旧静态挂点表方案
因双倍计账+上游污染已废弃，2026-09-20）。

物理约定：恒功率（不管流量多少都注入 q 瓦）；ṁ→0 时温升物理发散
（热量无人带走），数值上由 assembly 的 ε_q 兜底封顶——收敛后若该
元件流量近零应警告用户（M6 报表项）。
"""

from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel


class HeaterModel(ElementModel):
    """恒功率发热元件：零压降直通 + heat_input 报 q。"""
    elem_type = 8  # ElemType.HEATER

    def __init__(self, comp):
        super().__init__(comp)
        self.q_spec = comp.params[0]

    @property
    def row_units(self) -> list[int]:
        return [0, 1]  # f1 连续性=流量纲，f2 零压降=压力纲

    def heat_input(self, x, ctx) -> float:
        return self.q_spec

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        m1 = x[self._m_idx[0]]
        p1 = self._total_p(x, ctx, 0)
        p2 = self._total_p(x, ctx, 1)
        return np.array([
            m1 + x[self._m_idx[1]],  # f1 连续性
            p2 - p1,                  # f2 零压降
        ])
