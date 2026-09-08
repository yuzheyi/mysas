"""state — 流动状态层（对应 sas_comp.h）。

随时间/迭代变化的量。与拓扑层分离：面积、连接关系不在这里重复存储。
"""
from __future__ import annotations

from dataclasses import dataclass, field

N_HIST = 4  # 非定常元件保留最近 4 个时间步


@dataclass
class PortState:
    """接口流动状态。"""
    mass_flow: float = 0.0     # kg/s（正 = 按建网方向流入组件）
    static_pressure: float = 0.0
    static_temperature: float = 0.0
    total_pressure: float = 0.0
    total_temperature: float = 0.0
    mach_number: float = 0.0   # 派生量
    choked: bool = False       # 临界标志（亚/超临界方程分支用）


@dataclass
class VolumeState:
    """容腔状态（VOLUME 型组件每个时间步一份）。"""
    time: float = 0.0
    control_mass: float = 0.0  # kg，m(t) = ∫Σṁ dt
    avg_static_pressure: float = 0.0
    avg_static_temperature: float = 0.0
    avg_total_pressure: float = 0.0
    avg_total_temperature: float = 0.0
    avg_density: float = 0.0   # ρ = m/V


@dataclass
class UnsteadyState:
    """非定常组件状态：环形缓冲，head 指向最新步。"""
    volume_hist: list[VolumeState] = field(
        default_factory=lambda: [VolumeState() for _ in range(N_HIST)])
    ports_hist: list[list[PortState]] = field(default_factory=list)
    head: int = 0

    def advance(self) -> None:
        """时间步前移：head 循环减一，新数据写到新 head，覆盖最老步。"""
        self.head = (self.head + N_HIST - 1) % N_HIST
