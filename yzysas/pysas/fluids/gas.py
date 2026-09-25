"""gas — 流体对象：物性定律 + 气动关系门面（2026-09-25 类化重构）。

一个流体 = 一个实例（网络全流一种流体；多流体远期再议）。
物性已在三个地方散落（ctx.gas_R/gamma/mu 散字段、properties 按参数算、
isentropic 甩 R/gamma 传参）——M5 篦齿的 μ(T) 一进来就得收拢，类化
一次做干净。设计两层:

  IdealGas（本文件）   唯一公开出口：物性 + 气动关系的门面/配置
  isentropic.py        纯函数算法体（叶子，不导出给外界）
  properties.py        常比热闭式（内部实现）

变比热/真实气体的升级路：子类重写 cp(T)/total_to_static（策略模式，
理想气体闭式留作参考实现）——不是半状态，是两个实现。

粘度口径（Sutherland，空气默认）:
  μ(T) = μ_ref · (T/T_ref)^1.5 · (T_ref + S)/(T + S)
  空气: μ_ref=1.716e-5 Pa·s @ T_ref=273.15 K, S=110.4 K
  JSON 兼容：gas 块显式给 "mu" → 常数粘度（旧算例数值不变）；
  缺省 → 空气 Sutherland（Re/Hagen 的 μ 取上游口静温，与 ρs 同口径）。
"""
from __future__ import annotations

import numpy as np

from pysas.fluids.isentropic import (
    StaticState, mach_from_q, mach_from_q_arr, q_of_mach, total_to_static)


class IdealGas:
    """理想气体（常比热 + Sutherland 粘度）：一个流体一个实例。"""

    def __init__(self, R: float = 287.05, gamma: float = 1.4,
                 mu: float | None = None,
                 mu_ref: float = 1.716e-5, T_ref: float = 273.15,
                 S: float = 110.4):
        if R <= 0.0 or gamma <= 1.0:
            raise ValueError(f"气体参数非物理: R={R}, gamma={gamma}")
        self.R, self.gamma = R, gamma
        self._mu_const = mu                  # None → Sutherland 模式
        self._mu_ref, self._T_ref, self._S = mu_ref, T_ref, S

    # ---------- 物性（签名带 T：变比热/Sutherland 的兼容钩子） ----------
    def cp(self, T: float | None = None) -> float:
        """定压比热 J/(kg·K)（等比热；T 钩子留给变比热子类）。"""
        return self.R * self.gamma / (self.gamma - 1.0)

    def cv(self, T: float | None = None) -> float:
        """定容比热 J/(kg·K)（M4 容腔能量用）。"""
        return self.R / (self.gamma - 1.0)

    def mu(self, T: float | None = None) -> float:
        """动力粘度 Pa·s。

        常数模式：恒返 mu_const（JSON 显式 mu；旧算例口径）。
        Sutherland 模式：μ(T)，T=None 回落 μ_ref（缩放等无需温度的
        调用方——量级参考，不参与收敛解）。
        """
        if self._mu_const is not None:
            return self._mu_const
        if T is None:
            return self._mu_ref
        r = T / self._T_ref
        return self._mu_ref * r * np.sqrt(r) * (self._T_ref + self._S) / (T + self._S)

    # ---------- 气动关系（委托 isentropic 纯函数，甩掉 R/gamma 传参） ----------
    def q_of_mach(self, ma: float) -> float:
        return q_of_mach(ma, self.R, self.gamma)

    def mach_from_q(self, q: float) -> tuple[float, bool]:
        """标量反解（自验/手算/单件调试用；求解路径走白板向量化版）。"""
        return mach_from_q(q, self.R, self.gamma)

    def mach_from_q_arr(self, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """向量化反解（assembly 白板 prime 用）。"""
        return mach_from_q_arr(q, self.R, self.gamma)

    def total_to_static(self, p0: float, T0: float, mdot: float,
                        area: float) -> StaticState:
        """端口四件套 → 静参数（白板回退/后处理/手算用）。"""
        return total_to_static(p0, T0, mdot, area, self.R, self.gamma)


# ---------------- 自验证（Sutherland 手算 + 与 isentropic 委托一致） ----------------
if __name__ == "__main__":
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

    # ⑤ 非物理参数报错
    try:
        IdealGas(R=-1.0)
        ok.append(False)
    except ValueError:
        print("⑤ 非物理参数报错: OK")
        ok.append(True)

    all_ok = all(ok)
    print("\n" + "=" * 60)
    print("OK fluids.gas（IdealGas）全部验证通过" if all_ok else "FAIL 未全过")
    if not all_ok:
        raise SystemExit(1)
