"""materials — 随温度插值材料表（零依赖，独立复用）。"""
from pyfem.materials.materials import Material, TC11, get_material

__all__ = ["Material", "TC11", "get_material"]
