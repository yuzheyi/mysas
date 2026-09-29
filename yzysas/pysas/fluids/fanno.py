"""fanno — Fanno 摩擦管流（理想气体算法体，isentropic 的管流对偶，2026-09-27）。

核心：choked_flow_fanno —— 绝热等截面摩擦管的真容量上限，是
isentropic.choked_flow（理想喷嘴）在有限管长下的物理修正。

物理（绝热等截面摩擦管 = Fanno 流）:
  T0 沿程守恒（能量方程）；p0 单调下降（摩擦熵增）；亚声速支沿程
  **加速**趋向 Ma=1（摩擦把亚声速流"挤"向临界——与直觉相反的根源：
  密度下降快于流量守恒要求的速度上升）。
  最大管长函数（f_D = Darcy 摩擦系数；教科书 4f_Fanning·L*/D，f_D=4f_F）:
      f_D·L*/D = F(M) = (1-M²)/(γM²)
                  + (γ+1)/(2γ)·ln[ (γ+1)M² / (2+(γ-1)M²) ]
  F 在 (0,1] 单调降，F(1)=0（临界截面零附加长度），M→0 时 F→+∞。
  管件壅塞条件 L = L*(M_in)——出口截面达 Ma=1:
      M_in = F⁻¹(f_D·L/D),   ṁ_cap = q(M_in)·p0·A/√T0
  连续退化（自洽边界）:
      L→0   →  M_in→1 → cap = q(1)·p0·A/√T0 = 理想喷嘴（isentropic 口径）
               理想喷嘴是零长度 Fanno 管——两种 cap 同一物理家族
      L→∞   →  M_in→0 → cap→0（无限长管通不过任何流量）

摩擦系数闭合（差分雅可比安全，延续想法 27 纪律）:
  f_D = Swanee-Jain(Re(cap))——Re 取壅塞流自身的 Re（q 定点自洽）。
  为什么不用 Re(ṁ_guess)：零流量初值处 f·L/D→∞ → cap→0，且
  cap ∝ √ṁ_guess → 导数 1/√m 在零点爆炸（pipe docstring 明确拒绝的
  同款奇异性）。定点闭合后 cap 是 (A,D,L,ε,p0,T0) 的纯函数，与迭代
  状态无关——零初值处 cap 仍有限，雅可比干净；cap 对 p0 仅弱非线性
  （经 Re 的灵敏度 ~0.2），min 结构的 kink 处理不变。
  μ 取 μ(T0)（Fanno 沿程 T0 守恒；近声速截面 T*=0.833·T0，Re 口径
  相差 ~10%，原型容差）。

Re 恒等式（定点的简并结构）: Re = |ṁ|·D/(μ·A) = q·p0·D/(μ·√T0)
  —— A 消去：定点活在无量纲 q 上，与管面积无关的标量问题；面积只在
  最后 ṁ = q·p0·A/√T0 一步回场。（自验验证此标度不变性。）

导数行为:
  cap(p0) = q*(p0)·p0·A/√T0：光滑、单调增；∂cap/∂p_down = 0（壅塞
  与下游解耦 = min 结构本意）。q* 定点压缩因子 ~0.1（f 对 Re 的
  灵敏度 ~Re^-0.2，再经 F⁻¹ 衰减），~8 轮到 1e-14。

cap 的诚实边界（登记待定问题 9）: Darcy 需求曲线是不可压公式，高 Ma
  时高估亚声速容量 → min 结构偏保守地提前钳位。完备解 = Fanno 亚声速
  特性（出口静压匹配）替换需求曲线；本文件先给 cap 侧，需求侧维持
  （M1 手算算例全在 Darcy 曲线上）。
"""
from __future__ import annotations

import math

# 脚本直跑自举（本模块无注册副作用，直跑安全——见文末 __main__）；
# 包内正常导入时此三行是空操作（pysas 已在 sys.path）
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from pysas.fluids.isentropic import q_of_mach


def fanno_param(ma: float, gamma: float) -> float:
    """Fanno 最大管长函数 F(M)（= f_D·L*/D；在 (0,1] 单调降）。

    F(1)=0（临界截面）；M→0 时 F→+∞（缓流需无限管长到临界）。
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

    亚声速支 >1（摩擦耗散总压）；M=1 处 =1（临界截面）。出口静参数
    重构用：p0_exit = p0_star_ratio(M_exit)·p0*，其中 p0* 由
    sonic_state_from_flow(ṁ, A, T0) 连续性闭合上溯（两段在 Ma=1
    精确铰链——q(M)·p0 = ṁ√T0/A 沿 Fanno 线守恒是构造保证）。
    """
    m2 = ma * ma
    tau = 1.0 + 0.5 * (gamma - 1.0) * m2
    return (1.0 / ma) * (2.0 * tau / (gamma + 1.0)) ** (
        (gamma + 1.0) / (2.0 * (gamma - 1.0)))


