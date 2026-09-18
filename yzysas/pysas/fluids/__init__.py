"""fluids — 流体物性/气动函数包（无状态纯函数，不认识网络/元件/解向量）。

多流体定位（2026-09-18 晋升为包）:
  SAS 系统可能输送空气（可压、等熵关系主导），也可能输送液体工质
  （不可压、粘性主导）——两类流体的状态恢复/物性模型差异大，
  按文件分家、按需 import，互不拖累。

  isentropic.py   理想气体等熵关系：q(Ma)/total_to_static
                  （空气侧核心：总参数 → 静参数）
  viscosity.py    （预留）Sutherland μ(T)，篦齿 Re 修正用
  incompressible.py（预留）液体：密度常物性 + Bernoulli/损失模型

依赖方向: 本包不 import pysas 其他模块（叶子包）；
调用方: 元件残差内部 / 后处理 / 互相独立的物性家族。
"""
from pysas.fluids.isentropic import (
    StaticState,
    mach_from_q,
    q_of_mach,
    total_to_static,
)

__all__ = ["StaticState", "q_of_mach", "mach_from_q", "total_to_static"]
