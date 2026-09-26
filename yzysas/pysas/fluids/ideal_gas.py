"""ideal_gas — 理想气体（常比热 + 等熵气动函数），GasModel 首个子类。

理想气体家族内部实现（q_of_mach / 标量 mach_from_q 等熵流量函数族）
在 isentropic.py（物理说明/壅塞论证/导数行为分析的完整文档在那）——
不进基类：真实气体没有等熵 q(Ma) 闭式，不共享。
自验 _selftest()（模块身份调用；副作用决定自验入口，勿 python -m）。
"""
from __future__ import annotations

import numpy as np

from pysas.fluids.base import GasModel, register_gas
from pysas.fluids.isentropic import (
    StaticState, mach_from_q, mach_from_q_arr, q_of_mach, statics_from_mach,
    total_to_static)


@register_gas
class IdealGas(GasModel):
    """理想气体（常比热）：R/gamma 定义，等熵关系委托 isentropic 纯函数。

    参数校验单点在构造器；粘度 = 空气 Sutherland（同 cp 一样自带
    实现；mu 显式给 = 常数粘度，供编程调用/敏感性实验——JSON 不再
    携带任何数值物性键，2026-09-26 口径）。"""

    def __init__(self, R: float = 287.05, gamma: float = 1.4,
                 mu: float | None = None,
                 mu_ref: float = 1.716e-5, T_ref: float = 273.15,
                 S: float = 110.4):
        if R <= 0.0 or gamma <= 1.0:
            raise ValueError(f"气体参数非物理: R={R}, gamma={gamma}")
        self.R, self.gamma = R, gamma
        self._mu_const = mu           # 空气粘度族（显式——勿移基类）
        self._mu_ref, self._T_ref, self._S = mu_ref, T_ref, S

    # ---------- 物性 ----------
    def mu(self, T: float | None = None) -> float:
        """动力粘度 Pa·s：常数模式恒返；Sutherland
        μ_ref·(T/T_ref)^1.5·(T_ref+S)/(T+S)（空气族）；
        T=None 回落 μ_ref（缩放等量级参考）。"""
        if self._mu_const is not None:
            return self._mu_const
        if T is None:
            return self._mu_ref
        r = T / self._T_ref
        return self._mu_ref * r * np.sqrt(r) * (self._T_ref + self._S) / (T + self._S)
    def cp(self, T: float | None = None) -> float:
        """定压比热 J/(kg·K) = γR/(γ−1)（等比热；T 钩子留给变比热子类）。"""
        return self.R * self.gamma / (self.gamma - 1.0)

    def cv(self, T: float | None = None) -> float:
        """定容比热 J/(kg·K) = R/(γ−1)（M4 容腔能量用）。"""
        return self.R / (self.gamma - 1.0)

    def rho_from_pT(self, p: float, T: float) -> float:
        """理想气体状态方程密度 ρ = p/(R·T)。"""
        return p / (self.R * T)

    # ---------- 气动关系（委托 isentropic 纯函数） ----------
    def q_of_mach(self, ma: float) -> float:
        """等熵流量函数 q(Ma)（理想气体家族内部用）。"""
        return q_of_mach(ma, self.R, self.gamma)

    def mach_from_q(self, q: float) -> tuple[float, bool]:
        """标量反解（自验/手算/单件调试用；求解路径走白板向量化版）。"""
        return mach_from_q(q, self.R, self.gamma)

    def mach_from_q_arr(self, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """向量化反解（assembly 白板 prime 用）。"""
        return mach_from_q_arr(q, self.R, self.gamma)

    def q_of_flow(self, mdot, p0, T0, area):
        """|ṁ| → q = |ṁ|·√T0/(p0·A)（标量/数组通用）。"""
        return np.abs(mdot) * np.sqrt(T0) / (p0 * area)

    def statics_from_mach(self, ma, p0, T0):
        """Ma + 总参数 → (T, p, rho, v)（等熵闭式，与 total_to_static 同源）。"""
        return statics_from_mach(ma, p0, T0, self.R, self.gamma)

    def total_to_static(self, p0: float, T0: float, mdot: float,
                        area: float) -> StaticState:
        """端口四件套 → 静参数。"""
        return total_to_static(p0, T0, mdot, area, self.R, self.gamma)

    def choked_flow(self, area: float, p0: float, T0: float) -> float:
        """壅塞流量上限（Cd=1）= q(1)·p0·A/√T0（isentropic 自验②同源）。"""
        if area <= 0.0 or p0 <= 0.0:
            return 0.0
        return self.q_of_mach(1.0) * p0 * area / np.sqrt(T0)


# ---------------- 自验证（Sutherland 手算 + 气动方法链 + 分发） ----------------
# 运行方式（模块身份，只注册一次；**不要 python -m / 脚本直跑**——
# runpy/脚本身份会重执行装饰器造同名新类，幂等守卫正确拒绝）：
#   python -c "import sys; sys.path.insert(0, r'...yzysas'); \
#             from pysas.fluids.ideal_gas import _selftest; _selftest()"
def _selftest():
    from pysas.fluids import (GasModel, available_gases, make_gas)
    air = IdealGas()                      # Sutherland 缺省
    ok = []

    # ① Sutherland 锚点：T_ref 处 μ = μ_ref；600K 手算 ≈ 3.02e-5
    mu_273 = air.mu(273.15)
    mu_600 = air.mu(600.0)
    hand_600 = 1.716e-5 * (600.0 / 273.15) ** 1.5 * (273.15 + 110.4) / (600.0 + 110.4)
    print(f"① Sutherland: mu(273.15)={mu_273:.4e}（=1.716e-5）  "
          f"mu(600)={mu_600:.4e}  手算={hand_600:.4e}")
    ok.append(abs(mu_273 - 1.716e-5) < 1e-12 and abs(mu_600 - hand_600) < 1e-12)

    # ② T=None 回落 μ_ref；常数模式恒定
    air_c = IdealGas(mu=1.8e-5)
    print(f"② T=None 回落={air.mu():.4e}  常数模式 mu(100/1000)="
          f"{air_c.mu(100.0):.4e}/{air_c.mu(1000.0):.4e}")
    ok.append(air.mu() == 1.716e-5
              and air_c.mu(100.0) == air_c.mu(1000.0) == 1.8e-5)

    # ③ 委托一致：与 isentropic 纯函数同参同果
    ma, ch = air.mach_from_q(air.q_of_mach(0.5))
    st = air.total_to_static(3.0e5, 600.0, 0.1, 1.0e-4)
    print(f"③ 委托一致: mach(q(0.5))={ma:.10f}  t2s.rho={st.rho:.6f}")
    ok.append(abs(ma - 0.5) < 1e-10 and st.rho > 0)

    # ④ cp/cv 闭式
    print(f"④ cp={air.cp():.4f}  cv={air.cv():.4f}  cp-cv={air.cp() - air.cv():.4f}（=R）")
    ok.append(abs(air.cp() - air.cv() - air.R) < 1e-12)

    # ⑤ 非物理参数报错；基类契约未实现报错
    try:
        IdealGas(R=-1.0)
        ok_a = False
    except ValueError:
        ok_a = True

    class _Bare(GasModel):   # 只测契约骨架（不注册）
        pass
    try:
        _Bare().cp()
        ok_b = False
    except NotImplementedError:
        ok_b = True
    try:
        _Bare().mu(300.0)   # 粘度也是契约（无静默继承的默认粘度）
        ok_m = False
    except NotImplementedError:
        ok_m = True
    print(f"⑤ 参数报错={ok_a}  契约 NotImplementedError={ok_b}  "
          f"mu 契约={ok_m}")
    ok.append(ok_a and ok_b and ok_m)

    # ⑥ 气动方法链：q_of_flow ↔ mach_from_q 往返（取壅塞流量一半，
    #    Ma≈0.46 亚声速稳妥）+ statics_from_mach 与 total_to_static 同源
    m_test = 0.5 * air.choked_flow(1.0e-4, 3.0e5, 600.0)
    ma_chk = air.mach_from_q(air.q_of_flow(m_test, 3.0e5, 600.0, 1.0e-4))[0]
    ma_ref = air.mach_from_q(0.5 * air.q_of_mach(1.0))[0]   # q 减半对应 Ma
    st_arr = air.statics_from_mach(ma_ref, 3.0e5, 600.0)
    st_ref = air.total_to_static(3.0e5, 600.0,
                                 air.q_of_mach(ma_ref) * 3.0e5 * 1.0e-4
                                 / np.sqrt(600.0), 1.0e-4)
    ok_arr = (abs(ma_chk - ma_ref) < 1e-9
              and abs(st_arr[0] - st_ref.T) < 1e-9
              and abs(st_arr[3] - st_ref.v) < 1e-6)
    print(f"⑥ q/statics 方法: mach(q(m))={ma_chk:.6f}(ref {ma_ref:.6f})  "
          f"T={st_arr[0]:.4f}(ref {st_ref.T:.4f})  v={st_arr[3]:.3f}")
    ok.append(ok_arr)

    # ⑦ 壅塞上限 = q(1) 闭式（与 orifice 超临界系数同源）
    fc = air.choked_flow(1.0e-4, 3.0e5, 600.0)
    fc_hand = (np.sqrt(1.4 / 287.05) * (2.0 / 2.4) ** (2.4 / 0.8)
               * 3.0e5 * 1.0e-4 / np.sqrt(600.0))
    print(f"⑦ choked_flow={fc:.8f}  闭式={fc_hand:.8f}  Δ={abs(fc - fc_hand):.1e}")
    ok.append(abs(fc - fc_hand) < 1e-10)

    # ⑧ make_gas 类型分发：缺省 IdealGas；未知 type 报错
    g_def = make_gas({"R": 200.0})
    try:
        make_gas({"type": "VanDerWaals"})
        ok_t = False
    except ValueError:
        ok_t = True
    print(f"⑧ make_gas: 缺省type={type(g_def).__name__} R={g_def.R}  "
          f"未知type报错={ok_t}  已注册={available_gases()}")
    ok.append(g_def.R == 200.0 and ok_t)

    # ⑨ 等熵算法体（原 isentropic 自验合入）：设计点闭式 + 壅塞互检 +
    #    零流量滞止 + 超物理壅塞 + area=0 报错 + 向量化对拍
    ma_d, T0d, p0d, Ad = 0.5, 600.0, 3.0e5, 1.0e-4
    md_d = q_of_mach(ma_d, 287.05, 1.4) * p0d * Ad / np.sqrt(T0d)
    s = air.total_to_static(p0d, T0d, md_d, Ad)
    tau_d = 1.05
    ok9 = (abs(s.ma - 0.5) < 1e-10
           and abs(s.T - T0d / tau_d) < 1e-8
           and abs(s.p - p0d / tau_d ** 3.5) < 1e-4
           and abs(s.v - 0.5 * np.sqrt(1.4 * 287.05 * T0d / tau_d)) < 1e-6)
    q1 = q_of_mach(1.0, 287.05, 1.4)
    coef = np.sqrt(1.4 / 287.05) * (2.0 / 2.4) ** (2.4 / 0.8)
    ok9 &= abs(q1 - coef) < 1e-12                    # q(1)=orifice 超临界系数
    s0 = air.total_to_static(2.0e5, 500.0, 0.0, 1.0e-4)
    ok9 &= s0.ma == 0.0 and s0.p == 2.0e5 and s0.v == 0.0
    sc = air.total_to_static(3.0e5, 600.0, 10.0, 1.0e-4)
    ok9 &= sc.choked and sc.ma == 1.0
    try:
        air.total_to_static(3.0e5, 600.0, 0.1, 0.0)
        ok_a0 = False
    except ValueError:
        ok_a0 = True
    ok9 &= ok_a0
    rng = np.random.default_rng(42)
    q_test = np.concatenate([rng.uniform(0.0, q1, 200), [0.0, q1, 5.0]])
    ma_a, ch_a = air.mach_from_q_arr(q_test)
    ma_s = np.empty_like(q_test)
    ch_s = np.empty_like(q_test, dtype=bool)
    for i_, qi in enumerate(q_test):
        ma_s[i_], ch_s[i_] = air.mach_from_q(float(qi))
    dmax = np.abs(ma_a - ma_s).max()
    ok9 &= dmax < 1e-7 and np.array_equal(ch_a, ch_s)
    print(f"⑨ 等熵体: 设计点闭式/壅塞互检/滞止/夹断/area0={ok9 and ok_a0}  "
          f"向量化对拍 dMa={dmax:.1e}")
    ok.append(bool(ok9))

    all_ok = all(ok)
    print("\n" + "=" * 60)
    print("OK fluids（GasModel 家族）全部验证通过" if all_ok else "FAIL 未全过")
    return all_ok



