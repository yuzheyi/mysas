"""base — 流体基类与注册表（fluids 的 ElementModel，2026-09-26）。

与 elements/base.py 同构的心智模型：一边"加元件 = 加文件 + register_model"，
一边"加气体 = 加文件 + register_gas"。JSON 的 gas.type 字段按类名分发。

基类设计纪律（2026-09-26 定稿）：
  1. **薄**——只放消费方契约：system 白 prime / scaling / 元件残差
     实际调用的方法的 NotImplementedError 骨架。写新气体的人必须
     显式实现每个方法（含 mu），不能静默继承某种默认。
  2. **无任何物理默认值**——基类不内置“匿名空气”：粘度三参数/
     R/γ 都是气体身份属性，住各子类构造器，每个类像写 cp 一样
     自带 mu 实现（定律形式相同也是复制四行，不为省四行建抽象层
     ——SutherlandGas 混入曾建后删，教训：两种气体一种定律，
     混入是投机设计）。
  3. **不下沉理想气体内部实现**——q_of_mach/mach_from_q（等熵流量
     函数族）留在 IdealGas：真实气体没有等熵 q(Ma) 闭式。

消费方契约速查（谁在调这些方法）:
  R / gamma   orifice 可压缩喷嘴公式（β_crit 壅塞判断）——契约属性
              直读，子类构造器必设（类体注解声明，漏设当场
              AttributeError，无静默缺省）
  cp          system._cp / base.port_T_out / scaling 能量行参考量
  mu          pipe Re 与 Hagen-Poiseuille（取上游口静温）
  q_of_flow / mach_from_q_arr / statics_from_mach   system._prime_static
  total_to_static   base._static_state 白板回退 + 手算
  choked_flow       scaling m_ref 自估
  rho_from_pT       areachange 等元件的状态方程密度
"""
from __future__ import annotations

import numpy as np


# ---------- 注册表（对标 elements 的 register_model） ----------
_GAS_REGISTRY: dict[str, type] = {}


def register_gas(cls):
    """类装饰器：按类名注册气体模型（make_gas 分发用）。

    同类重复注册幂等（python -m 下模块本体与 __main__ 是两个身份，
    各执行一次装饰器；异类同名冲突仍报错）。
    """
    name = cls.__name__
    if name in _GAS_REGISTRY and _GAS_REGISTRY[name] is not cls:
        raise ValueError(f"气体类型 {name!r} 重复注册")
    _GAS_REGISTRY[name] = cls
    return cls


def available_gases() -> list[str]:
    """已注册气体类型名（报错信息/自检用）。"""
    return sorted(_GAS_REGISTRY)


class GasModel:
    """流体模型基类：一个流体 = 一个实例（网络全流一种流体）。

    子类必须在构造器设定 R/gamma 并实现全部 NotImplementedError 方法
    （消费方契约见模块 docstring）。基类不含任何物理默认值——
    粘度/比热/状态方程/气体常数都是气体身份属性，由子类像写 cp
    一样自带实现（定律形式相同也是复制四行，不为省四行建抽象层）。
    """

    # ---------- 契约：物性参数（属性直读；构造器必设，无默认值） ----------
    R: float      # 气体常数 J/(kg·K)（orifice 喷嘴公式等直读）
    gamma: float  # 比热比（理想气体家族口径；真实气体子类另行声明）

    # ---------- 契约：粘度（无共享实现——参数是气体身份属性） ----------
    def mu(self, T: float | None = None) -> float:
        """动力粘度 Pa·s（pipe Re/Hagen 用，取上游口静温；
        T=None 供缩放等无需温度的调用方——返回量级参考值）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现 mu(T)"
                                  f"（各气体类自带实现，同 cp 模式）")

    # ---------- 契约：物性 ----------
    def cp(self, T: float | None = None) -> float:
        """定压比热 J/(kg·K)（能量方程/发热温升用；变比热重写）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现 cp(T)")

    def cv(self, T: float | None = None) -> float:
        """定容比热 J/(kg·K)（M4 容腔能量用）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现 cv(T)")

    def rho_from_pT(self, p: float, T: float) -> float:
        """状态方程密度 ρ(p,T)（元件滞止/静密度用，看传入口径）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现 rho_from_pT(p,T)")

    # ---------- 契约：气动关系 ----------
    def q_of_flow(self, mdot, p0, T0, area):
        """|ṁ| → 无量纲流量函数 q（mach_from_q_arr 的输入；数组通用）。
        q 的定义依赖气体状态方程——每种子类有自己的口径。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现 q_of_flow")

    def mach_from_q_arr(self, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """向量化反解 q → (Ma, choked)（system 白板 prime 用）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现 mach_from_q_arr")

    def statics_from_mach(self, ma, p0, T0):
        """Ma + 总参数 → (T, p, rho, v)（白板 prime 消费，数组通用）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现 statics_from_mach")

    def total_to_static(self, p0: float, T0: float, mdot: float,
                        area: float):
        """端口四件套 → 静参数（白板回退/后处理/手算用）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现 total_to_static")

    def choked_flow(self, area: float, p0: float, T0: float) -> float:
        """壅塞流量上限（Cd=1，scaling m_ref 自估；非正参数返回 0）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现 choked_flow")
