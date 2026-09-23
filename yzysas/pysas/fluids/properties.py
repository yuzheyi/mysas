"""properties — 理想气体物性（常比热族；Sutherland μ(T)/变比热将来到此）。

定位：流体材料的物性函数之家——与 isentropic.py（气动函数）平级，
不认识网络/元件/解向量（叶子模块）。当前只有等比热族：

  cp_ideal_gas(R, gamma)   定压比热 J/(kg·K) = γR/(γ−1)
  cv_ideal_gas(R, gamma)   定容比热 J/(kg·K) = R/(γ−1)（M4 容腔能量用）

调用方：assembly 能量方程 q/cp 项、scaling 能量行参考量
（两处必须同源——同一函数保证数值一致）。
将来：Sutherland 粘度 μ(T)、变比热 cp(T) 表、真实气体修正都在此扩展；
届时签名从 (R, gamma) 演化为吃流体对象（依赖方向：算法层→数据层）。
"""
from __future__ import annotations


def cp_ideal_gas(R: float, gamma: float) -> float:
    """理想气体定压比热 J/(kg·K)（等比热；γ→1 时发散，调用方保证 γ>1）。"""
    return R * gamma / (gamma - 1.0)


def cv_ideal_gas(R: float, gamma: float) -> float:
    """理想气体定容比热 J/(kg·K)（cp − R；M4 容腔绝热充排气用）。"""
    return R / (gamma - 1.0)
