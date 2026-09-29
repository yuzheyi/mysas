"""pyfem — SAS×结构热耦合求解器（scikit-fem 路线）。

分层（对标 pysas 的分层心智模型，详见 开发计划-pyfem.md）:
  materials/  随温度插值材料表（TC11 起步）——零依赖
  mesh/       网格导入与检查（gmsh .msh + named boundaries）
  solver/     轴对称物理求解器（稳态热传导 / 热弹性）
  io/         载荷/配置读写（Phase 3 耦合配置入口）
  netelem/    网络侧耦合元件（WALL_FILM，外部注册进 pysas 工厂）[Phase 2]
  coupling/   partitioned / monolithic 两种耦合                    [Phase 3]
  examples/   demo 与验证脚本（demo_fe / demo_dqy / test_fe…）
  tests/      回归测试（Phase 3 起 examples 里的 test_* 迁入）

与 pysas 的关系: 平级独立包（yzysas 下 sys.path 直跑，不打包），
仅 netelem/coupling 需要 import pysas；fe 层零 pysas 依赖。
坐标约定（全包一致）: 剖面位于 (r, z) 平面，r≥0，2π 周长并入弱形式。
"""
