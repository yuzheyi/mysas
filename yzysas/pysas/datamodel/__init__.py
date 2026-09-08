"""pysas.datamodel — SAS 网络数据结构包。

分层（一个子模块对应一个 C++ 设计头文件）：
  topology  ← sas_network.h   拓扑 + 几何：Port/NodeLink/Node/Comp/Network
  state     ← sas_comp.h      流动状态：PortState/VolumeState/UnsteadyState
  general   ← sas_general.h   全局求解设置：SolveMode/General
  solver    ← sas_solver.h    算法配置：Newton/DiscreteNewton/Homotopy
"""
from .topology import Port, NodeLink, Node, Comp, Network, ElemType, CompType
from .state import PortState, VolumeState, UnsteadyState, N_HIST
from .general import SolveMode, General
from .solver import (
    NewtonOptions, NewtonReport,
    DiscreteNewtonOptions,
    HomotopyOptions, HomotopyReport,
    SolverSettings,
)

__all__ = [
    # topology
    "Port", "NodeLink", "Node", "Comp", "Network", "ElemType", "CompType",
    # state
    "PortState", "VolumeState", "UnsteadyState", "N_HIST",
    # general
    "SolveMode", "General",
    # solver
    "NewtonOptions", "NewtonReport", "DiscreteNewtonOptions",
    "HomotopyOptions", "HomotopyReport", "SolverSettings",
]
