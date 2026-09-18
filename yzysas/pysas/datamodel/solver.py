"""solver — 非线性方程组算法配置（对应 sas_solver.h）。

三层包含关系 = 联合算法的骨架：
  同伦延拓(Homotopy) 内含 离散牛顿(DiscreteNewton) 内含 阻尼牛顿(Newton)。
只存设置与报告；算法实现见 pysas.solver 包（M1：scaling/newton/discrete）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NewtonOptions:
    """阻尼牛顿（内核，局部收敛）。迭代全程在缩放坐标 x̃ 上（见 pysas.solver.scaling）。"""
    max_iter: int = 50
    tol: float = 1.0e-6          # 收敛判据 max|F̃|（缩放坐标无量纲残差——"相对判据"）
    relax_init: float = 1.0      # 初始松弛因子 α（残差不降自动减半）
    relax_min: float = 1.0e-3    # α 下限，低于此判失败
    damped: bool = True          # False = 裸牛顿（α≡1，跳过线搜索），仅对比实验/调试用


@dataclass
class NewtonReport:
    converged: bool = False
    iters: int = 0
    final_residual: float = 0.0
    final_relax: float = 1.0


@dataclass
class DiscreteNewtonOptions:
    """离散牛顿：雅可比用差分代替解析导数（差分在缩放坐标内做，步长自愈）。"""
    newton: NewtonOptions = field(default_factory=NewtonOptions)
    fd_eps_rel: float = 1.0e-7   # 差分步长 = eps * max(1, |x̃_j|)（缩放坐标内）
    use_coloring: bool = False   # 距离-2 染色并行扰动【悬置未实现，见 待定问题.md】
                                 # （C++ 规格默认 true；Python M1 先逐列差分）


@dataclass
class HomotopyOptions:
    """同伦延拓：λ 从 0（线性网络）推到 1（真实非线性系统）。"""
    inner: DiscreteNewtonOptions = field(default_factory=DiscreteNewtonOptions)
    lambda0: float = 0.0
    lambda1: float = 1.0
    d_lambda_init: float = 0.1
    d_lambda_min: float = 1.0e-4  # 步长下限（低于此触发"改变同伦"）
    max_steps: int = 200


@dataclass
class HomotopyReport:
    converged: bool = False
    n_lambda_steps: int = 0
    n_newton_total: int = 0
    final_residual: float = 0.0
    lambda_path: list[float] = field(default_factory=list)  # λ 轨迹（诊断用）


@dataclass
class SolverSettings:
    mode: int = 1  # 0=纯牛顿(需解析雅可比,未实现) 1=离散牛顿 2=同伦延拓(M2)
                   # （C++ 规格默认 0；Python 首个可用内核是离散牛顿，默认 1）
    newton: NewtonOptions = field(default_factory=NewtonOptions)
    discrete: DiscreteNewtonOptions = field(default_factory=DiscreteNewtonOptions)
    homotopy: HomotopyOptions = field(default_factory=HomotopyOptions)
