"""water_vapor — 水蒸气（过热蒸汽，理想气体近似），GasModel 第二个子类。

架构验证件（2026-09-26）：证明"加气体 = 加文件"的家族机制——本文件
零改 base/ideal_gas/io/assembly 任何一行，JSON 的 gas.type 写
"WaterVapor" 即接入。

物理口径（过热蒸汽理想气体近似，远离饱和线的 SAS 典型工况）:
  R = 461.5 J/(kg·K)     = R_universal/18.015（水摩尔质量）
  gamma = 1.33           过热蒸汽典型比热比（~1.33 @ 中低温）
  Sutherland: mu_ref = 1.22e-5 Pa·s @ T_ref = 373.15 K, S = 542 K
    （蒸汽粘度拟合常用组；100°C 实测约 1.23e-5，锚点取标准大气压
     饱和温度便于记忆，S 取 IAPWS 近似区间的中值）
  ⚠ 粘度族参数显式写在本构造器（不继承基类空气缺省）——第二种气体
    的存在正是为了暴露"空气缺省住基类"的隐式假设（想法 24 边界）。
"""
from __future__ import annotations

import numpy as np

from pysas.fluids.base import GasModel, register_gas
from pysas.fluids.isentropic import (
    StaticState, mach_from_q, mach_from_q_arr, q_of_mach, statics_from_mach,
    total_to_static)


