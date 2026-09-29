"""fe — 轴对称有限元（scikit-fem）：稳态热传导 + 热弹性。

模块:
  materials            随温度插值材料表（TC11 起步）
  axisym_heat          轴对称稳态热传导（Robin/*FILM 边界）
  axisym_thermoelastic 轴对称热弹性（初应变 αΔT + 离心 ρω²r）

坐标约定（全包一致）: 剖面位于 (r, z) 平面，r≥0，旋转轴 r=0；
                       2π 周长积分并入弱形式权函数（w.x[0] 即 r）。
"""
