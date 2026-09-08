"""base — 元件模型基类与残差约定。

统一隐式形式：0 = f(m1, m2, …, p1, p2, …)

方程计数（保证方程组方阵）:
  每个端口 1 个未知量 ṁᵢ + 每个端口 1 个方程
  二口元件 = 2 方程：f1 连续性(ṁ1+ṁ2) + f2 压力-流量特性

符号约定（全网络统一，元件作者必须遵守）:
  ṁᵢ > 0 : 流体经由端口 i 流【入】组件（各口独立带号，不预设主方向）
  p1, p2 : 各口所连节点的总压（内部节点取自解向量 x，边界节点取 ctx）
  节点连续性因此退化为 Σᵢ ṁᵢ = 0（挂在该节点的所有口求和，无需方向标志）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import numpy as np


class ElementModel(ABC):
    """元件物理模型：给定 Comp 的几何/物性参数，提供残差块。

    子类按 ElemType 实现（orifice.py / seal.py / pipe.py …）。
    一个实例服务一个 Comp；残差/雅可比的组装见 pysas.assembly。
    """

    #: 对应的 ElemType，由子类声明
    elem_type: int = -1

    def __init__(self, comp):
        self.comp = comp

    # ---------- 未知量排布 ----------
    # 全局解向量 x 的排布（由 assembly 层统一定义，元件层只认局部下标）:
    #   x = [p0_1 … p0_Nint | ṁ_1 … ṁ_M]   （M = 全网端口总数）
    # 元件的局部未知量下标由 assembly 注入（set_indices），元件不自己找。

    def set_indices(self, p_node_ids: Sequence[int], m_port_ids: Sequence[int]):
        """assembly 建索引时调用：p_node_ids[i] 是 ports[i] 所连【内部】节点
        的 p0 在 x 里的下标（边界节点给 -1）；m_port_ids[i] 是 ports[i] 的
        ṁ 在 x 里的下标。另存各口 node_id 供查边界值。"""
        self._p_idx = list(p_node_ids)
        self._m_idx = list(m_port_ids)
        self._node_ids = [port.node_id for port in self.comp.ports]

    @property
    def n_equations(self) -> int:
        """该元件贡献的方程数 = n_ports（每口 1 个连续性或特性方程，
        由 residual 的排布决定）。二口元件 = 2。"""
        return len(self.comp.ports)

    @abstractmethod
    def residual(self, x: np.ndarray, ctx: "SolveContext") -> np.ndarray:
        """返回本元件的残差块（长度 n_equations = n_ports）。
        x 是全局解向量；ctx 提供边界 p0/T0、物性(R, γ)、时间等。
        p0 取值助手：self._p(x, ctx, i) 返回第 i 口的节点总压。"""

    def _p(self, x, ctx, i: int) -> float:
        """第 i 口所连节点的总压：内部节点取 x，边界节点取 ctx。"""
        idx = self._p_idx[i]
        return x[idx] if idx >= 0 else ctx.boundary_p0[self._node_ids[i]]

    def _T0_of(self, ctx, i: int) -> float:
        """第 i 口所连节点的总温：边界取 ctx，内部回退 T0_default。
        （能量方程接入后，内部节点 T0 将成为未知量，此助手届时改读解向量。）"""
        return ctx.boundary_T0.get(self._node_ids[i], ctx.T0_default)

    # 解析雅可比可选：不实现则 assembly 用差分（离散牛顿天然支持）。
    def jacobian(self, x: np.ndarray, ctx) -> np.ndarray:
        raise NotImplementedError(
            f"{type(self).__name__} 未提供解析雅可比，请用离散牛顿（差分）")


class SolveContext:
    """求解上下文：元件方程需要但不在解向量里的量。"""

    def __init__(self):
        self.boundary_p0 = {}      # node_id → 边界总压 Pa（常数）
        self.boundary_T0 = {}      # node_id → 边界总温 K
        self.T0_default = 288.15   # 无边界信息时的默认总温 K（能量方程接入前的过渡）
        self.gas_R = 287.05        # 气体常数 J/(kg·K)
        self.gamma = 1.4           # 比热比
        self.mu = 1.8e-5           # 动力粘度 Pa·s（管摩擦 Re 用）
        self.time = 0.0            # 当前物理时间 s（非定常推进用）
        self.dt = 0.0              # 当前时间步长 s（0 = 定常）
