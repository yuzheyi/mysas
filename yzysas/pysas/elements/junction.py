"""junction — 理想三通（三口，零压差绝热混合）。

params = []（理想件无参数；汇合损失/非等压分配等真实物理 M5 扩展，
届时 params 加损失系数并覆盖 residual 的压力方程）

隐式形式（三口，每口 1 方程，共 3 个）:
  f1 = ṁ0 + ṁ1 + ṁ2          连续性（流量纲）
  f2 = p1 − p0                零压差（压力纲）
  f3 = p2 − p0                零压差（压力纲）

能量（port_T_out，三口无默认必须覆盖——见 base.py）:
  出流温度 = 全部进料口的流量加权混合温度 Σṁ_k·T_k / Σṁ_k
  进料/出料集合由解出的 ṁ 符号即时决定：任一口都可能是进料或出料，
  汇合（多进一出）→ 出口得混合温度；分叉（一进多出）→ 各出料口
  同为进口温度（理想分叉不改变温度）；ε 兜底分母防零流量除零。

与"三个支路直接挂一个节点"的等价性（开发日志想法 17）:
  理想绝热混合的全部物理就是能量守恒，节点能量平衡也是能量守恒——
  T_mix 在节点方程的乘积里被约掉，两种建模逐项相同。本元件的
  存在意义是将来承载非理想物理（汇合损失加热、不完全混合效率、
  非等压分配）——那些写进 residual/port_T_out，节点方程零改动。
"""

from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel

#: 零流量兜底 kg/s（混合公式分母；ṁ→0 时温度回退锚温均值方向）
EPS_MIX = 1.0e-9


class JunctionModel(ElementModel):
    """理想三通：零压差 + 绝热完全混合。"""
    elem_type = 9  # ElemType.JUNCTION

    @property
    def row_units(self) -> list[int]:
        return [0, 1, 1]  # f1 连续性=流量纲，f2/f3 零压差=压力纲

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        m0 = x[self._m_idx[0]]
        p0 = self._total_p(x, ctx, 0)
        p1 = self._total_p(x, ctx, 1)
        p2 = self._total_p(x, ctx, 2)
        return np.array([
            m0 + x[self._m_idx[1]] + x[self._m_idx[2]],  # f1 连续性
            p1 - p0,                                     # f2 零压差
            p2 - p0,                                     # f3 零压差
        ])

    def port_T_out(self, x, ctx, j: int) -> float:
        """口 j 的出流温度 = 全部进料口的流量加权混合温度。

        进料集合由当前解出的 ṁ 符号决定（ṁ_k > 0 = 流入元件）；
        分叉工况下所有出料口自然得到同一个混合温度 = 进口温度。
        ε 兜底分母：全部口零流量时返回 ~0/ε 退化值，由节点 ε 正则
        拉回锚温均值（与两口件 EPS_Q 同族防御）。
        """
        m_in, e_in = EPS_MIX, 0.0
        for k in range(3):
            m_k = x[self._m_idx[k]]
            if m_k > 0.0:
                m_in += m_k
                e_in += m_k * self._total_t(x, ctx, k)
        return e_in / m_in
