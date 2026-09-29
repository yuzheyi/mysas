"""pipe — 圆柱直管元件（Darcy 摩阻，层流/过渡/湍流三段 + 壅塞钳位）。

params = [长度 L m, 直径 D m, 粗糙度 eps m]（见 ElemType.PIPE docstring）

隐式形式 0 = f(m1, m2, p1, p2)（每口 1 方程，共 2 个）:
  f1 = ṁ1 + ṁ2                       （连续性）
  f2 = ṁ1 − ṁ_pipe(p1, p2, T)         （特性，ṁ1>0: 流体经口1流入 → 0→1 方向）

Darcy-Weisbach:  Δp = (f·L/D) · ρv²/2,  v = ṁ/(ρA)
  →  湍流  ṁ = ρA·√( 2ρΔp·D / (f·L) )   （f 依赖 Re → 依赖 ṁ 本身）
  →  层流  f = 64/Re 代回得线性律（Hagen-Poiseuille）:
          Δp = 32μLv/D²  →  ṁ = Δp·ρA·D²/(32μL)   【无 ṁ 依赖】

壅塞钳位（2026-09-26 引入，2026-09-27 口径升级 Fanno）:
  ṁ = min(ṁ_Darcy, ṁ_cap)，ṁ_cap = fluids.fanno.choked_flow_fanno
  管件真容量 = 有限长摩擦管 Fanno 上限（f_D·L/D 定点自洽）——
  理想喷嘴口径是它在 L→0 的退化（用户算例实证：D=0.02/L=0.5 的管
  用理想口径时四口全 Ma=1 假象，cap 高估 ~18%、入口 Ma=0.61 的
  物理图景全失）。下游压力从流量公式退场（下游扰动不回传上游；
  串联支路瓶颈自动涌现，详见开发日志想法 27 讨论）。
  判据自洽：cap 只看上游总参数+管几何，与白板 st_up.choked
  （运动学钳位）同源；min 结构分支点连续（kink，差分雅可比可处理）。

为什么层流段必须写显式线性律、不能沿用 "f=64/Re 代入湍流式"：
  后者是 ṁ 的隐式函数，在 ṁ→0 处 ∂ṁ_ideal/∂ṁ_guess ∝ √(1/ṁ) → ∞，
  差分雅可比在此爆炸（M1 坏初值实验实证：默认零流量初值下牛顿方向
  不可用）。线性律根部完全一致，但对 ṁ_guess 的导数恒为 0——雅可比干净。

分档（按 ṁ_guess 的 Re 选取权重 w，根部自动自洽）:
  Re(ṁ) ≤ 2000        层流：纯线性律
  2000 < Re(ṁ) < 4000 过渡：m = (1−w)·线性 + w·湍流（w 线性插值）
  Re(ṁ) ≥ 4000        湍流：Swanee-Jain（显式，免 Colebrook 迭代）
    f = 0.25 / [ log10( ε/(3.7D) + 5.74/Re^0.9 ) ]²
  收敛点自洽：层流根 Re≤2000 → w=0；湍流根 Re≥4000 → w=1；
  过渡带内 w 连续变化，物理上过渡区本就不唯一（经验区间），原型可接受。

注意：管内 ρ 取上游口截面的恢复静密度 ρs = ps/(R·Ts)（白板
_static_state，等熵关系由上游总态+流量反算；2026-09-25 口径升级——
原为滞止密度 p0/(RT0)，那是 v=0 假想态，流动密度应为静密度，
低 Ma 时两者相差 O(Ma²) 可忽略，高 Ma 时开始修正）。零流量时
ρs=ρ0（滞止），Hagen-Poiseuille 线性律的雅可比性质不变。
"""
from __future__ import annotations

import numpy as np

from pysas.elements.base import ElementModel
from pysas.fluids.fanno import (
    choked_flow_fanno, fanno_param, mach_from_fanno)
from pysas.fluids.isentropic import StaticState, sonic_state_from_flow

RE_LAM = 2300   # 层流上限 Re（工程惯例 2000~2300，取整便于插值分档）
RE_TURB = 3000  # 过渡区上限 Re


