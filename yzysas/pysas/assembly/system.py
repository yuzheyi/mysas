"""system — NetworkSystem：拓扑 → 方程组索引 → 残差 F(x)。

未知量排布（整个 pysas 统一约定）:
  x = [p0 全部节点 N 个 | ṁ 每口一个，M 个]      N + M = n
      （2026-09-15 边界元件化：节点统一为内部节点，边界条件由单口
        边界元件规定，不再有 boundary/interior 分叉）
方程排布:
  F = [节点连续性 N 个 | 元件方程块 Σn_ports = M 个]   ← 方阵 ✓

符号约定（见 base.py）:
  ṁᵢ > 0 = 流体经端口 i 流入组件
  → 节点连续性 = 挂在该节点上所有口的 ṁ 直接求和（无需方向标志）

行量纲登记（缩放层用，scaling.py 行缩放分档）:
  前 N 行（连续性）与绝大多数元件行是流量纲 kg/s；
  PRESSURE_BOUNDARY 行是压力纲 Pa → self.row_is_pressure 标记。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from pysas.datamodel import ElemType
from pysas.elements.base import ElementModel

if TYPE_CHECKING:
    from pysas.elements.base import SolveContext


class NetworkSystem:
    """持有 Network + 元件模型实例，提供 residual(x) 与索引管理。"""

    def __init__(self, network, models: dict[int, ElementModel]):
        self.net = network
        self.models = models  # comp_id → ElementModel 实例
        self._build_indices()

    # ---------- 索引 ----------
    def _build_indices(self):
        net = self.net
        # 全部节点 p0 → x[0..N-1]（按 node_id 升序）
        self.interior_ids = sorted(n.node_id for n in net.nodes)
        self.p_idx_of_node = {nid: i for i, nid in enumerate(self.interior_ids)}
        self.n_interior = len(self.interior_ids)  # = 全部节点数（命名沿用）

        # 端口 ṁ → x[N..N+M-1]（按 comp_id、port 顺序展平）
        self.m_idx_of_port: dict[tuple[int, int], int] = {}
        k = self.n_interior
        for comp in sorted(net.comps, key=lambda c: c.comp_id):
            for j in range(len(comp.ports)):
                self.m_idx_of_port[(comp.comp_id, j)] = k
                k += 1
        self.n_m = k - self.n_interior
        self.n = self.n_interior + self.n_m

        # 行量纲登记：True = 压力纲（Pa），False = 流量纲（kg/s）
        # 排布与 residual ①②一致：N 个连续性行 + 各元件块（按 comp_id 排序；
        # 各块行量纲由元件自报：model.row_is_pressure，默认全流量纲，
        # PressureBoundary/Booster 等含压力行的元件覆盖此属性）
        units = [False] * self.n_interior
        for comp in sorted(net.comps, key=lambda c: c.comp_id):
            units.extend(self.models[comp.comp_id].row_is_pressure)
        self.row_is_pressure = units  # len = n

        # 注入元件局部索引（全节点在 x，无 -1 特判）
        for comp in net.comps:
            model = self.models[comp.comp_id]
            p_ids = [self.p_idx_of_node[p.node_id] for p in comp.ports]
            m_ids = [self.m_idx_of_port[(comp.comp_id, j)]
                     for j in range(len(comp.ports))]
            model.set_indices(p_ids, m_ids)

        # 节点 → 挂在其上的口列表（comp_id, port_index）——残差求和用
        self.ports_on_node: dict[int, list[tuple[int, int]]] = {}
        for comp in net.comps:
            for j, port in enumerate(comp.ports):
                self.ports_on_node.setdefault(port.node_id, []).append(
                    (comp.comp_id, j))

        # 适定性断言：至少 1 个压力锚定元件——绝对压力水平必须有元件规定
        # （纯压差/全流量元件的网络 → J 零空间：只感知压差，水平浮动）。
        # 锚定能力由元件自报（model.anchors_pressure，见 base.py），
        # 新元件（TANK 等）声明能力即可，此处零改动。
        n_anchor = sum(1 for m in self.models.values() if m.anchors_pressure)
        if n_anchor == 0:
            raise ValueError(
                "网络无压力锚定元件（无元件声明 anchors_pressure）：绝对压力"
                "水平无锚定，方程组奇异（纯压差/全流量网络）。"
                "至少挂 1 个压力锚定元件（如 PRESSURE_BOUNDARY）")

    # ---------- 残差 ----------
    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        """全局残差 F(x)：节点连续性 + 各元件方程块。"""
        F = np.zeros(self.n)

        # ① 节点连续性：Σᵢ ṁᵢ = 0（ṁ 流入组件为正 → 对节点即流出为正）
        for nid in self.interior_ids:
            F[self.p_idx_of_node[nid]] = sum(
                x[self.m_idx_of_port[cid_j]] for cid_j in self.ports_on_node[nid])

        # ② 元件方程块（每口 1 个，共 M 个）
        off = self.n_interior
        for comp in sorted(self.net.comps, key=lambda c: c.comp_id):
            block = self.models[comp.comp_id].residual(x, ctx)
            F[off:off + len(block)] = block
            off += len(block)
        return F

    # ---------- 辅助 ----------
    def make_ctx(self, general=None) -> "SolveContext":
        """空 ctx（物性可选注入）。边界元件参数自带 p0/T0，不再从此填。"""
        from pysas.elements.base import SolveContext
        ctx = SolveContext()
        if general is not None:
            ctx.gas_R = general.gas_R
            ctx.gamma = general.gamma
        return ctx
