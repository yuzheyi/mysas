"""cosim — SAS×结构耦合包（yzysas 顶层，依赖单向向下）。

三包关系:
    pysas    一维空气系统网络求解器（独立，不 import 其他两包）
    pyfem    轴对称 FE 热/结构求解器（独立，不 import 其他两包）
    cosim    耦合层: import pysas + pyfem，管它俩怎么对话   ← 本包

耦合变量总览（Phase 2 实现热耦合，其余按计划扩展）:
    热:     网络→FE  每边界段 (h 分布, T_gas)  即 Robin/*FILM 边界
            FE→网络  每边界段壁温分布 T_w(r) 与总热流 Q_j
            （WALL_FILM 元件 q = Σ h_f·(T_gas − T_w(r_f))·dA_f，非平均壁温近似）
    间隙:   FE→网络  密封半径处径向位移 u_r → 篦齿间隙修正
            （Phase 4+：correlations/seal.py + netelem 耦合封严元件）

分层:
    correlations/  交接量关联式（纯函数注册表，零求解器依赖）
                   register_h（热）/ register_gap（间隙，预留）
    netelem/       pysas 侧耦合元件（外部注册进 pysas 工厂，pysas 零改动*）
    adapters/      FE 抽象契约 fe_base + 轴对称实现 fe_axisym
                   （3D 将来加 fe_3d.py，coupling/ 零改动）
    coupling/      耦合驱动器 partitioned / monolithic（只认 fe_base 契约）

* pysas 主干仅两处最小改动（见 开发计划）:
    datamodel/topology.py   ElemType 加 WALL_FILM = 11
    solver/newton.py        damped_newton 线性求解按雅可比稀疏/稠密自动分发
"""
