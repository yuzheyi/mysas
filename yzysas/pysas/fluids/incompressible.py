"""incompressible — 不可压流体气动函数（isentropic 的液体对偶，2026-09-26）。

核心：total_to_static —— 端口四件套 (p0, T0, mdot, A) 直算静参数。
与理想气体版的本质差别：**免反解**。气体 q(Ma) 单调需 60 轮二分；
不可压下 v = |ṁ|/(ρ·A) 代数直达，Bernoulli 滞止关系一步闭式——
整个"流量参数 → 马赫"反演坍缩成一次除法。

物理（Bernoulli 滞止关系，常物性液体）:
  v  = |ṁ|/(ρ·A)            （连续性，ρ 常数）
  p  = p0 − ½ρv²            （机械能滞止——水 5 m/s 即 12.5 kPa，不可忽略）
  T  = T0 − v²/(2cp)        （动能温升——水 5 m/s 仅 ~0.003 K，量级微小）
  ma = v/a                  （声学马赫，a≈1480 m/s → 恒 ~1e-3，永不壅塞于声速）
  ρ 与 (p,T) 解耦：状态方程退化解耦（ρ=ρ(T) 慢变，网络解里视常数）

壅塞对偶——空化（cavitation）:
  气体: q > q(1)             → Ma 夹 1（面积-热力学流量上限）
  液体: p0 − ½ρv² < p_vap    → v 夹 v_cav = √(2(p0−p_vap)/ρ)，choked=True
  结构同形（限幅器保残差有限），物理不同：静压触底蒸气压（泵汽蚀/
  孔板闪蒸）。v_cav 依赖 p0（不像气体 q_max 是纯物性常数）→ 夹断
  放 total_to_static（p0 在手），不放"mach_from_q"步——LiquidWater
  类落地时 mach_from_q_arr 退化为 v/a 一次除法，白板 prime 编排不变。

边界情形:
  mdot = 0     → v=0，静=总（滞止）
  p0 ≤ p_vap   → 深度空化兜底：v 夹 0、p 夹 p_vap（线搜索试探点安全；
                 物理上网络不该解到这，收敛后应报表警告）
  area ≤ 0     → ValueError（同气体版：边界元件口无动通量）

导数行为（差分雅可比安全性）:
  v ∝ ṁ 线性 → 静参数对 ṁ 全程光滑（零点也光滑，连气体二分的舍入
  路径差都没有）；空化夹断处 kink（与气体壅塞夹断同族，可接受）；
  ½ρv² 使 p 对 ṁ 二次——多项式光滑，牛顿照常。

与 LiquidWater 类的关系（水真进网络那天）:
  本模块是算法体（同 isentropic 之于 IdealGas）：类持有 ρ/cp/a/p_vap
  身份参数（同 R/γ 之于 IdealGas），委托此处纯函数；届时 orifice
  喷嘴公式一并改（可压 β 指数律 vs 不可压 √(2ρΔp)——公式真分岔
  了，流量定律升 gas 方法才有实证支撑，想法 25 纪律的自我兑现）。
"""
from __future__ import annotations

import numpy as np

# 脚本直跑自举（本模块无注册副作用，直跑安全——见文末 __main__）；
# 包内正常导入时此三行是空操作（pysas 已在 sys.path）
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from pysas.fluids.isentropic import StaticState  # 容器共用（字段语义见下）


def statics_from_velocity(v, p0, T0, rho, cp):
    """Bernoulli 闭式：v + 总参数 → (T, p, rho, v)。标量/数组通用。

    与气体版 statics_from_mach 同位（物理尾部单点）；rho 恒返入参
    （不可压：状态方程退化解耦，密度不随 (p,T) 变）。"""
    T = T0 - v * v / (2.0 * cp)
    p = p0 - 0.5 * rho * v * v
    return T, p, rho, v


