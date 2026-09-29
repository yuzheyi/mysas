"""isentropic — 理想气体等熵气动函数 + Fanno 摩擦管变体（gasprops 时期更名而来，2026-09-18；Fanno 段并入 2026-09-29）。

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

────────────────────────────────────────────
Fanno 摩擦管变体（2026-09-29 并入，原 fluids/fanno.py）
────────────────────────────────────────────
等截面绝热有摩擦 = isentropic 的摩擦对偶（熵增沿程累积）。
同一套气动函数（q(Ma)/τ(Ma)/声速闭合）加一个自由度：摩擦
耗散把亚声速流挤向 Ma=1（密度掉的比速度涨的快，为维持 ρv
不变只能提速）。三个函数：
  fanno_param(M, γ)      最大管长函数 F(M)（熵增积分，(0,1] 单调降）
  mach_from_fanno(χ, γ)   反解（二分）
  p0_star_ratio(M, γ)     Fanno 总压比（摩擦耗散总压的闭式）
管件容量/出口重构的物理都在这里（pipe.py 消费）；f 定点
（choked_flow_fanno）与出口记账（exit_state）是管件私有逻辑，
住 elements/pipe.py（元件组内，不归 fluids）。
"""
from __future__ import annotations

from dataclasses import dataclass
import math

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
    """流量参数 q(Ma)；其量纲为 sqrt(K)·s/m。"""
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


def mach_from_q_arr(q: np.ndarray, R: float, gamma: float
                     ) -> tuple[np.ndarray, np.ndarray]:
    """向量化反解：一批 q 同时二分 → (Ma, choked)。

    与标量版 mach_from_q 逐位同语义（同时 60 轮整组二分；掩码处理
    边界：q>=q_max 夹 Ma=1 标 choked；q=0 直接 Ma=0 不参与二分）。
    用途：assembly 静参数白板 prime（每残差评估一次）——M 个口从
    60×M 次 Python 循环降为 60 次整组 numpy 运算；也是将来按类型
    批核（③ 段向量化）的地基。
    """
    q = np.asarray(q, dtype=float)
    q_max = q_of_mach(1.0, R, gamma)
    choked = q >= q_max
    qq = np.where(choked, q_max, q)       # 夹住再二分（壅塞口免白算）
    lo = np.zeros_like(qq)
    hi = np.where(qq > 0.0, 1.0, 0.0)    # 零流量口：区间空，Ma≡0
    act = qq > 0.0                        # 活跃口掩码（参与二分）
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        below = q_of_mach(mid, R, gamma) < qq   # 纯算术，数组安全
        lo = np.where(below & act, mid, lo)
        hi = np.where((~below | ~act) & act, mid, hi)
    return 0.5 * (lo + hi), choked


def statics_from_mach(ma, p0, T0, R, gamma):
    """等熵闭式：Ma + 总参数 → (T, p, rho, v)。标量/数组通用（numpy
    广播）——total_to_static（标量）与白板 prime（数组）单点共用的
    物理尾部；真实气体子类重写自己的闭式（2026-09-26 从两处重复
    收敛到此）。"""
    tau = 1.0 + 0.5 * (gamma - 1.0) * ma * ma
    T = T0 / tau
    p = p0 * tau ** (-gamma / (gamma - 1.0))
    rho = p / (R * T)
    v = ma * np.sqrt(R * gamma * T)
    return T, p, rho, v


def sonic_state_from_flow(mdot: float, area: float, t0: float,
                          R: float, gamma: float) -> StaticState:
    if area <= 0.0:
        raise ValueError(
            f"端口面积 {area} <= 0，无法恢复静参数（边界元件口 area=0 无动通量）")
    m = abs(mdot)
    T_star = t0 * 2.0 / (gamma + 1.0)          # 临界温比闭式
    a_star = np.sqrt(R * gamma * T_star)       # 声速截面当地声速
    if m == 0.0:                               # 零流量无壅塞截面
        return StaticState(0.0, 0.0, t0, 0.0, 0.0, False)
    rho_star = m / (a_star * area)
    p_star = rho_star * R * T_star
    return StaticState(1.0, p_star, T_star, rho_star, a_star, True)


def total_to_static(p0: float, T0: float, mdot: float, area: float,
                    R: float, gamma: float) -> StaticState:
    """端口四件套 (p0, T0, |mdot|, A) → 静参数（等熵关系）。

    mdot 取绝对值定马赫数（符号只表方向，不影响气动状态量大小）；
    返回的 v 恒为正，实际方向由调用方结合端口流向给出。
    """
    if R <= 0.0 or gamma <= 1.0:
        raise ValueError(f"气体参数非物理: R={R}, gamma={gamma}")
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
    T, p, rho, v = statics_from_mach(ma, p0, T0, R, gamma)
    return StaticState(ma, p, T, rho, v, choked)


