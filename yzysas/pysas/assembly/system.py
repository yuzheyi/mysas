"""system — NetworkSystem：拓扑 → 方程组索引 → 残差 F(x)。

未知量排布（整个 pysas 统一约定）:
  x = [p0 内部节点 Nint 个 | ṁ 每口一个，M 个]      Nint + M = n
方程排布:
  F = [节点连续性 Nint 个 | 元件方程块 Σn_ports = M 个]   ← 方阵 ✓

符号约定（见 base.py）:
  ṁᵢ > 0 = 流体经端口 i 流入组件
  → 节点连续性 = 挂在该节点上所有口的 ṁ 直接求和（无需方向标志）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

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
        # 内部节点 p0 → x[0..Nint-1]（按 node_id 升序）
        self.interior_ids = sorted(
            n.node_id for n in net.nodes if not n.is_boundary)
        self.p_idx_of_node = {nid: i for i, nid in enumerate(self.interior_ids)}
        self.n_interior = len(self.interior_ids)

        # 端口 ṁ → x[Nint..Nint+M-1]（按 comp_id、port 顺序展平）
        self.m_idx_of_port: dict[tuple[int, int], int] = {}
        k = self.n_interior
        for comp in sorted(net.comps, key=lambda c: c.comp_id):
            for j in range(len(comp.ports)):
                self.m_idx_of_port[(comp.comp_id, j)] = k
                k += 1
        self.n_m = k - self.n_interior
        self.n = self.n_interior + self.n_m

        # 注入元件局部索引
        for comp in net.comps:
            model = self.models[comp.comp_id]
            p_ids = [self.p_idx_of_node.get(p.node_id, -1) for p in comp.ports]
            m_ids = [self.m_idx_of_port[(comp.comp_id, j)]
                     for j in range(len(comp.ports))]
            model.set_indices(p_ids, m_ids)

        # 节点 → 挂在其上的口列表（comp_id, port_index）——残差求和用
        self.ports_on_node: dict[int, list[tuple[int, int]]] = {}
        for comp in net.comps:
            for j, port in enumerate(comp.ports):
                self.ports_on_node.setdefault(port.node_id, []).append(
                    (comp.comp_id, j))

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
        """从 Network 的边界节点构造 SolveContext（load_netinf 已建 ctx 时不必调）。"""
        from pysas.elements.base import SolveContext
        ctx = SolveContext()
        for node in self.net.nodes:
            if node.is_boundary:
                ctx.boundary_p0[node.node_id] = node.total_pressure
                ctx.boundary_T0[node.node_id] = node.total_temperature
        if general is not None:
            ctx.gas_R = general.gas_R
            ctx.gamma = general.gamma
        return ctx
