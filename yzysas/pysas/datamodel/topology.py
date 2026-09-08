"""topology — 网络拓扑 + 几何层（对应 sas_network.h）。

只描述"谁连谁、多大"，不含任何流动状态。
拓扑是方程组的骨架：
  Comp.ports[i].node_id → 分支动量方程连接的两端
  Node.links            → 节点质量/能量方程汇聚的分支
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class ElemType(enum.IntEnum):
    """元件物理种类——决定用哪个流量方程、params 怎么解读。"""
    ORIFICE = 0          # 孔板/喷嘴：params = [面积比, 流量系数Cd]
    SEAL = 1             # 篦齿封严：params = [齿高, 齿宽, 齿数, 间隙]
    PIPE = 2             # 圆柱直管：params = [长度, 直径, 粗糙度]
    PRESWIRL_NOZZLE = 3  # 预旋喷嘴：params = [半径, 角度, Cd]
    VOLUME = 4           # 纯容腔：params = [体积]


class CompType(enum.IntEnum):
    """元件数学类型——与物理种类（ElemType）正交。"""
    STEADY = 0   # 定常：只保留最新一步状态
    VOLUME = 1   # 零维容腔：保留最近 N_HIST 步
    ONE_D = 2    # 一维元件（预留）
    COUPLED = 3  # 耦合元件（预留）


@dataclass
class Port:
    """接口几何 + 拓扑（不随时间变）。"""
    area: float = 0.0   # 流通面积 m²
    node_id: int = -1   # 连接到的节点 ID


@dataclass
class NodeLink:
    """节点 ↔ 组件的连接记录（建网方向约定，非求解结果）。"""
    comp_id: int = -1
    port_index: int = -1
    is_outflow_of_comp: bool = True  # true: 该口是组件出口（流体流入节点）


@dataclass
class Node:
    """节点：内部节点 p0/T0 是未知量；边界节点是给定边界条件。"""
    node_id: int = -1
    is_boundary: bool = False
    total_pressure: float = 0.0      # 总压 Pa
    total_temperature: float = 0.0   # 总温 K
    volume: float = 0.0              # 节点自身容腔 m³（0 = 无）
    links: list[NodeLink] = field(default_factory=list)


@dataclass
class Comp:
    """组件：拓扑 + 几何 + 物理参数。"""
    comp_id: int = -1
    elem_type: ElemType = ElemType.ORIFICE
    comp_type: CompType = CompType.STEADY
    ports: list[Port] = field(default_factory=list)
    params: list[float] = field(default_factory=list)  # 按 elem_type 约定顺序
    control_volume: float = 0.0  # 容腔体积 m³（VOLUME 型用，自动从 params[0] 同步）

    def __post_init__(self):
        if self.comp_type == CompType.VOLUME and self.params and self.control_volume == 0.0:
            self.control_volume = self.params[0]


@dataclass
class Network:
    """网络总装：只存结构，不存解。要求 node_id/comp_id 从 0 连续编号。"""
    nodes: list[Node] = field(default_factory=list)
    comps: list[Comp] = field(default_factory=list)
    n_interior: int = 0   # 内部节点数 = 方程组未知量个数（拓扑校验后填）
    n_boundary: int = 0
