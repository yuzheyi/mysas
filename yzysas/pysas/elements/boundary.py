"""boundary — 边界条件元件（单口对偶对，2026-09-15 边界统一元件化）。

设计动机（开发计划 M5 提前实施）:
  此前边界节点不进 x，压力存 ctx.boundary_p0 由 _p() 特判读取（消元式）。
  流量边界无法这样表达（节点 p 必须成为未知量），为免"一半特判一半元件"
  两种心智模型并存，边界条件统一升格为元件——所有节点一视同仁进 x，
  assembly/缩放层不再有 boundary/interior 分叉。

对偶结构（残差行不含的量恰好互换）:
                        规定的量        解出的量        残差行不含
  PressureBoundary      p_node          ṁ_port          ṁ_port
  MassSource            ṁ_port          p_node          p_node

账目：边界元件 1 口 = +1 未知(ṁ) +1 方程；其节点 p 进 x = +1 未知
      +1 连续性方程 → 方阵保持（见 elements/base.py 方程计数约定）。

适定性（assembly 组装时断言）:
  网络至少挂 1 个 PressureBoundary——全流量边界下管网只感知压差，
  绝对压力水平浮动，J 有零空间（不可压精确奇异，可压近奇异）。

物理约定:
  ṁ > 0 = 流入组件（全网统一符号）→ MassSource 注入时其端口流量为负
  （流体经该口流出源元件、进入网络）。T0_spec 是源的总温（M3 能量
  方程的入口条件，等温冻结期直接用作该节点的特征温度）。
"""
from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel


class PressureBoundaryModel(ElementModel):
    """压力边界：规定所连节点的总压。params=[p0_spec, T0_spec]

    T0_spec 语义（2026-09-25 退锚重构，想法 22）：不再锚定节点 T，
    只作 T_supply——倒流（流体从边界进入网络）时能量方程的注入温度；
    正向出流时节点的 T 是未知量，由能量行解出真实出口温度。
    旧硬锚定把出口节点 T 钉在无意义的 T_spec 上（报表撒谎 +
    经 T̄_anchor 正则泄漏上游 ~ε/ṁ 量级），已废弃。
    """
    elem_type = 5  # ElemType.PRESSURE_BOUNDARY
    # 锚定能力由 anchor_P_values 非空推导（报值即能力）；T 不锚

    def __init__(self, comp):
        super().__init__(comp)
        self.p0_spec, self.T0_spec = comp.params

    def anchor_P_values(self) -> dict[int, float]:
        return {self._node_ids[0]: self.p0_spec}

    def T_supply(self, ctx) -> float:
        return self.T0_spec

    @property
    def row_units(self) -> list[int]:
        return [1]  # p_node − p0_spec 是压力纲 Pa

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        """F = p_node − p0_spec（压力量纲 → assembly 登记压力行缩放）。"""
        return np.array([self._total_p(x, ctx, 0) - self.p0_spec])


class MassSourceModel(ElementModel):
    """流量边界：规定注入(>0)/抽取(<0)网络的流量。params=[ṁ_spec, T0_spec]

    T0_spec 同 PB 退锚（想法 22）：T_supply 而非锚定——注入流温度经
    能量方程注入项进入节点 T（单源节点能量行退化为 T_n=T_supply，
    与旧锚定同解；多源异温节点解出混合温度，优于旧字典覆盖的任意性）。
    """
    elem_type = 6  # ElemType.MASS_SOURCE

    def __init__(self, comp):
        super().__init__(comp)
        self.m_spec, self.T0_spec = comp.params

    def T_supply(self, ctx) -> float:
        return self.T0_spec

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        """F = ṁ_port + ṁ_spec（ṁ>0=流入组件；注入 → 端口流量 = −ṁ_spec）。"""
        return np.array([x[self._m_idx[0]] + self.m_spec])
