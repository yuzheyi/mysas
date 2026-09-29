"""heat — h 对流换热系数关联式（Phase 2 参考实现两条）。

物理来源（文献锚点，系数按经典公开形式，工程使用前请自行核对适用区间）:

  disk_cavity（旋转盘腔，Owen/Rogers 转系盘对流）:
      盘面局部 Nu_r = 0.021·Re_φ,r^0.8 · C_w* 校正（工程简化型）
      h(r) = Nu·λ_air/r_local
      Re_φ,r = ω·r²/ν_gas（局部旋转雷诺数）
      C_w* 校正暂取 1（无净流盘腔）；有贯流时乘 (1 + C_w/(2π·Re_φ))^0.4
      适用: 转静系盘腔、无浮力主导、湍流 Re_φ > 2e5

  pipe_duct（管道/引气，Dittus-Boelter）:
      Nu = 0.023·Re^0.8·Pr^0.4（加热流体；冷却用 Pr^0.3）
      h = Nu·λ/D，Re = 4·ṁ/(π·D·μ)（管内质量流口径）
      适用: 充分发展湍流 Re > 1e4、L/D > 60；短管需乘入口修正（未含）

物性: 空气按理想气体 λ(T)/μ(T)（幂律插值，300-800 K 误差 <3%——
      参考实现取简即可；精度敏感场景应替换为真实物性表）。
"""
from __future__ import annotations

import numpy as np

from cosim.correlations import register_h

# ---------- 空气物性简化（参考实现用；可替换） ----------
def _air_lambda(T):
    """导热系数 W/(m·K)，幂律拟合 300-800K（Sutherland 简化）。"""
    return 0.0263 * (T / 300.0) ** 0.85


def _air_mu(T):
    """动力粘度 Pa·s，Sutherland 律简化幂律。"""
    return 1.85e-5 * (T / 300.0) ** 0.7


def _air_pr(T):
    """Prandtl 数（空气 ~0.7，弱温度依赖取常数）。"""
    return 0.70


# ---------- 盘腔 ----------
@register_h("disk_cavity")
def disk_cavity(state: dict, geom: dict):
    """旋转盘腔 h(r) 分布。

    state: {"omega": rad/s, "T_gas": K, "mdot": kg/s(贯流，可 0), "p_gas": Pa}
    geom:  {"facet_rs": (nf,) 各 facet 中点半径 m（必需）,
            "b": 盘外缘半径 m（C_w* 用，缺省取 facet_rs.max()）}
    返回:  (nf,) h 分布 W/(m²K)
    """
    omega = state.get("omega", 0.0)
    T = state["T_gas"]
    rs = np.asarray(geom["facet_rs"], dtype=float)
    b = geom.get("b", float(rs.max()))

    lam, mu = _air_lambda(T), _air_mu(T)
    rho = state.get("rho_gas")
    if rho is None:
        p = state.get("p_gas", 1.0e5)
        rho = p / (287.05 * T)          # 理想气体

    # 局部旋转雷诺数 Re_phi = ω r² / ν
    nu = mu / rho
    Re_phi = omega * rs * rs / nu       # (nf,)
    Re_phi = np.maximum(Re_phi, 1.0e3)  # 低转静止兜底（层流区关联式外）

    # Nu = 0.021 Re_phi^0.8 · 贯流校正
    Nu = 0.021 * Re_phi ** 0.8
    mdot = state.get("mdot", 0.0)
    if mdot > 0.0:
        C_w = mdot / (rho * nu * b)     # 无量纲流量
        Nu *= (1.0 + C_w / (2.0 * np.pi * np.maximum(Re_phi, 1.0))) ** 0.4

    # h = Nu·λ/r（局部半径为特征长度）
    return Nu * lam / np.maximum(rs, 1e-6)


# ---------- 管道 ----------
@register_h("pipe_duct")
def pipe_duct(state: dict, geom: dict):
    """管道/引气 h（Dittus-Boelter，均匀值）。

    state: {"mdot": kg/s, "T_gas": K, "p_gas": Pa}
    geom:  {"D": 水力直径 m（必需）}
    返回:  标量 h W/(m²K)
    """
    mdot = abs(state["mdot"])
    T = state["T_gas"]
    D = float(geom["D"])

    lam, mu, Pr = _air_lambda(T), _air_mu(T), _air_pr(T)
    rho = state.get("rho_gas")
    if rho is None:
        p = state.get("p_gas", 1.0e5)
        rho = p / (287.05 * T)

    Re = 4.0 * mdot / (np.pi * D * mu)
    if Re < 2300.0:                     # 层流兜底: Nu=3.66（等壁温圆管）
        Nu = 3.66
    else:
        Nu = 0.023 * Re ** 0.8 * Pr ** 0.4
    return float(Nu * lam / D)
