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
    PRESSURE_BOUNDARY = 5  # 压力边界（单口）：params = [p0_spec Pa, T0_spec K]
    MASS_SOURCE = 6        # 流量边界/源（单口）：params = [ṁ_spec kg/s(>0注入), T0_spec K]
    BOOSTER = 7            # 升压/压力源（两口，锚定型）：params = [p_in_spec, p_out_spec]
    HEATER = 8             # 发热元件（两口，恒功率）：params = [q W(>0加热)]
    JUNCTION = 9           # 理想三通（三口，零压差绝热混合）：params = []
    AREA_CHANGE = 10       # 突扩/突缩（两口，总压损失 ζ·½ρV_min²）：params = [ζ]
    WALL_FILM = 11         # 壁面换热（单口，cosim 耦合用）：params = [A_ref]
                           # q 注入能量方程（heat_input），模型在
                           # cosim/netelem/filmwall.py 外部注册进工厂
    SURROGATE_FLOW = 12    # 数据驱动代理元件（两口压差→流量）：params = [面积比]
                           # 无量纲特性 Φ(PR) 由 Comp.model_path 携带的模型文件
                           # 定义（.npz 表 / .onnx），模型在 elements/surrogate/
                           # 子包，训练管线在 tools/surrogate/（想法 8 / M7 v1）


class CompType(enum.IntEnum):
    """元件数学类型——与物理种类（ElemType）正交。"""
    STEADY = 0   # 定常：只保留最新一步状态
    VOLUME = 1   # 零维容腔：保留最近 N_HIST 步
    ONE_D = 2    # 一维元件（预留）
    COUPLED = 3  # 耦合元件（预留）


#: 元件端口数约定表（单一事实源，2026-10-01）——io 校验（端口数
#: 够不够）、tools/ui 面板元数据都从这张表读。此前约定只活在
#: ElemType docstring 文字里（三处记载等着漂移：docstring/io/ui）。
#: (min_ports, max_ports)：JUNCTION 允许 3~6 口（理想混合，多股
#: 汇流/分流都合法）；其余固定口数。新元件必须同步登记此表——
#: _check_schema（tools/ui）与 netinf 校验都会对它对拍。
PORT_COUNTS: dict[ElemType, tuple[int, int]] = {
    ElemType.ORIFICE: (2, 2),
    ElemType.SEAL: (2, 2),
    ElemType.PIPE: (2, 2),
    ElemType.PRESWIRL_NOZZLE: (2, 2),
    ElemType.VOLUME: (1, 1),
    ElemType.PRESSURE_BOUNDARY: (1, 1),
    ElemType.MASS_SOURCE: (1, 1),
    ElemType.BOOSTER: (2, 2),
    ElemType.HEATER: (2, 2),
    ElemType.JUNCTION: (3, 6),
    ElemType.AREA_CHANGE: (2, 2),
    ElemType.WALL_FILM: (1, 1),
    ElemType.SURROGATE_FLOW: (2, 2),
}


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
    """节点：统一为内部节点——p0 全部进解向量 x（2026-09-15 边界元件化）。
    边界条件由挂在节点上的单口边界元件（PRESSURE_BOUNDARY / MASS_SOURCE）
    规定，节点本身不再有边界/内部之分。"""
    node_id: int = -1
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
    model_path: str = ""  # 代理元件（SURROGATE_FLOW）模型文件路径：
                          # .npz 一维 Φ 表 / .onnx；load_netinf 把相对路径
                          # 相对 JSON 目录解析；其余元件忽略
    control_volume: float = 0.0  # 容腔体积 m³（VOLUME 型用，自动从 params[0] 同步）

    def __post_init__(self):
        if self.comp_type == CompType.VOLUME and self.params and self.control_volume == 0.0:
            self.control_volume = self.params[0]


@dataclass
class Network:
    """网络总装：只存结构，不存解。要求 node_id/comp_id 从 0 连续编号。"""
    nodes: list[Node] = field(default_factory=list)
    comps: list[Comp] = field(default_factory=list)
    n_interior: int = 0   # 节点总数 = 压力未知量个数（统一后皆为内部节点）
