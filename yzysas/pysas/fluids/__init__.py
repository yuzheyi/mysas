"""fluids — 流体模型包（GasModel 家族；无状态，不认识网络/元件/解向量）。

包内分工（与 elements 同构，2026-09-26 定稿）：
  base.py        GasModel 薄基类（消费方契约 + register_gas）
  isentropic.py  理想气体等熵物理文档 + 算法体（StaticState/q_of_mach/...）
  ideal_gas.py   IdealGas：常比热子类（@register_gas）+ _selftest
  本文件         make_gas 工厂（JSON gas.type → 类名分发）

加一种新气体 = 加一个文件（GasModel 子类 + @register_gas），
JSON 的 gas.type 改类名即接入，io/assembly/solver/元件零改动。
（properties.py/gas.py 已删：cp/cv 内联在 IdealGas，工厂归包入口。）
"""
from pysas.fluids.base import (GasModel, available_gases, register_gas)
from pysas.fluids.ideal_gas import IdealGas  # noqa: F401  (注册副作用)
from pysas.fluids.isentropic import StaticState
from pysas.fluids.water_vapor import WaterVapor  # noqa: F401  (注册副作用)


def make_gas(cfg: dict | None = None) -> GasModel:
    """JSON gas 块（或缺省）→ 流体实例（类名分发）。

    cfg["type"] 缺省 "IdealGas"；无参调用 = 缺省空气（手算/单测用）。自定义气体做成 GasModel 子类 +
    @register_gas 后，JSON 侧只改 type 字符串。**参数缺省归各类构造器**
    （工厂只透传显式给定的键——水蒸气类缺省 R=461.5，空气类 287.05，
    工厂不越权替它们定；mu 显式给=常数口径，缺省=各类自己的族）。
    """
    from pysas.fluids.base import _GAS_REGISTRY
    cfg = cfg or {}
    name = cfg.get("type", "IdealGas")
    if name not in _GAS_REGISTRY:
        raise ValueError(f"未知气体类型 {name!r}，可用: {sorted(_GAS_REGISTRY)}")
    return _GAS_REGISTRY[name](**{k: v for k, v in cfg.items() if k != "type"})


__all__ = ["GasModel", "register_gas", "available_gases",
           "IdealGas", "WaterVapor", "make_gas", "StaticState"]