def v_cavitation(p0, rho, p_vap):
    """空化临界速度 v_cav = √(2(p0−p_vap)/ρ)（静压恰触蒸气压）；
    p0 ≤ p_vap 时 0（深度空化，由调用方兜底）。"""
    return np.sqrt(2.0 * max(p0 - p_vap, 0.0) / rho)


def total_to_static(p0, T0, mdot, area, rho, cp, a_sound, p_vap) -> StaticState:
    """端口四件套 (p0, T0, |mdot|, A) → 静参数（Bernoulli，直算免反解）。

    mdot 取绝对值（符号只表方向），v 恒为正；ma = v/a 声学马赫；
    choked=True = 静压触底蒸气压（空化夹断，气体壅塞的结构对偶）。
    液体物性 (rho, cp, a, p_vap) 显式传入——将来是 LiquidWater 的
    身份属性（同 R/gamma 之于 IdealGas）。
    """
    if rho <= 0.0 or cp <= 0.0 or a_sound <= 0.0:
        raise ValueError(f"液体参数非物理: rho={rho}, cp={cp}, a={a_sound}")
    if p0 <= 0.0 or T0 <= 0.0:
        raise ValueError(f"总参数非物理: p0={p0}, T0={T0}")
    if area <= 0.0:
        raise ValueError(
            f"端口面积 {area} <= 0，无法恢复静参数（边界元件口 area=0 无动通量）")

    v = abs(mdot) / (rho * area)          # 免反解：连续性直达（对照气体 60 轮二分）
    v_lim = v_cavitation(p0, rho, p_vap)
    choked = v >= v_lim
    v = min(v, v_lim)                     # 空化夹断（限幅器，同壅塞夹 Ma=1）
    T, p, rho_s, v = statics_from_velocity(v, p0, T0, rho, cp)
    if choked:
        p = p_vap            # 夹断处静压=蒸气压（构造使然；免 ½ρv² 重算的浮点尾差）
    else:
        p = max(p, p_vap)    # 深度空化兜底（p0≤p_vap 线搜索试探点：p 夹蒸气压、v 已夹 0）
    return StaticState(v / a_sound, p, T, rho_s, v, choked)