@register_gas
class WaterVapor(GasModel):
    """过热水蒸气（理想气体近似）：常比热 + 蒸汽 Sutherland（自带 mu）。"""

    def __init__(self, R: float = 461.5, gamma: float = 1.33,
                 mu: float | None = None,
                 mu_ref: float = 1.22e-5, T_ref: float = 373.15,
                 S: float = 542.0):
        if R <= 0.0 or gamma <= 1.0:
            raise ValueError(f"气体参数非物理: R={R}, gamma={gamma}")
        self.R, self.gamma = R, gamma
        self._mu_const = mu           # 蒸汽粘度族（显式——勿移基类）
        self._mu_ref, self._T_ref, self._S = mu_ref, T_ref, S

    # ---------- 粘度（蒸汽 Sutherland 族，同 cp 模式自带） ----------
    def mu(self, T: float | None = None) -> float:
        """动力粘度 Pa·s：常数模式恒返；蒸汽 Sutherland
        μ_ref·(T/T_ref)^1.5·(T_ref+S)/(T+S)；T=None 回落 μ_ref。"""
        if self._mu_const is not None:
            return self._mu_const
        if T is None:
            return self._mu_ref
        r = T / self._T_ref
        return self._mu_ref * r * np.sqrt(r) * (self._T_ref + self._S) / (T + self._S)

    # ---------- 物性（理想气体闭式，参数是蒸汽的） ----------
    def cp(self, T: float | None = None) -> float:
        """定压比热 J/(kg·K) = γR/(γ−1) ≈ 1863（过热蒸汽典型 ~1900）。"""
        return self.R * self.gamma / (self.gamma - 1.0)

    def cv(self, T: float | None = None) -> float:
        """定容比热 J/(kg·K) = R/(γ−1)。"""
        return self.R / (self.gamma - 1.0)

    def rho_from_pT(self, p: float, T: float) -> float:
        """理想气体状态方程密度 ρ = p/(R·T)（远离饱和线的过热区）。"""
        return p / (self.R * T)

    # ---------- 气动关系（与 IdealGas 同一套等熵算法体，参数不同） ----------
    def q_of_mach(self, ma: float) -> float:
        return q_of_mach(ma, self.R, self.gamma)

    def mach_from_q(self, q: float) -> tuple[float, bool]:
        """标量反解（手算/调试用）。"""
        return mach_from_q(q, self.R, self.gamma)

    def mach_from_q_arr(self, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """向量化反解（白板 prime 用）。"""
        return mach_from_q_arr(q, self.R, self.gamma)

    def q_of_flow(self, mdot, p0, T0, area):
        """|ṁ| → q（蒸汽 R/γ 进闭式）。"""
        return np.abs(mdot) * np.sqrt(T0) / (p0 * area)

    def statics_from_mach(self, ma, p0, T0):
        """Ma + 总参数 → (T, p, rho, v)（等熵闭式，蒸汽参数）。"""
        return statics_from_mach(ma, p0, T0, self.R, self.gamma)

    def total_to_static(self, p0: float, T0: float, mdot: float,
                        area: float) -> StaticState:
        """端口四件套 → 静参数。"""
        return total_to_static(p0, T0, mdot, area, self.R, self.gamma)

    def choked_flow(self, area: float, p0: float, T0: float) -> float:
        """壅塞流量上限（Cd=1）= q(1)·p0·A/√T0。"""
        if area <= 0.0 or p0 <= 0.0:
            return 0.0
        return self.q_of_mach(1.0) * p0 * area / np.sqrt(T0)


def _selftest():
    """蒸汽自验：物理锚点手算 + 与空气参数的区分度 + make_gas 分发。"""
    wv = WaterVapor()
    ok = []

    # ① 物理锚点：cp ≈ 1863（蒸汽典型 1860-1900）；R 自洽
    print(f"① cp={wv.cp():.1f}（γR/(γ-1)=1.33×461.5/0.33）  R={wv.R}")
    ok.append(abs(wv.cp() - 1.33 * 461.5 / 0.33) < 1e-9)

    # ② Sutherland 蒸汽族：373.15K 处 = mu_ref；与空气族明显不同
    #    （对照 = 蒸汽参数换成空气三参数的假想实例——粘度族区分实证）
    mu_b = wv.mu(373.15)
    from pysas.fluids import IdealGas
    mu_air_same_T = IdealGas(R=461.5, gamma=1.33).mu(373.15)  # 空气粘度族
    print(f"② mu(373.15)={mu_b:.4e}（=1.22e-5）  若用空气族={mu_air_same_T:.4e}"
          f"（差 {(mu_b / mu_air_same_T - 1) * 100:+.1f}%——粘度族区分实证）")
    ok.append(abs(mu_b - 1.22e-5) < 1e-12 and mu_b != mu_air_same_T)

    # ③ 状态方程：1bar/500K 的蒸汽密度手算 ρ = 1e5/(461.5×500)
    rho = wv.rho_from_pT(1.0e5, 500.0)
    print(f"③ rho(1bar,500K)={rho:.5f} kg/m³  手算={1.0e5 / (461.5 * 500.0):.5f}")
    ok.append(abs(rho - 1.0e5 / (461.5 * 500.0)) < 1e-12)

    # ④ 等熵链（蒸汽 γ=1.33）：Ma=0.5 设计点闭式手算
    T0d, p0d, Ad = 600.0, 5.0e5, 2.0e-4
    ma_d = 0.5
    md = wv.q_of_mach(ma_d) * p0d * Ad / np.sqrt(T0d)
    s = wv.total_to_static(p0d, T0d, md, Ad)
    tau = 1.0 + 0.5 * 0.33 * 0.25
    T_hand = T0d / tau
    p_hand = p0d * tau ** (-1.33 / 0.33)
    v_hand = 0.5 * np.sqrt(1.33 * 461.5 * T_hand)
    print(f"④ 等熵设计点: T={s.T:.4f}(手算 {T_hand:.4f})  "
          f"p={s.p:.1f}(手算 {p_hand:.1f})  v={s.v:.2f}(手算 {v_hand:.2f})")
    ok.append(abs(s.T - T_hand) < 1e-8 and abs(s.p - p_hand) < 1e-4
              and abs(s.v - v_hand) < 1e-6)

    # ⑤ 壅塞：q(1) 用蒸汽 γ——与空气不同（区分度）
    fc_wv = wv.choked_flow(1.0e-4, 3.0e5, 600.0)
    from pysas.fluids import IdealGas
    fc_air = IdealGas(R=461.5, gamma=1.33).choked_flow(1.0e-4, 3.0e5, 600.0)
    # 上一行故意用同参 IdealGas：证明 choked_flow 只依赖 R/gamma
    print(f"⑤ 壅塞上限={fc_wv:.8f}  同参IdealGas={fc_air:.8f}（应逐位相等）")
    ok.append(abs(fc_wv - fc_air) < 1e-15)

    # ⑥ make_gas 分发：type="WaterVapor" 造出蒸汽（粘度族随类走）
    from pysas.fluids import make_gas
    g = make_gas({"type": "WaterVapor"})
    ok6 = isinstance(g, WaterVapor) and abs(g.cp() - wv.cp()) < 1e-12
    ok6 &= abs(g.mu(373.15) - 1.22e-5) < 1e-12   # JSON 路径也拿到蒸汽粘度族
    print(f"⑥ make_gas: type=WaterVapor → {type(g).__name__}  "
          f"cp 匹配={ok6}")
    ok.append(ok6)

    all_ok = all(ok)
    print("\n" + "=" * 60)
    print("OK fluids.water_vapor 全部验证通过" if all_ok else "FAIL 未全过")
    return all_ok