def _swanee_jain(re: float, diameter: float, roughness: float) -> float:
    """Swanee-Jain 摩擦系数（与 pipe._friction 同公式，标量 math 版——
    热路径上 60 轮二分不走 numpy 分发）。"""
    term = max(roughness / (3.7 * diameter) + 5.74 / re ** 0.9, 1.0e-12)
    return 0.25 / (math.log10(term) ** 2)


def choked_flow_fanno(area: float, p0: float, t0: float,
                      length: float, diameter: float, roughness: float,
                      R: float, gamma: float, mu: float) -> float:
    """摩擦管（Fanno）壅塞流量上限 ṁ = q*·p0·A/√T0。

    q* 定点自洽：q* = q(M_in)，M_in = F⁻¹(f(Re(q*))·L/D)，
    Re = q·p0·D/(μ·√T0)（A 消去，见模块 docstring）。
    非正参数 → 0（与 gas.choked_flow 同守卫）；L≤0 退化为理想喷嘴 cap。
    """
    if area <= 0.0 or p0 <= 0.0 or t0 <= 0.0 or mu <= 0.0:
        return 0.0
    if length <= 0.0 or diameter <= 0.0:
        return q_of_mach(1.0, R, gamma) * p0 * area / math.sqrt(t0)
    q = q_of_mach(1.0, R, gamma)            # 定点起点 = 理想 cap（上界）
    for _ in range(50):
        re = q * p0 * diameter / (mu * math.sqrt(t0))
        f = _swanee_jain(re, diameter, roughness)
        q_new = q_of_mach(mach_from_fanno(f * length / diameter, gamma),
                          R, gamma)
        if abs(q_new - q) <= 1.0e-14 * q:
            return q_new * p0 * area / math.sqrt(t0)
        q = q_new
    return q * p0 * area / math.sqrt(t0)    # 未收敛（理论边界）：返回现值


