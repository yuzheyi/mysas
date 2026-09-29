"""mesh — 网格导入与检查（gmsh → skfem，named boundaries）。"""
from pyfem.mesh.meshtools import load_gmsh, report

__all__ = ["load_gmsh", "report"]