class PipeModel(ElementModel):
    elem_type = 2  # ElemType.PIPE

    def __init__(self, comp):
        super().__init__(comp)
        self.length, self.diameter, self.roughness = comp.params
        self.area = comp.ports[0].area

    # ---------- 物性 ----------
    def _re(self, mdot, rho, mu):
        """雷诺数 Re = |ṁ|·D/(μ·A) = ρ·v·D/μ（μ 取上游口静温的 Sutherland
        值——与 ρs 同口径，2026-09-25 随 IdealGas 类化升级；旧算例显式
        给 mu 常数则数值不变）。"""
        return abs(mdot) * self.diameter / (mu * self.area)

    def _friction(self, Re):
        """Swanee-Jain 摩擦系数（仅湍流段调用）。"""
        term = self.roughness / (3.7 * self.diameter) + 5.74 / Re ** 0.9
        return 0.25 / (np.log10(max(term, 1.0e-12)) ** 2)

    # ---------- 特性 ----------
    def mass_flow(self, p_up, p_down, rho_up, mu_up, mdot_guess, ctx, t0_up=None):
        """给定两端总压与上游静参数（ρs、μ(Ts)），返回管流量。

        rho_up/mu_up = 上游口恢复静密度与静温粘度（调用方从白板
        _static_state 取）；mdot_guess 只用于湍流/过渡段的 f(Re) 估计
        （Swanee-Jain 对 f 灵敏度低）；层流段完全不用它。

        壅塞支（2026-09-26，2026-09-27 Fanno 口径）：ṁ = min(Darcy,
        ṁ_cap)——管件真容量 = Fanno 摩擦管上限（f_D·L/D 定点自洽，
        fluids.fanno.choked_flow_fanno；理想喷嘴是其 L→0 退化）。
        Darcy 需要的流量超过管容量时钳在 cap，下游压力从流量公式
        退场（只留连续性传递）；min 结构保证分支点连续（kink，
        差分雅可比可处理，与 orifice β 夹断同族）。t0_up=None 退回
        旧口径（不钳，兼容旧调用）。
        """
        dp = p_up - p_down
        if dp <= 0.0:
            return 0.0
        rho = rho_up
        A, D, L = self.area, self.diameter, self.length

        m_lam = rho * dp * A * D * D / (32.0 * mu_up * L)  # Hagen-Poiseuille
        re_guess = self._re(mdot_guess, rho, mu_up)
        if re_guess <= RE_LAM:
            m_darcy = m_lam
        else:
            f = self._friction(re_guess)
            m_turb = A * np.sqrt(2.0 * rho * dp * D / (f * L))
            w = min(max((re_guess - RE_LAM) / (RE_TURB - RE_LAM), 0.0), 1.0)
            m_darcy = (1.0 - w) * m_lam + w * m_turb

        if t0_up is None:
            return m_darcy          # 旧口径（无总温不上限）
        # Fanno 摩擦管容量（f·L/D 定点自洽；理想喷嘴 = L→0 退化）
        m_cap = choked_flow_fanno(A, p_up, t0_up, L, D, self.roughness,
                                  ctx.gas.R, ctx.gas.gamma,
                                  ctx.gas.mu(t0_up))
        return min(m_darcy, m_cap)                  # 钳位：分支点连续

    def residual(self, x: np.ndarray, ctx) -> np.ndarray:
        p1 = self._total_p(x, ctx, 0)
        p2 = self._total_p(x, ctx, 1)
        m1 = x[self._m_idx[0]]
        m2 = x[self._m_idx[1]]

        # 上游 = 高压侧；流体从高压侧口流入组件（ṁ_high > 0）。
        # ρs/μ(Ts) 取上游口恢复静参数（白板查表；μ 用同口静温的 Sutherland）；
        # t0_up 进壅塞支（ṁ_cap 只看上游总参数）
        if p1 >= p2:
            k = 0
            st_up = self._static_state(x, ctx, 0)
            t0_up = self._total_t(x, ctx, 0)
            m_ideal = self.mass_flow(p1, p2, st_up.rho,
                                     ctx.gas.mu(st_up.T), m1, ctx, t0_up)
        else:
            k = 1
            st_up = self._static_state(x, ctx, 1)
            t0_up = self._total_t(x, ctx, 1)
            m_ideal = self.mass_flow(p2, p1, st_up.rho,
                                     ctx.gas.mu(st_up.T), m2, ctx, t0_up)

        return np.array([
            m1 + m2,
            x[self._m_idx[k]] - m_ideal,
        ])

    # ---------- 出口截面状态重构（报表/后处理用，不进残差） ----------
    def exit_state(self, x, ctx, j: int):
        """口 j 出口截面的静参数（StaticState；j 应为出料口 ṁ_j<0）。

        物理依据（2026-09-27，用户裁决"拥塞口静参数不得从下游节点
        总压反推"）：管的出口截面是元件内部物理的终点，其状态由
        (ṁ, A, T0_up) + 管几何决定——与下游节点压力无关（亚声速口
        依赖下游压力匹配出口静压，但总参数与 T0 仍是上游的）。

        壅塞口（ṁ ≥ cap）：出口即临界截面，sonic_state_from_flow
        声速闭合（T*=T0·2/(γ+1)，ρ*=ṁ/(a*A)，p*=ρ*RT*——ṁ 自身生成
        静压，无压力反演）。
        亚声速口：进口 Ma 由等熵 q(ṁ|p0_up) 反解（与 Darcy 支 ρs 同
        口径），出口 Ma 由 Fanno 管长关系 F(M_out) = F(M_in) − f·L/D
        （亚声速沿程加速），T0 守恒 + 连续性直算静参数，状态方程
        收尾——压力是输出不是输入。ṁ→cap 时两支在 Ma=1 精确铰链。

        白板查表（_static_state）对出料口给的是"节点滞止态反推"的
        假想等熵态（腔体假设），本方法是其物理修正——port_states
        对管件出料口改调此处（与 areachange 同构的元件自报模式）。
        """
        gas = ctx.gas
        mdot = abs(x[self._m_idx[j]])           # 出料口流量（=管内流量）
        if mdot == 0.0:
            return None                         # 零流量无出口态
        A, D, L = self.area, self.diameter, self.length
        j_up = 1 - j
        t0_up = self._total_t(x, ctx, j_up)     # 上游节点总温（Fanno T0 守恒）
        st_son = sonic_state_from_flow(mdot, A, t0_up, gas.R, gas.gamma)
        # 出口马赫：壅塞口 = 1（临界截面）；亚声速口沿 Fanno 线积分
        cap = choked_flow_fanno(A, self._total_p(x, ctx, j_up), t0_up,
                                L, D, self.roughness, gas.R, gas.gamma,
                                gas.mu(t0_up))
        if mdot >= cap:
            return st_son                       # 壅塞：出口 = 临界截面
        # 亚声速：F(M_out) = F(M_in) − f·L/D（亚声速沿程加速）；
        # M_in 由等熵 q(ṁ|p0_up) 反解（与 Darcy 支 ρs 同口径），f 取
        # 实际 ṁ 的 Re（Swanee-Jain）。连续性 + T0 守恒直接构造静参数
        # ——不经总压链，压力是输出不是输入。
        q_m = gas.q_of_flow(mdot, self._total_p(x, ctx, j_up), t0_up, A)
        ma_in, _ = gas.mach_from_q(q_m)
        re = mdot * D / (gas.mu(t0_up) * A)
        chi_out = fanno_param(ma_in, gas.gamma) - self._friction(re) * L / D
        if chi_out <= 1.0e-8:                  # 剩余壅塞长度≈0（ṁ→cap 数值
            return st_son                      # 贴界）：按临界报（χ→Ma 是
                                               # 平方根映射，Ma 阈值会漏判）
        ma_out = mach_from_fanno(chi_out, gas.gamma)
        tau_out = 1.0 + 0.5 * (gas.gamma - 1.0) * ma_out * ma_out
        T_out = t0_up / tau_out                 # 能量守恒（T0 沿程不变）
        v_out = ma_out * np.sqrt(gas.R * gas.gamma * T_out)
        rho_out = mdot / (A * v_out)            # 连续性定密度
        p_out = rho_out * gas.R * T_out         # 状态方程收尾
        return StaticState(ma_out, p_out, T_out, rho_out, v_out, False)
