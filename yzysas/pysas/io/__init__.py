"""io — 拓扑信息读入。

netinf.json → Network（拓扑） + SolveContext（边界与物性）
+ 元件工厂：type 名 → ElementModel 子类
"""
from pysas.io.netinf import load_netinf, build_models

__all__ = ["load_netinf", "build_models"]
