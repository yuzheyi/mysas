"""materials — 随温度插值材料表（线性插值，向量化）。

数据源: 31project 0_example/dqy/model/boundary.txt 的 *Material TC11
（航空发动机风扇盘钛合金）。表外温度线性外推（端点斜率延拓）。

接口约定: 每个属性一个方法，接收 ndarray 或 float，返回同形值；
单位 SI（K, W/m/K, Pa, 1/K, kg/m³）。

CalcuLiX 口径对照:
  *Conductivity  → k(T)   W/(m·K)
  *Elastic       → E(T) Pa, nu(T)
  *Expansion, Zero=20 → alpha(T) 1/K，参考温 T_ref=293.15 K（20°C）
  *Density       → rho(T) kg/m³
"""
from __future__ import annotations

import numpy as np


class Material:
    """一维温度插值材料（分段的线性插值/外推）。

    table: [(T_K, value), ...] 按 T 升序。
    """

    def __init__(self, name: str, tables: dict[str, list[tuple[float, float]]]):
        self.name = name
        self._tables: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for prop, pts in tables.items():
            T = np.asarray([p[0] for p in pts], dtype=float)
            v = np.asarray([p[1] for p in pts], dtype=float)
            self._tables[prop] = (T, v)

    def __call__(self, prop: str, T):
        """prop(T) 线性插值 + 端点斜率外推（np.interp 的 clamp 改外推）。"""
        T_tab, v_tab = self._tables[prop]
        T_arr = np.asarray(T, dtype=float)
        # np.interp 是 clamp 外推；材料表两端用端点斜率线性延拓更物理
        lo = T_arr < T_tab[0]
        hi = T_arr > T_tab[-1]
        out = np.interp(T_arr, T_tab, v_tab)
        if np.any(lo):
            s = (v_tab[1] - v_tab[0]) / (T_tab[1] - T_tab[0])
            out = np.where(lo, v_tab[0] + s * (T_arr - T_tab[0]), out)
        if np.any(hi):
            n = len(v_tab)
            s = (v_tab[-1] - v_tab[-2]) / (T_tab[-1] - T_tab[-2])
            out = np.where(hi, v_tab[-1] + s * (T_arr - T_tab[-1]), out)
        return out if out.ndim else float(out)

    # ---------- 语义化便捷方法（FE 表单直接用） ----------
    def k(self, T):          # 导热率 W/(m·K)
        return self("k", T)

    def E(self, T):          # 弹性模量 Pa
        return self("E", T)

    def nu(self, T):         # 泊松比
        return self("nu", T)

    def alpha(self, T):      # 热膨胀系数 1/K
        return self("alpha", T)

    def rho(self, T):        # 密度 kg/m³
        return self("rho", T)


#: TC11 钛合金（31project dqy boundary.txt 迁移，零改动）
TC11 = Material("TC11", {
    "k":     [(373.15, 6.3), (473.15, 7.5), (573.15, 9.2),
              (673.15, 10.5), (773.15, 12.1)],
    "E":     [(293.15, 123e9), (373.15, 119e9), (473.15, 114e9),
              (573.15, 110e9), (673.15, 115e9), (773.15, 112e9)],
    "nu":    [(293.15, 0.33), (773.15, 0.33)],
    "alpha": [(373.15, 9.3e-6), (473.15, 9.3e-6),
              (573.15, 9.5e-6), (673.15, 9.7e-6)],
    "rho":   [(293.15, 4480.0), (773.15, 4480.0)],
    # 热膨胀参考温（CalcuLiX *Expansion Zero=20 → 293.15 K）
    "T_ref_alpha": [(293.15, 293.15)],
})

#: 注册表（“加材料 = 加一行”）
MATERIALS = {"TC11": TC11}


def get_material(name: str) -> Material:
    m = MATERIALS.get(name)
    if m is None:
        raise KeyError(f"未知材料 {name!r}，可用: {sorted(MATERIALS)}")
    return m
