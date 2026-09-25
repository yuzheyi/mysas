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

    #: 能力声明（推导式，不单独设 bool）：anchor_P_values() 返回非空
    #: = 能锚定压力。报值即能力，消除"True+空 dict"的矛盾态；
    #: assembly 适定性断言、default_guess、缩放层全部直接查 dict——
    #: 新元件只覆盖这一个方法，组装层零改动。
    #: （锚温能力已退役，想法 22 完成态：所有节点 T 都是未知量由
    #:   能量方程解出；源温度一律走 T_supply/port_T_out 输运语义。）

    def anchor_P_values(self) -> dict[int, float]:
        """本元件规定的绝对压力（node_id → p_spec），供适定性断言/
        初值/缩放参考量用。默认空（= 不锚定）；锚定元件覆盖。"""
        return {}

    def T_supply(self, ctx) -> float:
        """源元件供气总温 K（向网络注入流体时的源项温度）。
        默认 T0_default；PB/MASS_SOURCE/锚温 BOOSTER 覆盖。
        节点能量平衡的源项用——内部元件不用（走上游节点温度）。"""
        return ctx.T0_default

    def heat_input(self, x, ctx) -> float:
        """元件给流体的加热功率 W（发热/冷却元件用；默认 0）。
        物理上 ΔT = q/(ṁ·cp)，但接口报 q——port_T_out 默认实现以
        q/((ṁ+ε_q)·cp) 并入温升，ṁ 被代数约去，零流量处无除法
        奇点（见开发日志想法 16）。增量型元件只写这一个方法。"""
        return 0.0

    #: 零流量发热兜底流量 kg/s（ṁ→0 时温升封顶 q/(ε_q·cp)，防发散；
    #: 物理语义=热量暂存等流动带走，收敛后近零流量发热应报表警告）
    EPS_Q = 1.0e-6

    def port_T_out(self, x, ctx, j: int) -> float:
        """流体经口 j 离开元件时的输运总温 K（节点能量平衡的注入项）。

        默认实现 = 两口件（绝热直通 + heat_input 温升）：
          T_out = 另一口所连节点温度 + q/((ṁ+ε_q)·cp)
        增量型发热元件（heater 等）只需写 heat_input，本方法零改动；
        多口混合元件（T 型/盘腔等）必须覆盖——进料集合由解出的 ṁ
        符号决定，混合物理没有"另一口"，框架不猜（raise 逼作者写明）。
        口 j 应为出料口（ṁ_j < 0，assembly 调用约定）；对出料口问
        进料口流量取温升（连续性保证两者绝对值相等，进料口符号更直白）。
        """
        n_ports = len(self.comp.ports)
        if n_ports != 2:
            raise NotImplementedError(
                f"{type(self).__name__} 是 {n_ports} 口元件，"
                f"必须覆盖 port_T_out（多口元件的出流温度是元件物理，"
                f"无默认可猜——见 elements/base.py docstring）")
        j_other = 1 - j
        T_up = self._total_t(x, ctx, j_other)
        q = self.heat_input(x, ctx)
        if q == 0.0:
            return T_up
        cp = ctx.gas.cp()
        m_in = abs(x[self._m_idx[j_other]])     # 进料口流量（=出料量）
        return T_up + q / ((m_in + self.EPS_Q) * cp)

    def __init__(self, comp):
        self.comp = comp

    # ---------- 未知量排布 ----------
    # 全局解向量 x 的排布（由 assembly 层统一定义，元件层只认局部下标）:
    #   x = [p0_1 … p0_Nint | ṁ_1 … ṁ_M]   （M = 全网端口总数）
    # 元件的局部未知量下标由 assembly 注入（set_indices），元件不自己找。

    def set_indices(self, p_node_ids: Sequence[int], m_port_ids: Sequence[int],
                    t_node_ids: Sequence[int]):
        """assembly 建索引时调用：p_node_ids[i] 是 ports[i] 所连节点的
        p0 在 x 里的下标；m_port_ids[i] 是 ports[i] 的 ṁ 下标；
        t_node_ids[i] 是所连节点 T0 在 x 温度段的下标（全部节点都有）。"""
        self._p_idx = list(p_node_ids)
        self._m_idx = list(m_port_ids)
        self._t_idx = list(t_node_ids)
        self._node_ids = [port.node_id for port in self.comp.ports]

    @property
    def n_equations(self) -> int:
        """该元件贡献的方程数 = n_ports（每口 1 个连续性或特性方程，
        由 residual 的排布决定）。二口元件 = 2。"""
        return len(self.comp.ports)

    @property
    def row_units(self) -> list[int]:
        """残差块各行量纲档（长度 = n_equations；默认全 0=流量纲 kg/s）。
        1=压力 Pa（PressureBoundary 的 p−p_spec、锚定 booster）、
        2=能量 W（M3 节点能量行）由子类覆盖。assembly 逐块收集成
        全局表供缩放层分行取值（scaling.row_scales）——行分档。"""
        return [0] * self.n_equations

    @abstractmethod
    def residual(self, x: np.ndarray, ctx: "SolveContext") -> np.ndarray:
        """返回本元件的残差块（长度 n_equations = n_ports）。
        x 是全局解向量；ctx 提供物性(R, γ)、锚温、时间等。
        取值助手：self._total_p(x, ctx, i) 返回第 i 口所连节点的总压，
        self._total_t(x, ctx, i) 返回总温（未锚温取 x，锚温查 ctx）。"""

    def _static_state(self, x, ctx, i: int):
        """第 i 口的局部静参数（StaticState）——统一读入口（2026-09-25）。

        优先读白板：assembly 每次 residual(x) 开头对全部有效口向量化
        反算一次（ctx._static_table，身份键 x is x 保证同源），元件在
        ③ 段循环内查表零成本；未命中（单元测试直调元件残差/表未建）
        回退标量现算，口径与 clamp 纪律完全一致（想法 19 纪律一）。
        静参数是元件私有量——不跨元件共享，白板只是免除重复的 60 轮
        二分，不引入任何元件间依赖（过度设计否决记：不做全局重建
        协议，白板只在单次残差评估生命周期内有效）。
        """
        table = getattr(ctx, "_static_table", None)
        if table is not None and table.x is x:
            st = table.entry(self._m_idx[i] - self._off_m)
            if st is not None:
                return st            # 白板命中（有效口）
        # 回退：表未建 / 单元测试直调 / 非有效口（area=0 无动通量）
        from pysas.fluids import total_to_static
        p0 = max(self._total_p(x, ctx, i), 1.0)
        T0 = max(self._total_t(x, ctx, i), 10.0)
        return total_to_static(p0, T0, x[self._m_idx[i]],
                               self.comp.ports[i].area,
                               ctx.gas.R, ctx.gas.gamma)

    def _total_p(self, x, ctx, i: int) -> float:
        """第 i 口所连节点的总压（全部节点都在 x，直接取）。"""
        return x[self._p_idx[i]]

    def _total_t(self, x, ctx, i: int) -> float:
        """第 i 口所连节点的总温（全部节点 T 都在 x 的温度段，直接取；
        锚温消元已退役——想法 22 完成态）。"""
        return x[self._t_idx[i]]

    # 解析雅可比可选：不实现则 assembly 用差分（离散牛顿天然支持）。
    def jacobian(self, x: np.ndarray, ctx) -> np.ndarray:
        raise NotImplementedError(
            f"{type(self).__name__} 未提供解析雅可比，请用离散牛顿（差分）")


class SolveContext:
    """求解上下文：元件方程需要但不在解向量里的量。"""

    def __init__(self):
        self.gas = None            # IdealGas 实例（物性载体；netinf 读入时注入，
                                   # 直调残差的单元测试需自建后赋值）
        self.T0_default = 288.15   # 无边界信息时的默认总温 K（能量方程接入前的过渡）
        self.time = 0.0            # 当前物理时间 s（非定常推进用）
        self.dt = 0.0              # 当前时间步长 s（0 = 定常）
