"""meshtools — gmsh 网格导入与边界检查。

工作流（与 31project .geo → gmsh 完全一致，几何资产可直接迁移）:
    gmsh（GUI / .geo 文本 / Python API）建剖面
    + Physical Curve 命名边界（= 未来的腔/封严名，耦合时按名对账）
    → mesh generate → .msh
    → load_gmsh() 读入 skfem，boundaries 直接按名字可用
    → h / T_gas / 约束等物理值在 Python / JSON 里按名字赋值（不进网格）

支持与限制:
  - msh 4.x（meshio 5.x 转换 cell_sets → boundaries / subdomains）
  - 剖面须在 (r, z) 平面且 r ≥ 0
  - r = 0 轴上节点: 热传导无碍（权 r→0）；热弹性 εθ = u_r/r 在轴附近
    刚度略偏硬（积分点不落在节点上，数值安全，P1 光滑解 u_r ~ c·r
    时 u_r/r 有界）——实心盘建议留极小中心孔，或接受轻微刚度误差
  - 无 Physical Group 的 msh 也能读，但 boundaries 为空 → 边界条件无从
    设置，需回 gmsh 补命名（GUI: Geometry → Physical Groups → Add →
    Curve），或用交互标注工具（见 pyfem/meshtag.py，若已提供）
"""
from __future__ import annotations

import warnings

import numpy as np
from skfem import MeshTri


def load_gmsh(path) -> MeshTri:
    """读 .msh → MeshTri，做轴对称基本校验。

    校验项:
      r ≥ 0       剖面在 (r,z) 平面且不越对称轴
      有命名边界  无 Physical Curve 时告警（边界条件将无从挂）
    """
    m = MeshTri.load(path)
    r = m.p[0]
    if r.min() < -1e-12:
        raise ValueError(
            f"剖面出现 r < 0（min = {r.min():.3e}）——"
            f"请检查几何是否在 (r,z) 平面、r ≥ 0")
    if not m.boundaries:
        warnings.warn(
            "网格无命名边界（Physical Curve）——热/力边界将无从设置；"
            "请在 gmsh 中添加 Physical Groups 后重新导出")
    return m


def report(m: MeshTri) -> str:
    """网格 + 命名边界的人读摘要（每段边界给出位置范围，便于核对命名）。"""
    lines = [
        f"MeshTri1: {m.nvertices} 节点 / {m.nelements} 单元",
        f"r ∈ [{m.p[0].min():.4g}, {m.p[0].max():.4g}]  "
        f"z ∈ [{m.p[1].min():.4g}, {m.p[1].max():.4g}]",
    ]
    for name, facets in (m.boundaries or {}).items():
        # facet 中点的坐标范围——一眼看出这段边界在哪（防命名张冠李戴）
        mid = m.p[:, m.facets[:, facets]].mean(axis=1)
        lines.append(
            f"  boundary '{name}': {len(facets):3d} facets  "
            f"r ∈ [{mid[0].min():.4g}, {mid[0].max():.4g}]  "
            f"z ∈ [{mid[1].min():.4g}, {mid[1].max():.4g}]")
    return "\n".join(lines)
