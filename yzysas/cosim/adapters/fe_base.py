"""fe_base — FE 适配器抽象契约（cosim 的维度扩展点）。

coupling/ 只认本契约，不 import pyfem —— 换 3D FE = 新增一个
adapter 文件实现同一契约，耦合驱动器零改动。

契约成员（实现者必须提供）:

  boundary_geometry(name) -> dict
      {"facets": (nf,), "facet_rs": (nf,) 各 facet 中点半径,
       "facet_areas": (nf,) 各 facet 面积（轴对称 = 2πr·ds）}
      关联式求 h 分布、WALL_FILM 积分 q 都用它；初始化算一次缓存。

  solve_films(films) -> dict[str, FilmSolution]
      films: {name: {"h": 标量或(nf,)数组, "T_gas": K}}（耦合器每轮更新）
      FilmSolution: {"T_w": (nf,) 壁温分布, "Q": W 总热流(>0 气流加热壁)}
      —— 网络侧 WALL_FILM 拿 T_w 分布积分，FE 侧拿 h/T_gas 做 Robin。

  displacements(boundary_name, direction="r") -> (nnodes,) 或 (nf,)
      该边界上位移场（间隙耦合 Phase 4+ 用；Phase 2 可先返回 None）。

  属性:
      n_nodes: FE 节点数（monolithic 全局未知量规模用）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class FilmSolution:
    """一段 film 边界的求解输出。"""
    T_w: np.ndarray          # (nf,) 各 facet 中点壁温 K
    Q: float                 # 总热流 W（>0 = 气流加热壁面；网络侧对账口径）


class FEAdapterBase(ABC):
    """FE 求解器适配契约（热耦合必需三件套 + 间隙耦合预留）。"""

    #: FE 节点数（全局耦合未知量规模）
    n_nodes: int

    @abstractmethod
    def boundary_geometry(self, name: str) -> dict:
        """命名边界的 facet 几何（半径/面积），缓存复用。"""

    @abstractmethod
    def solve_films(self, films: dict) -> dict[str, FilmSolution]:
        """按给定 (h, T_gas) 解稳态热传导，返回各段 (T_w 分布, Q)。"""

    def displacements(self, boundary_name: str, direction: str = "r"):
        """边界位移（间隙耦合用；默认未实现。"""
        raise NotImplementedError(
            "位移提取属间隙耦合（Phase 4+），当前 adapter 未实现")
