"""pyfem — SAS×结构热耦合求解器（scikit-fem 路线）。

分层（对应 开发计划-pyfem.md）:
  fe/       轴对称稳态热传导 + 热弹性（独立可用，不依赖网络侧）
  netelem/  网络侧耦合元件（WALL_FILM，外部注册进 pysas 工厂）  [Phase 2]
  coupling/ partitioned / monolithic 两种耦合                    [Phase 3]

与 pysas 的关系: 平级独立包（yzysas 下 sys.path 直跑，不打包），
仅 netelem/coupling 需要 import pysas；fe/ 零依赖 pysas。
"""
