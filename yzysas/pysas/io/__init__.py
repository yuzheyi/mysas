"""io — 拓扑读入与结果输出。

netinf.json → Network（拓扑） + SolveContext（边界与物性）
+ 元件工厂：type 名 → ElementModel 子类
+ 结果输出：解向量 → 统一格式文本报告（result.py，三算法互证用）
"""
from pysas.io.netinf import load_netinf, netinf_from_dict, build_models
from pysas.io.result import write_result

__all__ = ["load_netinf", "netinf_from_dict", "build_models", "write_result"]
