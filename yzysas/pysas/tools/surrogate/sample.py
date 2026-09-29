"""sample — 合成采样器：代理元件训练样本（独立物理源公式）。

"独立"纪律（与 test_solver 手算同款）：物理公式在本文件重新实现，
不 import elements/ 任何代码——训练数据与被验证元件不同源，防自证。

物理源 v1 = 孔板族无量纲特性 Φ(PR; Cd, γ)（闭式，R 在无量纲化中消去）:
  超临界 (PR ≤ PR_crit):  Φ = Cd
  亚临界:                Φ = Cd·√(2γ/(γ−1))·PR^(1/γ)·√(1−PR^((γ−1)/γ))
                            / [√γ·(2/(γ+1))^((γ+1)/(2(γ−1)))]
采样网格：PR 基线网格 + 近临界（PR→1，√(1−PR) 奇异）二次加密 +
PR_crit 精确落点（kink 不被插值抹平）。
"""
from __future__ import annotations

import numpy as np


def pr_crit_of(gamma: float) -> float:
    """临界压比（等熵壅塞）；Φ 表的 kink 落点。"""
    return (2.0 / (gamma + 1.0)) ** (gamma / (gamma - 1.0))


def phi_orifice(pr, cd: float, gamma: float):
    """孔板族无量纲特性（闭式；标度契约同 elements/surrogate/runtime）。"""
    pr = np.clip(np.asarray(pr, dtype=float), 0.0, 1.0)
    crit = pr_crit_of(gamma)
    choke = np.sqrt(gamma) * (2.0 / (gamma + 1.0)) ** (
        (gamma + 1.0) / (2.0 * (gamma - 1.0)))
    sub = np.sqrt(2.0 * gamma / (gamma - 1.0)) * pr ** (1.0 / gamma) \
        * np.sqrt(np.maximum(1.0 - pr ** ((gamma - 1.0) / gamma), 0.0))
    return cd * np.where(pr <= crit, choke, sub) / choke


def pr_grid(pr_min: float = 0.02, pr_max: float = 1.0, n: int = 65,
            gamma: float = 1.4) -> np.ndarray:
    """采样网格：二次加密近 PR→1（√(1−PR) 奇异区）+ pr_crit 精确落点。"""
    t = np.linspace(0.0, 1.0, n)
    grid = pr_min + (pr_max - pr_min) * (1.0 - (1.0 - t) ** 2)
    grid = np.unique(np.concatenate(
        [grid, [pr_crit_of(gamma)], [pr_max]]))
    return np.clip(grid, pr_min, pr_max)


def synthetic_samples(cd: float = 0.8, gamma: float = 1.4,
                      pr_min: float = 0.02, n_pr: int = 65,
                      noise: float = 0.0, seed: int = 0) -> dict:
    """合成样本集 → {"pr", "phi", "cd", "gamma"}（可选乘性高斯噪声）。

    noise>0 时 phi·(1+ε)，ε~N(0, noise)——表后端训练端再做单调投影
    （np.minimum.accumulate），MLP 后端靠拟合自身平滑去噪。
    """
    rng = np.random.default_rng(seed)
    pr = pr_grid(pr_min=pr_min, n=n_pr, gamma=gamma)
    phi = phi_orifice(pr, cd, gamma)
    if noise > 0.0:
        phi = phi * (1.0 + rng.normal(0.0, noise, phi.shape))
    return {"pr": pr, "phi": phi, "cd": cd, "gamma": gamma}
