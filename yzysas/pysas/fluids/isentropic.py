"""isentropic — 理想气体等熵气动函数（gasprops 时期更名而来，2026-09-18）。

核心：total_to_static —— 端口四件套 (p0, T0, mdot, A) 反解静参数。
  求解器解出的是总压/总温/流量，动量型元件（可压长管、预旋喷嘴、
  盘腔）的残差需要当地静参数（p, T, rho, V）与 Ma；后处理报表同样要。

物理（等熵关系，理想气体，亚声速支）:
  tau = 1 + (gamma-1)/2 * Ma^2
  T = T0 / tau,  p = p0 * tau^(-gamma/(gamma-1)),  rho = p/(R*T),
  V = Ma * sqrt(gamma*R*T)
  流量函数:  q(Ma) = mdot*sqrt(T0)/(p0*A)
           = sqrt(gamma/R) * Ma * tau^(-(gamma+1)/(2(gamma-1)))
  q 在 Ma∈[0,1] 严格单调增（亚声速支唯一可逆），二分反解 Ma。

边界情形:
  mdot = 0        → Ma=0，静=总（滞止），V=0
  q > q(Ma=1)     → 该面积下物理不可能 = 壅塞，夹回 Ma=1 并标记 choked
                    （与 orifice.py 超临界分支同一物理，数值上
                     q(1) == sqrt(gamma/R)*(2/(gamma+1))^((gamma+1)/(2(gamma-1)))
                     ——见 __main__ 互检）
  超声速支        未实现（SAS 网络内基本亚声速；需选支信息，届时再扩）
  area <= 0       无法定义动通量（边界元件口 area=0）→ ValueError

导数行为（差分雅可比安全性，M1 教训清单）:
  Ma→0 时 q ≈ sqrt(gamma/R)*Ma 线性 → Ma ∝ mdot，静参数对 mdot
  在零点光滑（与管层流 √mdot 奇异相反，安全）；壅塞夹断处导数
  不连续（kink），与孔板 beta 夹断同族，可接受。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class StaticState:
    """一个端口截面上的完整静参数（等熵恢复假设）。"""
    ma: float        # 马赫数（亚声速支；=1 且 choked=True 表示壅塞夹断）
    p: float         # 静压 Pa
    T: float         # 静温 K
    rho: float       # 密度 kg/m³
    v: float         # 速度 m/s（大小，方向由网络流一向给出）
    choked: bool     # True = 给定 (p0,T0,mdot,A) 物理壅塞，Ma 夹在 1


def q_of_mach(ma: float, R: float, gamma: float) -> float:
    """流量函数 q(Ma)（无量纲）。"""
    tau = 1.0 + 0.5 * (gamma - 1.0) * ma * ma
    return np.sqrt(gamma / R) * ma * tau ** (-(gamma + 1.0) / (2.0 * (gamma - 1.0)))


def mach_from_q(q: float, R: float, gamma: float) -> tuple[float, bool]:
    """反解 q → Ma（亚声速支，二分；单调性保证唯一根）。

    q > q(1) 时壅塞：返回 (1.0, True)。
    """
    q_max = q_of_mach(1.0, R, gamma)
    if q >= q_max:
        return 1.0, True
    lo, hi = 0.0, 1.0            # q 单调增，根被夹住
    for _ in range(60):          # 2^-60 ≈ 1e-18，机器精度级
        mid = 0.5 * (lo + hi)
        if q_of_mach(mid, R, gamma) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi), False


def total_to_static(p0: float, T0: float, mdot: float, area: float,
                    R: float = 287.05, gamma: float = 1.4) -> StaticState:
    """端口四件套 (p0, T0, |mdot|, A) → 静参数（等熵关系）。

    mdot 取绝对值定马赫数（符号只表方向，不影响气动状态量大小）；
    返回的 v 恒为正，实际方向由调用方结合端口流向给出。
    """
    if p0 <= 0.0 or T0 <= 0.0:
        raise ValueError(f"总参数非物理: p0={p0}, T0={T0}")
    if area <= 0.0:
        raise ValueError(
            f"端口面积 {area} <= 0，无法恢复静参数（边界元件口 area=0 无动通量）")

    m = abs(mdot)
    if m == 0.0:                 # 滞止：静=总
        return StaticState(0.0, p0, T0, p0 / (R * T0), 0.0, False)

    q = m * np.sqrt(T0) / (p0 * area)
    ma, choked = mach_from_q(q, R, gamma)

    tau = 1.0 + 0.5 * (gamma - 1.0) * ma * ma
    T = T0 / tau
    p = p0 * tau ** (-gamma / (gamma - 1.0))
    rho = p / (R * T)
    v = ma * np.sqrt(gamma * R * T)
    return StaticState(ma, p, T, rho, v, choked)


# ---------------- 自验证（手算闭式核对，独立于元件代码） ----------------
if __name__ == "__main__":
    R, GAM = 287.05, 1.4

    # ① 设计点 Ma=0.5：先正算 mdot，再反解恢复，应逐项闭合
    ma_d, T0, p0, A = 0.5, 600.0, 3.0e5, 1.0e-4
    mdot = q_of_mach(ma_d, R, GAM) * p0 * A / np.sqrt(T0)
    s = total_to_static(p0, T0, mdot, A, R, GAM)
    tau_d = 1.0 + 0.2 * 0.25                      # 1.05（γ=1.4: (γ-1)/2=0.2）
    hand = {                                       # 手算闭式
        "ma": 0.5,
        "T": 600.0 / 1.05,                         # 571.4286 K
        "p": 3.0e5 / 1.05 ** 3.5,                  # 252905.75 Pa
        "v": 0.5 * np.sqrt(GAM * R * 600.0 / 1.05),
    }
    print(f"① 设计点 Ma=0.5（mdot={mdot:.6f} kg/s 由 q 正算）")
    print(f"   恢复 Ma={s.ma:.6f}  T={s.T:.4f}  p={s.p:.2f}  "
          f"rho={s.rho:.4f}  V={s.v:.2f}  choked={s.choked}")
    print(f"   手算     Ma={hand['ma']}  T={hand['T']:.4f}  p={hand['p']:.2f}  "
          f"V={hand['v']:.2f}")
    ok1 = (abs(s.ma - 0.5) < 1e-10 and abs(s.T - hand["T"]) < 1e-8
           and abs(s.p - hand["p"]) < 1e-4 and abs(s.v - hand["v"]) < 1e-6)

    # ② 壅塞互检：q(1) 必须等于 orifice.py 超临界公式系数（Cd=1）
    q1 = q_of_mach(1.0, R, GAM)
    orifice_coef = np.sqrt(GAM / R) * (2.0 / (GAM + 1.0)) ** (
        (GAM + 1.0) / (2.0 * (GAM - 1.0)))
    print(f"\n② 壅塞互检: q(1)={q1:.10f}  orifice超临界系数={orifice_coef:.10f}"
          f"  Δ={abs(q1 - orifice_coef):.2e}")
    ok2 = abs(q1 - orifice_coef) < 1e-12

    # ③ 零流量 → 滞止（静=总）
    s0 = total_to_static(2.0e5, 500.0, 0.0, 1.0e-4, R, GAM)
    print(f"\n③ 零流量: Ma={s0.ma}  p={s0.p}（=p0）  T={s0.T}（=T0）  "
          f"V={s0.v}  rho={s0.rho:.4f}")
    ok3 = s0.ma == 0.0 and s0.p == 2.0e5 and s0.v == 0.0

    # ④ 超物理流量 → 壅塞夹断
    s_c = total_to_static(3.0e5, 600.0, 10.0, 1.0e-4, R, GAM)  # 远超 q(1)
    tau1 = 1.0 + 0.2
    print(f"\n④ 壅塞夹断: Ma={s_c.ma}  choked={s_c.choked}  "
          f"T={s_c.T:.2f}（=600/1.2={600.0 / tau1:.2f}）  "
          f"p={s_c.p:.1f}（=3e5/1.2^3.5={3.0e5 / tau1 ** 3.5:.1f}）")
    ok4 = s_c.choked and s_c.ma == 1.0

    # ⑤ 非法输入报错
    try:
        total_to_static(3.0e5, 600.0, 0.1, 0.0, R, GAM)   # 边界元件口 area=0
        ok5 = False
    except ValueError:
        ok5 = True
    print(f"\n⑤ area=0 报错: {'OK' if ok5 else 'FAIL'}")

    all_ok = ok1 and ok2 and ok3 and ok4 and ok5
    print("\n" + "=" * 60)
    print("OK fluids.isentropic 全部验证通过" if all_ok else
          f"FAIL 未通过项: "
          f"{[n for n, o in zip('12345', [ok1, ok2, ok3, ok4, ok5]) if not o]}")
    raise SystemExit(0 if all_ok else 1)
