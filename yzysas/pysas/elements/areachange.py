"""areachange — 突扩/突缩面积变化元件（两口，总压损失型）。

params = [ζ]（总压损失系数，参考速度 = 小截面速度；ζ > 0）

物理模型（Borda-Carnot 类总压损失，不可压形式）:
  p01 − p02 = ζ · ½ · ρ · V_ref² ,   V_ref = ṁ/(ρ·A_ref)
  A_ref = min(A1, A2) —— 突扩的 Borda-Carnot (1−A1/A2)² 与突缩的
  ~0.4-0.5（Idelchik）都以小截面速度为参考，故 A_ref 取两口面积较小者
  （纯几何量，与流向无关）。

隐式形式（两口，每口 1 方程，与 orifice 同构）:
  f1 = ṁ1 + ṁ2                      连续性
  f2 = ṁ_k − ṁ_ideal                 特性（k = 高压侧口，流入为正）
  ṁ_ideal = A_ref · √(2·ρ_up·Δp0/ζ)   Δp0 = p0k − p0_other ≥ 0

设计要点（"节点总参数 + 元件局部静参数"模式的最小示范，想法 19）:
  - 面积属于 Port：两口面积可不同，元件消费几何，不产生节点量；
  - 损失发生在元件自己的小截面（局部速度 V_ref），不碰节点静压——
    节点静压 ≠ 任何元件的反算值，节点只存总压；
  - ρ 取高压侧总态（与 orifice 的 T_hi 同款上游口径）；
  - f2 对解变量 ṁ 系数 1（零流量牛顿友好；√Δp0 在 Δp0→0 的奇异
    与 orifice β→1 同族，阻尼线搜索兜住）；
  - 绝热直通：温度零代码自动接入 port_T_out 两口默认（另一口温度，
    q=0），验证算例 W3 的 T 逐位传递即证。

局限/升级路（M5+，不预做）:
  - 单一 ζ 不分方向（突扩/突缩物理上不同）——按 sign(ṁ) 切换 ζ_eff
    是残差内一行的事，等有标定数据再开；
  - 高速时 ρ 应随当地静参数变化——届时在特性闭包内用
    total_to_static 恢复小截面静参数做可压修正（两段式启动，想法 19）。
"""

from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel


class AreaChangeModel(ElementModel):
    """突扩/突缩元件：两口面积不同，总压损失 = ζ·½ρV_min²。"""
    elem_type = 10  # ElemType.AREA_CHANGE

    def __init__(self, comp):
        super().__init__(comp)
        self.zeta = comp.params[0]
        if self.zeta <= 0.0:
            raise ValueError(
                f"AREA_CHANGE params[0] ζ={self.zeta} 须 > 0"
                f"（ζ=0 无损失且使特性除零）")
        areas = [p.area for p in comp.ports]
        if min(areas) <= 0.0:
            raise ValueError(f"AREA_CHANGE 两口面积均须 > 0: {areas}")
        self.a_ref = min(areas)   # 损失参考截面 = 小面积口（几何量）

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        p1 = self._total_p(x, ctx, 0)
        p2 = self._total_p(x, ctx, 1)

        # 高压侧为上游（与 orifice 同款 k 选择）
        if p1 >= p2:
            k, p_hi, p_lo = 0, p1, p2
        else:
            k, p_hi, p_lo = 1, p2, p1

        T_hi = self._total_t(x, ctx, k)
        rho_up = p_hi / (ctx.gas.R * T_hi)     # 上游总态密度（不可压口径）
        dp0 = max(p_hi - p_lo, 0.0)
        m_ideal = self.a_ref * np.sqrt(2.0 * rho_up * dp0 / self.zeta)
        return np.array([
            x[self._m_idx[0]] + x[self._m_idx[1]],   # f1 连续性
            x[self._m_idx[k]] - m_ideal,              # f2 特性：高压口流入
        ])
