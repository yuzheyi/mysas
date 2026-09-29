"""filmwall — 壁面换热元件（单口，cosim 热耦合的网络侧落点）。

params = [T_w_default K]
    非耦合独立跑网络时的默认壁温（耦合运行时由 cosim 注入真实 T_w 分布，
    见下）。

隐式形式（单口 1 方程）——与 PRESSURE_BOUNDARY 同构的零压降直通:
  f1 = ṁ1          恒等（单口无对偶口，流量由所连节点连续性定）
  （压力不出现: 壁面换热不产生压降，口所连节点压力由网络其余部分定）

能量: heat_input() 报 q = h·A·(T_w − T_gas)（>0 = 壁面加热气流），
  assembly 能量方程把温升并入注出节点——与 HEATER 同一条注入管道，
  区别仅在于 q 不是常数而是 Newton 迭代中的耦合量。

耦合注入协议（cosim 专用，网络独立跑时不生效）:
  本元件实例属性 h / area / T_gas / T_w_dist 可被 cosim 耦合器在每轮
  迭代前覆写:
    film.h = 500.0            标量（均匀）
    film.T_w_dist = (nf,) 数组 + film.weights = (nf,) 面积权重
      → q = Σ h_f·(T_w_f − T_gas)·w_f   （FE 壁温分布积分，非平均近似）
  缺省（未注入）: h=0 → q=0，网络退化为绝热——安全中性。
"""
from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel


class FilmWallModel(ElementModel):
    """壁面换热（单口）: 零压降直通 + heat_input 报耦合热流。"""
    elem_type = 11  # ElemType.WALL_FILM

    def __init__(self, comp):
        super().__init__(comp)
        self.T_w_default = comp.params[0]
        # ---- 耦合器注入态（独立跑网络时保持缺省 → q=0 绝热中性） ----
        self.h: float = 0.0                  # W/(m²K)（标量；分布见下）
        self.T_gas: float = 288.15           # 气流温度 K
        self.T_w_dist: np.ndarray | None = None  # (nf,) 壁温分布（可选）
        self.weights: np.ndarray | None = None   # (nf,) 面积权重 m²（与上同给）
        self.h_dist: np.ndarray | None = None    # (nf,) h 分布（可选）

    @property
    def row_units(self) -> list[int]:
        return [0]  # f1 恒等式 = 流量纲

    def anchor_P_values(self) -> dict[int, float]:
        return {}   # 不锚压（节点压力由网络定）

    def set_coupling(self, h, T_gas, T_w_dist=None, weights=None,
                     h_dist=None):
        """cosim 耦合器每轮调用: 注入换热参数。

        h:        标量 W/(m²K)（均匀）——T_w_dist 未给时用它
        T_gas:    气流温度 K
        T_w_dist: (nf,) FE 壁温分布（给了走分布积分，否则用 T_w_default）
        weights:  (nf,) 各 facet 面积权重 m²（与 T_w_dist 同给）
        h_dist:   (nf,) h 分布（可选；缺省用标量 h）
        """
        self.h = float(h)
        self.T_gas = float(T_gas)
        self.T_w_dist = (None if T_w_dist is None
                         else np.asarray(T_w_dist, dtype=float))
        self.weights = (None if weights is None
                        else np.asarray(weights, dtype=float))
        self.h_dist = (None if h_dist is None
                       else np.asarray(h_dist, dtype=float))
        if (self.T_w_dist is None) != (self.weights is None):
            raise ValueError("T_w_dist 与 weights 必须成对给出")

    def heat_input(self, x, ctx) -> float:
        """q > 0 = 壁面加热气流（FE 侧 Q_j > 0 = 气流加热壁 → 符号相反）。"""
        if self.T_w_dist is not None and self.weights is not None:
            hh = (self.h_dist if self.h_dist is not None
                  else np.full_like(self.T_w_dist, self.h))
            # 分布积分: q = Σ h_f·(T_w_f − T_gas)·w_f
            q = float(np.sum(hh * (self.T_w_dist - self.T_gas)
                             * self.weights))
        else:
            q = self.h * self._area_total() * (self.T_w_default - self.T_gas)
        return q

    def _area_total(self) -> float:
        """总面积 m²（分布权重或元件几何面积）。"""
        if self.weights is not None:
            return float(self.weights.sum())
        return float(self.comp.ports[0].area) if self.comp.ports else 1.0

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        m1 = x[self._m_idx[0]]
        return np.array([m1])       # 恒等式（单口直通）

    def port_T_out(self, x, ctx, j: int) -> float:
        """出流温度 = 上游直通 + heat_input 温升（单口=两口缺省退化）。"""
        # 单口无"另一口"，上游即所连节点本身（零压降直通语义）
        return self._total_t(x, ctx, j)  # 温升由 assembly 经 heat_input 并入