# ---------------- 自验证（手算闭式核对，独立于元件代码） ----------------
# 本模块无注册副作用 → 脚本直跑安全（同 isentropic.py 纪律）:
#   python -X utf8 pysas\fluids\fanno.py
if __name__ == "__main__":
    import numpy as np

    from pysas.fluids.ideal_gas import IdealGas

    R, GAM = 287.05, 1.4
    air = IdealGas()
    ok = []

    # ① 教科书 Fanno 表锚点（γ=1.4, 4fL*/D 列）: M=0.4→2.3086,
    #    M=0.5→1.0691, M=0.8→0.0723, M=1→0
    vals = [fanno_param(m, GAM) for m in (0.4, 0.5, 0.8, 1.0)]
    print(f"① F(M) 表锚点: F(0.4)={vals[0]:.4f}(2.3086)  F(0.5)={vals[1]:.4f}"
          f"(1.0691)  F(0.8)={vals[2]:.4f}(0.0723)  F(1)={vals[3]:.4e}(0)")
    ok.append(abs(vals[0] - 2.3086) < 2e-4 and abs(vals[1] - 1.0691) < 2e-4
              and abs(vals[2] - 0.0723) < 2e-4 and vals[3] == 0.0)

    # ② 反解往返: M = F⁻¹(F(M))（含 F(1)=0 → χ=0 直接返 1）
    rt = [abs(mach_from_fanno(fanno_param(m, GAM), GAM) - m) < 1e-10
          for m in (0.2, 0.4, 0.5, 0.6, 0.8, 0.95, 1.0)]
    print(f"② 反解往返: {'全通过' if all(rt) else rt}")
    ok.append(all(rt))

    # ③ 极限: χ→0 → M→1（理想喷嘴）；χ 大 → M 小（长管缓流；
    #    首阶渐近 √(1/(γχ)) 忽略对数项 ~4% @χ=100，容差 5% 如实标注）
    m_chi0 = mach_from_fanno(1.0e-12, GAM)
    m_big = mach_from_fanno(100.0, GAM)
    print(f"③ 极限: M(χ=1e-12)={m_chi0:.10f}(→1)  M(χ=100)={m_big:.6f}"
          f"（首阶渐近√(1/(γχ))={np.sqrt(1.0 / (1.4 * 100.0)):.6f}，"
          f"偏差 {(m_big / np.sqrt(1.0 / (1.4 * 100.0)) - 1) * 100:.1f}%"
          f"=对数修正项）")
    ok.append(m_chi0 > 0.999999
              and abs(m_big / np.sqrt(1.0 / (1.4 * 100.0)) - 1.0) < 0.05)

    # ④ 本算例锚点（D=0.02, L=0.5, ε=1e-5, p0=3e5, T0=600）:
    #    独立路径（对 M_in 外层二分）与库内定点同根；q*/q(1) ≈ 0.845
    #    （cap 比理想喷嘴低 ~15.5%，M_in ≈ 0.61——入口亚声速加速图景）
    D, L, EPS, P0, T0 = 0.02, 0.5, 1.0e-5, 3.0e5, 600.0
    mu = air.mu(T0)

    def g(m):     # F(M) - f(Re(q(M)))·L/D，单调降
        q_m = q_of_mach(m, R, GAM)
        re = q_m * P0 * D / (mu * np.sqrt(T0))
        return (fanno_param(m, GAM)
                - 0.25 / np.log10(EPS / (3.7 * D) + 5.74 / re ** 0.9) ** 2
                * L / D)

    lo_m, hi_m = 1.0e-6, 1.0
    for _ in range(200):
        mid = 0.5 * (lo_m + hi_m)
        if g(mid) > 0.0:
            lo_m = mid
        else:
            hi_m = mid
    m_hand = 0.5 * (lo_m + hi_m)
    q_hand = q_of_mach(m_hand, R, GAM)
    cap = choked_flow_fanno(3.1416e-4, P0, T0, L, D, EPS, R, GAM, mu)
    cap_ideal = air.choked_flow(3.1416e-4, P0, T0)
    ratio = cap / cap_ideal
    print(f"④ 定点 vs 外层二分: M_in={m_hand:.8f}  q*={q_hand:.8f}"
          f"  q(定点)={cap / (P0 * 3.1416e-4 / np.sqrt(T0)):.8f}")
    print(f"   cap={cap:.6f}  ideal={cap_ideal:.6f}  比值={ratio:.4f}"
          f"（手算 M_in≈0.608 → q/q(1)≈0.845）")
    ok.append(abs(cap / (P0 * 3.1416e-4 / np.sqrt(T0)) - q_hand) < 1e-12
              and 0.80 < ratio < 0.89 and 0.55 < m_hand < 0.65)

    # ⑤ q* 标度不变性（A 消去）: 面积加倍 → cap 精确加倍
    cap2x = choked_flow_fanno(2 * 3.1416e-4, P0, T0, L, D, EPS, R, GAM, mu)
    print(f"⑤ 面积加倍: cap {cap:.8f} -> {cap2x:.8f}"
          f"  比值={cap2x / cap:.12f}(=2)")
    ok.append(abs(cap2x / cap - 2.0) < 1e-12)

    # ⑥ 临界截面一致性: 连续性声速闭合 p* == q*·p0·√(2R/(γ(γ+1)))
    #    （isentropic.sonic_state_from_flow 与 Fanno cap 的解析铰链）
    from pysas.fluids.isentropic import sonic_state_from_flow
    st_son = sonic_state_from_flow(cap, 3.1416e-4, T0, R, GAM)
    p_star_cf = q_hand * P0 * np.sqrt(2.0 * R / (GAM * (GAM + 1.0)))
    print(f"⑥ 声速截面 p*: 连续性={st_son.p:.4f}  闭式={p_star_cf:.4f}"
          f"  Δ={abs(st_son.p - p_star_cf):.2e}")
    ok.append(abs(st_son.p - p_star_cf) < 1e-6 * p_star_cf
              and st_son.ma == 1.0 and st_son.choked)

    # ⑦ L→0 退化 = 理想喷嘴（同源家族的数值验证）
    cap_l0 = choked_flow_fanno(3.1416e-4, P0, T0, 1.0e-9, D, EPS, R, GAM, mu)
    print(f"⑦ L→0: cap={cap_l0:.8f}  ideal={cap_ideal:.8f}"
          f"  Δ={abs(cap_l0 - cap_ideal):.2e}")
    ok.append(abs(cap_l0 - cap_ideal) < 1e-6 * cap_ideal)

    # ⑧ p0_star_ratio 锚点: M=1 → 1；M=0.5 → 1.3398
    #    （=2×(2.1/2.4)³ 精确值；与表格列 T/T*=2.4/2.1、p/p*=2.1381、
    #     等熵链 p0/p0*=(p/p*)·(1.05^3.5)/(1.2^3.5) 三路互推闭合）
    r1 = p0_star_ratio(1.0, GAM)
    r_half = p0_star_ratio(0.5, GAM)
    print(f"⑧ p0_star_ratio: M=1 → {r1:.12f}(=1)  M=0.5 → {r_half:.4f}"
          f"（闭式 2×(2.1/2.4)³=1.3398）")
    ok.append(abs(r1 - 1.0) < 1e-12 and abs(r_half - 1.33984375) < 1e-4)

    all_ok = all(ok)
    print("\n" + "=" * 60)
    print("OK fluids.fanno 全部验证通过" if all_ok else
          f"FAIL 未通过项: "
          f"{[n for n, o in zip('12345678', ok) if not o]}")
    raise SystemExit(0 if all_ok else 1)