# ---------------- 自验证（手算闭式核对，独立于元件代码） ----------------
# 本模块无注册副作用 → 脚本直跑安全（不同于 ideal_gas 的 _selftest 纪律）:
#   python -X utf8 pysas\fluids\incompressible.py
if __name__ == "__main__":
    # 圆整参数（手算友好；真水 ρ≈998/cp≈4182/a≈1480/p_vap@20°C≈2.34e3）
    RHO, CP, A_SND, P_VAP = 1000.0, 4000.0, 1500.0, 2.0e3

    # ① 设计点 v=5 m/s：机械滞止 12.5 kPa 恢复、温升微小、声学马赫
    s = total_to_static(3.0e5, 600.0, 5.0, 1.0e-3, RHO, CP, A_SND, P_VAP)
    p_hand = 3.0e5 - 0.5 * RHO * 25.0          # 287500
    T_hand = 600.0 - 25.0 / (2.0 * CP)          # 599.996875
    ma_hand = 5.0 / A_SND                       # 3.3333e-3
    print(f"① 设计点 v=5: p={s.p:.1f}(手算 {p_hand:.1f})  "
          f"T={s.T:.6f}(手算 {T_hand:.6f})  ma={s.ma:.6e}  choked={s.choked}")
    ok1 = (abs(s.p - p_hand) < 1e-9 and abs(s.T - T_hand) < 1e-12
           and abs(s.ma - ma_hand) < 1e-15 and not s.choked)

    # ② 零流量 → 滞止（静=总）
    s0 = total_to_static(2.0e5, 500.0, 0.0, 1.0e-3, RHO, CP, A_SND, P_VAP)
    print(f"② 零流量: v={s0.v}  p={s0.p}（=p0）  T={s0.T}（=T0）  ma={s0.ma}")
    ok2 = s0.v == 0.0 and s0.p == 2.0e5 and s0.T == 500.0 and s0.ma == 0.0

    # ③ 线性倍增：ṁ 加倍 → v/ma 精确加倍（免反解的代价对照——气体
    #    版两流量各走独立二分，只有近似线性；这里代数精确）
    s1 = total_to_static(3.0e5, 600.0, 1.0, 1.0e-3, RHO, CP, A_SND, P_VAP)
    s2 = total_to_static(3.0e5, 600.0, 2.0, 1.0e-3, RHO, CP, A_SND, P_VAP)
    print(f"③ 线性: v(1)={s1.v:.6f} v(2)={s2.v:.6f} 比={s2.v / s1.v:.12f}"
          f"  ma 比={s2.ma / s1.ma:.12f}（应精确=2）")
    ok3 = s2.v == 2.0 * s1.v and s2.ma == 2.0 * s1.ma

    # ④ 空化夹断：ṁ=100 → 裸 v=100 m/s 远超 v_cav，夹断在蒸气压
    sc = total_to_static(3.0e5, 600.0, 100.0, 1.0e-3, RHO, CP, A_SND, P_VAP)
    v_cav_hand = np.sqrt(2.0 * (3.0e5 - P_VAP) / RHO)   # √596 ≈ 24.4131
    print(f"④ 空化: v={sc.v:.6f}(v_cav 手算 {v_cav_hand:.6f})  p={sc.p}（=p_vap）  "
          f"T={sc.T:.6f}（=600−596/8000）  choked={sc.choked}")
    ok4 = (sc.choked and abs(sc.v - v_cav_hand) < 1e-12
           and sc.p == P_VAP and abs(sc.T - (600.0 - 596.0 / 8000.0)) < 1e-12)

    # ⑤ 深度空化兜底：p0 < p_vap（线搜索试探点安全）→ v 夹 0、p 夹 p_vap
    sd = total_to_static(1.0e3, 300.0, 5.0, 1.0e-3, RHO, CP, A_SND, P_VAP)
    print(f"⑤ 深度空化: p0=1e3 < p_vap → v={sd.v}  p={sd.p}（夹 {P_VAP}）  "
          f"choked={sd.choked}（残差有限，报表应警告）")
    ok5 = sd.v == 0.0 and sd.p == P_VAP and sd.choked

    # ⑥ 非法输入报错（area=0 / rho≤0，与气体版同族）
    try:
        total_to_static(3.0e5, 600.0, 5.0, 0.0, RHO, CP, A_SND, P_VAP)
        ok6 = False
    except ValueError:
        pass
    try:
        total_to_static(3.0e5, 600.0, 5.0, 1.0e-3, -1.0, CP, A_SND, P_VAP)
        ok6 = False
    except ValueError:
        ok6 = True
    print(f"⑥ area=0 与 rho≤0 报错: {'OK' if ok6 else 'FAIL'}")

    # ⑦ 数组广播：statics_from_velocity 一批 v（白板 prime 的数组口径）
    v_arr = np.array([0.0, 5.0, 24.0])
    T_a, p_a, rho_a, v_a = statics_from_velocity(v_arr, 3.0e5, 600.0, RHO, CP)
    scal = [statics_from_velocity(float(vv), 3.0e5, 600.0, RHO, CP)
            for vv in v_arr]
    ok7 = (np.allclose(T_a, [t[0] for t in scal], atol=0, rtol=0)
           and np.allclose(p_a, [t[1] for t in scal], atol=0, rtol=0)
           and np.all(v_a == v_arr))
    print(f"⑦ 数组广播: 与标量逐个一致={ok7}（v=[0,5,24] 一次算）")

    all_ok = all([ok1, ok2, ok3, ok4, ok5, ok6, ok7])
    print("\n" + "=" * 60)
    print("OK fluids.incompressible 全部验证通过" if all_ok else "FAIL 未全过")
    raise SystemExit(0 if all_ok else 1)
