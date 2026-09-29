"""axisym_heat — 轴对称稳态热传导（scikit-fem）。

物理/离散:
  剖面 Ω 位于 (r,z)，r≥0。轴对称稳态无内热源:
      ∇·(k(T) ∇T) = 0                    （体积方程）
      -k ∂T/∂n = h_j (T - T_gas,j)        （Robin/*FILM 边界段 j）
      T = T_spec                          （Dirichlet，可选）
  弱形式（2π 周长并入权 r）:
      ∫_Ω k(T) (∇T·∇v) r drdz
      + Σ_j ∫_{Γ_j} h_j (T - T_gas,j) v r ds = 0

  k(T) 非线性 → 逐次代入（Picard: A(T_old)·T_new = b(T_old)，网格
  不变只重组装）。TC11 的 k(T) 6.3→12.1 很温和，Picard 3-5 步收敛；
  不上真牛顿（切线 dk/dT 组装复杂、此处收益小）。

  Robin 边界自带正定贡献，刚度矩阵 + Robin 块恒正定 → 可靠解。

耦合交换量（Phase 3 用，Phase 1 先实现好接口）:
  wall_temps  边界段 j 的 r 加权平均壁温 T_w,j = ∫T r ds / ∫r ds
              （热量加权平均温度，工程上盘腔-气流换热驱动力的定义）
  film_heats  边界段 j 壁面热流 Q_j = 2π∫ h_j (T_gas,j − T) r ds
              （>0 = 气流加热壁面；与网络侧 Σq 对账的能量口径）
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from skfem import (Basis, BilinearForm, ElementTriP1, FacetBasis, Functional,
                   LinearForm, condense, solve)
from skfem.helpers import dot, grad


# ---------- 表单 ----------
@BilinearForm
def _conduction(u, v, w):
    """∫ k(T) (∇u·∇v) r drdz —— k 从 w['kT'] 逐积分点取。"""
    return w['kT'] * dot(grad(u), grad(v)) * w.x[0]


@BilinearForm
def _robin_mass(u, v, w):
    """∫ h (u·v) r ds —— Robin 的质量部分（左端）。"""
    return w['h'] * u * v * w.x[0]


@LinearForm
def _robin_load(v, w):
    """∫ h T_gas v r ds —— Robin 的载荷部分（右端）。"""
    return w['h'] * w['Tgas'] * v * w.x[0]


@Functional
def _f_Tw_num(w):
    """∫ T r ds（平均壁温分子）。T 从 w['T'] 取。"""
    return w['T'] * w.x[0]


@Functional
def _f_Tw_den(w):
    """∫ r ds（平均壁温分母）。"""
    return w.x[0]


@Functional
def _f_heat(w):
    """∫ h (T_gas − T) r ds（热流被积，2π 在调用处补）。"""
    return w['h'] * (w['Tgas'] - w['T']) * w.x[0]


@dataclass
class FilmBC:
    """一段对流换热边界（CalcuLiX *FILM 的等价物）。

    facets: skfem 边界 facet 索引（mesh.boundaries[name] 或手选）
    h:      对流换热系数 W/(m²K)（耦合时来自网络解/关联式）
    T_gas:  气流恢复温度 K（耦合时来自网络解）
    """
    facets: np.ndarray
    h: float
    T_gas: float
    name: str = ""


@dataclass
class HeatResult:
    converged: bool
    iters: int
    T: np.ndarray                                    # 节点温度
    wall_temps: dict = field(default_factory=dict)   # name -> T_w (K)
    film_heats: dict = field(default_factory=dict)   # name -> Q (W，>0 气流加热壁)


class AxisymHeat:
    """轴对称稳态热传导求解器（k(T) Picard 逐次代入）。

    用法:
        heat = AxisymHeat(mesh, material=TC11)
        heat.add_film("cavity", facets, h=516.0, T_gas=418.7)
        heat.set_dirichlet(dofs, 658.15)          # 可选
        res = heat.solve()
        res.T / res.wall_temps["cavity"] / res.film_heats["cavity"]
    """

    def __init__(self, mesh, material, quadrature: int = 2):
        self.mesh = mesh
        self.mat = material
        self._quadrature = quadrature
        self.basis = Basis(mesh, ElementTriP1(), intorder=quadrature)
        self.films: list[FilmBC] = []
        self._dirichlet_dofs: np.ndarray | None = None
        self._dirichlet_vals: np.ndarray | None = None
        self._T: np.ndarray | None = None

    # ---------- 边界条件 ----------
    def add_film(self, name: str, facets, h: float, T_gas: float):
        self.films.append(FilmBC(facets=np.asarray(facets, dtype=np.int32),
                                 h=float(h), T_gas=float(T_gas), name=name))

    def set_dirichlet(self, dofs, values):
        self._dirichlet_dofs = np.asarray(dofs, dtype=np.int64)
        self._dirichlet_vals = np.asarray(values, dtype=float)

    # ---------- 组装 ----------
    def _facetbasis(self, facets) -> FacetBasis:
        return FacetBasis(self.mesh, self.basis.elem, facets=facets,
                          intorder=self._quadrature)

    def _assemble(self, T: np.ndarray):
        """给定节点温度 → (A, b)（k(T) 在积分点取值）。"""
        kT = self.mat.k(self.basis.interpolate(T))   # 积分点导热率
        A = _conduction.assemble(self.basis, kT=kT)
        b = np.zeros(self.basis.N)
        for f in self.films:
            fb = self._facetbasis(f.facets)
            A = A + _robin_mass.assemble(fb, h=f.h)
            b = b + _robin_load.assemble(fb, h=f.h, Tgas=f.T_gas)
        return A, b

    # ---------- 求解 ----------
    def solve(self, T0=None, tol: float = 1e-8, max_iter: int = 30) -> HeatResult:
        """稳态解（Picard 至收敛）。T0: 初值（缺省 300 K 均匀）。"""
        T = (np.full(self.basis.N, 300.0) if T0 is None
             else np.asarray(T0, dtype=float).copy())
        err, it = np.inf, 0
        for it in range(1, max_iter + 1):
            A, b = self._assemble(T)
            if self._dirichlet_dofs is not None:
                # x=T_iter 预置 Dirichlet 自由度的给定值再缩聚
                T_iter = T.copy()
                T_iter[self._dirichlet_dofs] = self._dirichlet_vals
                T_new = solve(*condense(A, b, x=T_iter, D=self._dirichlet_dofs))
            else:
                T_new = solve(A, b)
            err = np.max(np.abs(T_new - T)) / max(1.0, np.max(np.abs(T_new)))
            T = T_new
            if err < tol:
                break
        self._T = T
        return HeatResult(converged=bool(err < tol), iters=it, T=T,
                          wall_temps=self.wall_temps(),
                          film_heats=self.film_heats())

    # ---------- 耦合交换量 ----------
    def wall_temps(self) -> dict:
        """各 film 边界 r 加权平均壁温 T_w = ∫T r ds / ∫r ds。"""
        self._require_solved()
        out = {}
        for f in self.films:
            fb = self._facetbasis(f.facets)
            num = _f_Tw_num.assemble(fb, T=fb.interpolate(self._T))
            den = _f_Tw_den.assemble(fb)
            out[f.name] = float(num / den)
        return out

    def film_heats(self) -> dict:
        """各 film 边界热流 Q = 2π∫ h (T_gas − T) r ds（W，>0 气流加热壁）。"""
        self._require_solved()
        out = {}
        for f in self.films:
            fb = self._facetbasis(f.facets)
            Q = 2.0 * np.pi * _f_heat.assemble(
                fb, T=fb.interpolate(self._T), h=f.h, Tgas=f.T_gas)
            out[f.name] = float(Q)
        return out

    def _require_solved(self):
        if self._T is None:
            raise RuntimeError("先调用 solve()")
