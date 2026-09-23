"""booster — 升压/压力源元件（锚定型，两口，2026-09-18 重定义）。

params = [p_in_spec, p_out_spec] 或 [p_in_spec, p_out_spec, T_spec]
（第三参数可选：环路温度锚——锚定型无上游节点，闭式环能量平衡
需要至少一个绝对温度源，T_spec 同时供两口；缺省则不锚温，
温度由网络上下文定）

隐式形式（两口，每口 1 方程）:
  f1 = p_in  − p_in_spec     进口节点压力直接锚定（压力纲）
  f2 = p_out − p_out_spec    出口节点压力直接锚定（压力纲）

与 Δp 型（p_out − p_in = Δp，2026-09-18 前旧版）的本质区别:
  - 锚定能力：两个绝对压力方程直接消除压力零空间——纯 booster 环路
    不再需要额外 PRESSURE_BOUNDARY（assembly 适定性断言已同步扩展）
  - 无内部连续性：m1、m2 不被元件强制相等，由网络约束——闭环内
    处处相等（环上连续性链），开式网络两侧独立（等效无限气库）
  - 物理语义：运行点边界源（进出口压力钉死的"无限功率"源），
    流量由网络解出；流量型运行点源（压比+流量）另立类型

口序约定：口 0 = 进口（低压侧），口 1 = 出口（高压侧）。
"""

from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel


class BoosterModel(ElementModel):
    """锚定型压力源：两口各自规定所连节点的绝对总压。"""
    elem_type = 7  # ElemType.BOOSTER
    # 锚定能力由 anchor_P_values/anchor_T_values 非空推导（报值即能力）

    def __init__(self, comp):
        super().__init__(comp)
        self.p_in_spec, self.p_out_spec = comp.params[:2]
        self.T_spec = comp.params[2] if len(comp.params) > 2 else None

    def anchor_P_values(self) -> dict[int, float]:
        return {self.comp.ports[0].node_id: self.p_in_spec,
                self.comp.ports[1].node_id: self.p_out_spec}

    def anchor_T_values(self) -> dict[int, float]:
        return ({self.comp.ports[0].node_id: self.T_spec,
                 self.comp.ports[1].node_id: self.T_spec}
                if self.T_spec is not None else {})

    def T_supply(self, ctx) -> float:
        return self.T_spec if self.T_spec is not None else ctx.T0_default

    @property
    def row_units(self) -> list[int]:
        return [1, 1]  # 两行全是压力纲 Pa

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        return np.array([
            self._total_p(x, ctx, 0) - self.p_in_spec,   # f1 进口锚定
            self._total_p(x, ctx, 1) - self.p_out_spec,  # f2 出口锚定
        ])
