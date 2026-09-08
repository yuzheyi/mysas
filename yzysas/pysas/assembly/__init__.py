"""assembly — 全局方程组组装层。

把 Network（拓扑）+ 各元件 ElementModel 组装成统一非线性方程组
  0 = F(x)，x = [p0 内部节点 | ṁ 各端口]
三层结构（solver 只认 F/J，不认识网络）:
  topology 定骨架 → assembly 拼 F → solver 解 F=0
"""
from pysas.assembly.system import NetworkSystem

__all__ = ["NetworkSystem"]
