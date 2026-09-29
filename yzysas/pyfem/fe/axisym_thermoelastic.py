"""axisym_thermoelastic — 轴对称热弹性（scikit-fem）。

物理/离散:
  位移场 u = (u_r, u_z)，剖面 (r,z) r≥0。
  应变（轴对称 4 分量）:
      εr = ∂u_r/∂r,  εz = ∂u_z/∂z,
      εθ = u_r / r,  γrz = ∂u_r/∂z + ∂u_z/∂r
  本构（3D 各向同性 D 的轴对称特例，3×3 正块 + G）:
      D11 = E(1−ν)/((1+ν)(1−2ν)),  D12 = Eν/((1+ν)(1−2ν)),
      D44 = E/(2(1+ν))
      热初应变 ε_th = α(T)(T − T_ref)·[1,1,1,0]（T_ref = 20°C，
      CalculiX *Expansion Zero=20 口径）

  虚功方程（2π 周长并入权 r）:
      ∫ (D(ε−ε_th)) : ε(v) r drdz = ∫ ρ ω² r e_r·v r drdz + ∑∫ t·v r ds

  非线性: E(T)、ν(T) 随温度 → Picard 逐次代入（温度场固定，
  只重组装 D(T)），2-3 步收敛。

skfem 维度约定（表单内）: 物理场数组形状 (…, nelems, nqp)，
  用省略号广播；w.x[0] = r 积分点坐标。

输出:
  位移 (u_r, u_z)、应力 4 分量、von Mises（节点值，积分点算术
  平均 → 节点平均，一阶恢复够工程云图用；精确应力恢复后续按需）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from skfem import (Basis, BilinearForm, ElementTriP1, ElementVector,
                   FacetBasis, LinearForm, condense, solve)


# ---------- 轴对称应变算子 ----------
def _eps(u, w):
    """轴对称应变 4 分量 (4, …, nelems, nqp)。u: 位移向量场（skfem）。"""
    return np.stack([
        u.grad[0, 0, ...],                       # εr
        u.grad[1, 1, ...],                       # εz
        u[0, ...] / w.x[0, ...],                 # εθ = u_r/r
        u.grad[0, 1, ...] + u.grad[1, 0, ...],   # γrz
    ])


def _sig(eps, D):
    """σ = D ε，(4, …)。D: (4, 4, …)。"""
    return np.einsum('ij...,j...->i...', D, eps)


# ---------- 表单 ----------
@BilinearForm
def _stiffness(u, v, w):
    """∫ (D ε(u)) : ε(v) r drdz。"""
    su = _sig(_eps(u, w), w['D'])
    return np.einsum('i...,i...->...', su, _eps(v, w)) * w.x[0]


@LinearForm
def _thermal_load(v, w):
    """∫ (D ε_th) : ε(v) r drdz —— 热初应变等效节点力。"""
    s_th = _sig(w['eps_th'], w['D'])
    return np.einsum('i...,i...->...', s_th, _eps(v, w)) * w.x[0]


@LinearForm
def _centrifugal(v, w):
    """∫ ρ ω² r (e_r·v) r drdz —— 离心体力。"""
    return w['rho'] * w['omega2'] * w.x[0] * v[0, ...] * w.x[0]


@LinearForm
def _traction(v, w):
    """∫ t·v r ds —— 面力（trac (2, …) 分量逐积分点）。"""
    return ((w['trac'][0, ...] * v[0, ...]
             + w['trac'][1, ...] * v[1, ...]) * w.x[0])


@dataclass
class ThermoElasticResult:
    converged: bool
    iters: int
    u: np.ndarray                                # 位移 dof 向量（skfem 排布）
    sigma: dict = field(default_factory=dict)    # 节点应力分量
    von_mises: np.ndarray | None = None          # 节点 von Mises (Pa)


class AxisymThermoElastic:
    """轴对称热弹性求解器（D(T) Picard + 初应变法热载荷）。

    用法:
        te = AxisymThermoElastic(mesh, material=TC11, T_field=T_node)
        te.add_centrifugal(omega2=7.16e5)        # ω² (rad/s)²
        te.set_constraint(dofs, values)          # 至少约束刚体模态
        res = te.solve()
        res.u（basis.split 或 nodal_dofs 取 (u_r, u_z)）
    """

    def __init__(self, mesh, material, T_field: np.ndarray,
                 quadrature: int = 2):
        self.mesh = mesh
        self.mat = material
        # 标量 P1 基（温度插值到积分点用）与位移向量基共享网格
        self._Tbasis = Basis(mesh, ElementTriP1(), intorder=quadrature)
        self.basis = Basis(mesh, ElementVector(ElementTriP1()),
                           intorder=quadrature)
        self._quadrature = quadrature
        if len(T_field) != self._Tbasis.N:
            raise ValueError(f"T_field 长度 {len(T_field)} != 节点数 "
                             f"{self._Tbasis.N}")
        self.T = np.asarray(T_field, dtype=float)
        self._omega2 = 0.0
        self._rho_override = None
        self._tractions: list[tuple[np.ndarray, tuple[float, float]]] = []
        self._cdofs: np.ndarray | None = None
        self._cvals: np.ndarray | None = None
        self._u: np.ndarray | None = None

    # ---------- 载荷 ----------
    def add_centrifugal(self, omega2: float, rho=None):
        """离心体力 ρ ω² r e_r。omega2 = ω² (rad/s)²。rho 可覆盖材料密度。"""
        self._omega2 = float(omega2)
        self._rho_override = rho

    def add_traction(self, facets, trac_r: float, trac_z: float):
        """分段常面力 (t_r, t_z) Pa，正 = 沿正坐标方向（压力取负法向另行）。"""
        self._tractions.append((np.asarray(facets, dtype=np.int32),
                                (float(trac_r), float(trac_z))))

    def set_constraint(self, dofs, values):
        """位移约束（skfem 位移 dof 索引，全局展平编号）。"""
        self._cdofs = np.asarray(dofs, dtype=np.int64)
        self._cvals = np.asarray(values, dtype=float)

    # ---------- 逐积分点量 ----------
    def _D_at_qp(self):
        """积分点 D (4, 4, nelems, nqp)。E/ν 在积分点温度取值。"""
        Tq = self._Tbasis.interpolate(self.T)          # (nelems, nqp)
        E = self.mat.E(Tq)
        nu = self.mat.nu(Tq)
        D11 = E * (1 - nu) / ((1 + nu) * (1 - 2 * nu))
        D12 = E * nu / ((1 + nu) * (1 - 2 * nu))
        D44 = E / (2 * (1 + nu))
        D = np.zeros((4, 4, *E.shape))
        for i in range(3):
            for j in range(3):
                D[i, j] = D11 if i == j else D12
        D[3, 3] = D44
        return D

    def _eps_th_at_qp(self):
        """积分点热初应变 (4, nelems, nqp)：α(T)(T−T_ref)[1,1,1,0]。"""
        Tq = self._Tbasis.interpolate(self.T)
        alpha = self.mat.alpha(np.clip(Tq, 373.15, None))  # 表 373.15 起，低温端外推
        dT = Tq - 293.15                                  # Zero=20°C
        e = np.zeros((4, *Tq.shape))
        e[0] = e[1] = e[2] = alpha * dT
        return e

    def _rho_qp(self):
        """积分点密度（常数 ρ(T)，轴对称圆盘离心力用）。"""
        if self._rho_override is not None:
            return self._rho_override
        Tq = self._Tbasis.interpolate(self.T)
        return self.mat.rho(Tq)

    # ---------- 组装/求解 ----------
    def _assemble(self):
        D = self._D_at_qp()
        K = _stiffness.assemble(self.basis, D=D)
        f = _thermal_load.assemble(self.basis, D=D,
                                   eps_th=self._eps_th_at_qp())
        if self._omega2 > 0.0:
            f = f + _centrifugal.assemble(self.basis, rho=self._rho_qp(),
                                          omega2=self._omega2)
        for facets, (tr, tz) in self._tractions:
            fb = FacetBasis(self.mesh, self.basis.elem, facets=facets,
                            intorder=self._quadrature)
            trac = np.array([np.full(fb.X.shape[1:], tr),
                             np.full(fb.X.shape[1:], tz)])
            f = f + _traction.assemble(fb, trac=trac)
        return K, f

    def solve(self, tol: float = 1e-10, max_iter: int = 10) -> ThermoElasticResult:
        """热弹性解（D(T) Picard；温度场固定）。必须先 set_constraint。"""
        if self._cdofs is None:
            raise ValueError("热弹性必须约束刚体模态（set_constraint）")
        u = np.zeros(self.basis.N)
        err, it = np.inf, 0
        for it in range(1, max_iter + 1):
            K, f = self._assemble()
            u_iter = np.zeros(self.basis.N)
            u_iter[self._cdofs] = self._cvals
            u_new = solve(*condense(K, f, x=u_iter, D=self._cdofs))
            err = np.linalg.norm(u_new - u) / max(1.0, np.linalg.norm(u_new))
            u = u_new
            if err < tol:
                break
        self._u = u
        sigma, vm = self._stress_recovery()
        return ThermoElasticResult(converged=bool(err < tol), iters=it,
                                   u=u, sigma=sigma, von_mises=vm)

    # ---------- 应力恢复 ----------
    def _stress_recovery(self):
        """σ = D(ε−ε_th) 积分点求值 → 单元均值 → 节点平均。"""
        D = self._D_at_qp()

        # 应变需要位移梯度在积分点: 用 CellBasis.map 形式手工重建。
        # skfem 的 interpolate 只对 nodal 字段插值；梯度积分点值用
        # 位移梯度插值（P1 梯度是常数/每单元）——这里用单元常应变近似:
        # P1 单元 ε 在单元内为常数 → 直接对每单元取三节点平均位移梯度。
        t = self.mesh.t                              # (3, nelems)
        p = self.mesh.p                              # (2, n_nodes)
        # 节点位移 (u_r, u_z)
        u_nodes = self._u[self.basis.nodal_dofs]     # (2, n_nodes)
        # 每单元形心坐标的应变: P1 → 梯度常数, 单元内任点皆可
        # 用 skfem affine 映射: grad 常数 = inv(J) @ 节点值差商
        sig_nodes = {k: np.zeros(p.shape[1]) for k in ("r", "z", "theta", "rz")}
        vm_nodes = np.zeros(p.shape[1])
        cnt = np.zeros(p.shape[1])
        n_elems = t.shape[1]
        # 预取积分点温度的单元均值（D 也近似单元常值）
        Tq = self._Tbasis.interpolate(self.T)
        T_elem = Tq.reshape(n_elems, -1).mean(axis=1)
        E = self.mat.E(T_elem)
        nu = self.mat.nu(T_elem)
        D11 = E * (1 - nu) / ((1 + nu) * (1 - 2 * nu))
        D12 = E * nu / ((1 + nu) * (1 - 2 * nu))
        D44 = E / (2 * (1 + nu))
        alpha = self.mat.alpha(np.clip(T_elem, 373.15, None))
        dT = T_elem - 293.15
        # 逐单元应变（P1 常应变）
        for e in range(n_elems):
            i, j, k = t[0, e], t[1, e], t[2, e]
            x1, x2, x3 = p[:, i], p[:, j], p[:, k]
            A = np.array([[x2[0] - x1[0], x3[0] - x1[0]],
                          [x2[1] - x1[1], x3[1] - x1[1]]])
            detA = A[0, 0] * A[1, 1] - A[0, 1] * A[1, 0]
            if detA == 0:
                continue
            invA = np.linalg.inv(A)
            # 形函数导数（对物理坐标，常数）；列序 (N1, N2, N3):
            #   N1 = 1−λ₂−λ₃ → ∂N/∂λ = (−1, −1);  N2 = λ₂ → (1, 0);  N3 = λ₃ → (0, 1)
            # 链法则 dN = (∂N/∂λ)·(∂λ/∂x) = B @ invA，行序转置即 invA.T @ B
            dN = invA.T @ np.array([[-1.0, 1.0, 0.0],
                                    [-1.0, 0.0, 1.0]])
            # 节点位移
            U = np.column_stack([u_nodes[:, i], u_nodes[:, j], u_nodes[:, k]])  # (2,3)
            gradU = U @ dN.T                      # (2,2): [∂/∂r, ∂/∂z] × (u_r,u_z)
            r_c = (x1[0] + x2[0] + x3[0]) / 3.0
            u_r_c = (u_nodes[0, i] + u_nodes[0, j] + u_nodes[0, k]) / 3.0
            eps = np.array([
                gradU[0, 0],                      # εr
                gradU[1, 1],                      # εz
                u_r_c / r_c,                      # εθ（形心）
                gradU[0, 1] + gradU[1, 0],        # γrz
            ])
            eps_th = alpha[e] * dT[e] * np.array([1.0, 1.0, 1.0, 0.0])
            D = np.array([
                [D11[e], D12[e], D12[e], 0.0],
                [D12[e], D11[e], D12[e], 0.0],
                [D12[e], D12[e], D11[e], 0.0],
                [0.0, 0.0, 0.0, D44[e]],
            ])
            s = D @ (eps - eps_th)
            vm = np.sqrt(0.5 * ((s[0] - s[1])**2 + (s[1] - s[2])**2
                                + (s[2] - s[0])**2 + 3 * s[3]**2))
            nodes = (i, j, k)
            for idx, key in enumerate(("r", "z", "theta", "rz")):
                sig_nodes[key][list(nodes)] += s[idx]
            vm_nodes[list(nodes)] += vm
            cnt[list(nodes)] += 1
        for d in (*sig_nodes.values(), vm_nodes):
            d /= np.maximum(cnt, 1)
        return sig_nodes, vm_nodes