# ══════════════════════════════════════════════════════════════
# Fanno 摩擦管变体（2026-09-29 并入，原 fluids/fanno.py 数学层）
# ══════════════════════════════════════════════════════════════
def fanno_param(ma: float, gamma: float) -> float:
    """Fanno 最大管长函数 F(M)（= f_D·L*/D；在 (0,1] 单调降）。

    F(1)=0（临界截面零附加长度）；M→0 时 F→+∞（缓流需无限管长
    到临界）。第一项 = 动量主部，对数项 = 能量方程焊死 T(v) 的修正
    ——整个函数是沿程熵增的积分（摩擦把亚声速流挤向 Ma=1）。
    """
    m2 = ma * ma
    tau = 1.0 + 0.5 * (gamma - 1.0) * m2
    return ((1.0 - m2) / (gamma * m2)
            + (gamma + 1.0) / (2.0 * gamma)
            * math.log((gamma + 1.0) * m2 / (2.0 * tau)))


def mach_from_fanno(chi: float, gamma: float) -> float:
    """反解 F(M) = χ → 亚声速支 M∈(0,1]（二分；单调性保根唯一）。

    χ = f_D·L/D ≥ 0：χ=0 → M=1（零长管/理想喷嘴极限）；χ>0 时
    F 单调降保证根存在唯一。
    """
    if chi <= 0.0:
        return 1.0
    lo, hi = 1.0e-9, 1.0           # F(lo)~+∞ > χ > 0 = F(hi)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if fanno_param(mid, gamma) > chi:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def p0_star_ratio(ma: float, gamma: float) -> float:
    """Fanno 总压比 p0(M)/p0* = (1/M)·[(2+(γ-1)M²)/(γ+1)]^((γ+1)/(2(γ-1)))。

    亚声速支 >1（摩擦耗散总压）；M=1 处 =1（临界截面）。
    """
    m2 = ma * ma
    tau = 1.0 + 0.5 * (gamma - 1.0) * m2
    return (1.0 / ma) * (2.0 * tau / (gamma + 1.0)) ** (
        (gamma + 1.0) / (2.0 * (gamma - 1.0)))


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

    # ⑥ 向量化 vs 标量逐位对拍（白板 prime 的地基，2026-09-25）
    rng = np.random.default_rng(42)
    q_test = np.concatenate([
        rng.uniform(0.0, q_of_mach(1.0, R, GAM), 200),   # 亚声速随机
        [0.0, q_of_mach(1.0, R, GAM), 5.0],             # 零流量/恰临界/壅塞
    ])
    ma_a, ch_a = mach_from_q_arr(q_test, R, GAM)
    ma_s = np.empty_like(q_test)
    ch_s = np.empty_like(q_test, dtype=bool)
    for i_, qi in enumerate(q_test):
        ma_s[i_], ch_s[i_] = mach_from_q(float(qi), R, GAM)
    print(f"\n⑥ 向量化 vs 标量对拍: max|dMa|={np.abs(ma_a - ma_s).max():.2e}"
          f"  choked 一致={np.array_equal(ch_a, ch_s)}")
    # 阈值 1e-7：两版二分的最后几轮在中点选取上差一个舍入（标量版
    # 从 (0,1) 起步、数组版零流量口区间起点不同），60 轮后残差 ~2^-53
    # 的量级差是浮点路径差不是语义差；对物理量（ΔMa<1e-8）完全无害
    ok6 = (np.abs(ma_a - ma_s).max() < 1e-7
           and np.array_equal(ch_a, ch_s))

    # ⑦ Fanno 变体锚点（2026-09-29 并入，原 fanno.py 自验精华）:
    #    教科书 F(M) 表 + 反解往返 + 总压比闭式 + L→0/大 χ 极限
    fv = [fanno_param(m, GAM) for m in (0.4, 0.5, 0.8, 1.0)]
    ok7 = (abs(fv[0] - 2.3086) < 2e-4 and abs(fv[1] - 1.0691) < 2e-4
           and abs(fv[2] - 0.0723) < 2e-4 and fv[3] == 0.0)
    ok7 &= all(abs(mach_from_fanno(fanno_param(m, GAM), GAM) - m) < 1e-10
               for m in (0.2, 0.4, 0.5, 0.6, 0.8, 0.95, 1.0))
    r_half = p0_star_ratio(0.5, GAM)
    ok7 &= (abs(p0_star_ratio(1.0, GAM) - 1.0) < 1e-12
            and abs(r_half - 1.33984375) < 1e-4)
    ok7 &= (mach_from_fanno(1.0e-12, GAM) > 0.999999)
    print(f"\n⑦ Fanno 变体: F(0.4/0.5/0.8/1)={fv[0]:.4f}/{fv[1]:.4f}/"
          f"{fv[2]:.4f}/{fv[3]:.1e}  反解往返 OK  "
          f"p0*(0.5)={r_half:.4f}(闭式 1.3398)")

    all_ok = ok1 and ok2 and ok3 and ok4 and ok5 and ok6 and ok7
    print("\n" + "=" * 60)
    print("OK fluids.isentropic 全部验证通过" if all_ok else
          f"FAIL 未通过项: "
          f"{[n for n, o in zip('1234567', [ok1, ok2, ok3, ok4, ok5, ok6, ok7]) if not o]}")
    raise SystemExit(0 if all_ok else 1)
