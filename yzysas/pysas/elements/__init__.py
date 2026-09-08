"""elements — 元件方程包。

每种 ElemType 一个 ElementModel 实现，统一以隐式残差形式 0 = f(m…, p…)
参与全局方程组（与 LGSAS 用户自定义元件同款路线）。
"""
from pysas.elements.base import ElementModel

__all__ = ["ElementModel"]
