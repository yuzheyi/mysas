"""correlations — 交接量关联式注册表（纯函数，零求解器依赖）。

设计（"加物理 = 加文件 + 注册"，与 pysas 元件/fluids 同构）:
    关联式是纯函数 (流动状态, 几何) → 交接量分布
    通过 register_h / register_gap 装饰器注册，按名字查表

模块:
    heat.py    h 对流换热系数关联式 ★Phase 2
               - disk_cavity:  盘腔（Owen 旋转库塔赫型 Nu = f(Re_phi, C_w)）
               - pipe_duct:    管道/引气（Dittus-Boelter Nu = 0.023 Re^0.8 Pr^0.4）
    seal.py    （Phase 4+ 落点：篦齿间隙→流阻；当前不建文件）

契约（h 关联式）:
    fn(state: dict, geom: dict) -> np.ndarray | float
      state: {"mdot": kg/s, "T_gas": K, "p_gas": Pa, "omega": rad/s, ...}（网络解）
      geom:  {"r_in": m, "r_out": m, "facet_rs": (nf,) 各 facet 半径, ...}（FE 边界）
      返回:  标量（均匀 h）或 facet 级数组（h(r) 分布，FE 表单自动插值）

    可选属性 fn.dh_dstate: dict[str, callable] | None
      monolithic 雅可比用；缺省 None → 耦合器对该关联式差分。
"""
from __future__ import annotations

from typing import Callable

import numpy as np

# ---------- 注册表 ----------
H_REGISTRY: dict[str, Callable] = {}
GAP_REGISTRY: dict[str, Callable] = {}   # Phase 4+ 篦齿间隙关联式


def register_h(name: str):
    """装饰器：注册 h 关联式（name 唯一，重复注册报错防静默覆盖）。"""
    def deco(fn):
        if name in H_REGISTRY:
            raise ValueError(f"h 关联式 {name!r} 已注册")
        H_REGISTRY[name] = fn
        return fn
    return deco


def register_gap(name: str):
    """装饰器：注册间隙→流阻关联式（Phase 4+ 启用）。"""
    def deco(fn):
        if name in GAP_REGISTRY:
            raise ValueError(f"gap 关联式 {name!r} 已注册")
        GAP_REGISTRY[name] = fn
        return fn
    return deco


def get_h(name: str) -> Callable:
    fn = H_REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"未知 h 关联式 {name!r}，可用: {sorted(H_REGISTRY)}")
    return fn


def make_h(name_or_fn):
    """字符串名 → 注册表函数；已是 callable → 原样返回。"""
    return get_h(name_or_fn) if isinstance(name_or_fn, str) else name_or_fn


# 惰性导入自带关联式（防 import 环：heat.py 只依赖本模块的注册表）
def _load_builtin():
    if H_REGISTRY:
        return
    from cosim.correlations import heat as _heat  # noqa: F401


_load_builtin()
