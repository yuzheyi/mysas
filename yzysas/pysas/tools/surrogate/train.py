"""train — 代理元件模型训练/导出 CLI（tools 重依赖隔离区住户）。

用法（任意 CWD——脚本自举 sys.path 到 yzysas）:
  python pysas/tools/surrogate/train.py --backend table --out pysas/out/phi_cd08.npz
  python pysas/tools/surrogate/train.py --backend onnx  --out pysas/out/phi_cd08.onnx

产物契约（= elements/surrogate/model_bank.py）:
  .npz  一维 Φ 表（PCHIP 保形插值；零依赖主路径）
  .onnx sklearn MLPRegressor → skl2onnx（附同名 .meta.json 边车）
合成源 = sample.phi_orifice（独立闭式）。真实数据接入时替换数据源即可。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..")))

from pysas.tools.surrogate.sample import (  # noqa: E402
    phi_orifice, pr_crit_of, synthetic_samples)


def _samples(args):
    return synthetic_samples(cd=args.cd, gamma=args.gamma,
                             pr_min=args.pr_min, n_pr=args.n_pr,
                             noise=args.noise, seed=args.seed)


def _ensure_dir(out: str) -> None:
    d = os.path.dirname(os.path.abspath(out))
    os.makedirs(d, exist_ok=True)


def train_table(args) -> int:
    from pysas.elements.surrogate.runtime import TableRuntime
    s = _samples(args)
    phi = np.minimum.accumulate(s["phi"])   # 噪声单调投影（上包络）
    pr_crit = pr_crit_of(args.gamma)
    _ensure_dir(args.out)
    np.savez(args.out,
             format="pysas-surrogate-table", version=1,
             pr_grid=s["pr"], phi_grid=phi, pr_crit=pr_crit,
             gamma_train=args.gamma, a_ref_train=args.a_ref,
             cd_train=args.cd,
             source=f"synthetic-orifice(cd={args.cd}, gamma={args.gamma})",
             note="train.py v1")
    print(f"[train] 表模型已写出: {args.out}（{s['pr'].size} 点，"
          f"PR∈[{s['pr'][0]:.3f},{s['pr'][-1]:.2f}]，Φ_max={phi[0]:.4f}）")
    # 自检 1：PCHIP 中点 vs 闭式（表后端精度标尺）
    rt = TableRuntime(s["pr"], phi, pr_crit, meta={"gamma_train": args.gamma})
    mid = 0.5 * (s["pr"][:-1] + s["pr"][1:])
    ref = phi_orifice(mid, args.cd, args.gamma)
    got = np.array([rt.flow_coefficient(float(p)) for p in mid])
    print(f"[train] PCHIP 中点对拍 max|ΔΦ|={np.abs(got - ref).max():.2e}")
    # 自检 2：自研 PCHIP vs scipy 同算法（实现正确性对拍，非物理）
    try:
        from scipy.interpolate import PchipInterpolator as _Pchip
        d = np.abs(got - _Pchip(s["pr"], phi)(mid)).max()
        print(f"[train] 与 scipy PchipInterpolator 对拍 max|ΔΦ|={d:.2e}")
    except ImportError:
        pass
    return 0


def train_onnx(args) -> int:
    try:
        from sklearn.neural_network import MLPRegressor
    except ImportError:
        print("[train] 缺 sklearn——pip install scikit-learn")
        return 2
    try:
        from skl2onnx import convert_sklearn
        from skl2onnx.common.data_types import FloatTensorType
    except ImportError:
        print("[train] 缺 skl2onnx——pip install skl2onnx"
              "（零依赖的表后端 .npz 无需它）")
        return 2
    s = _samples(args)
    X = s["pr"].reshape(-1, 1).astype(np.float32)
    y = s["phi"]
    mlp = MLPRegressor(hidden_layer_sizes=(24, 24), activation="tanh",
                       solver="lbfgs", max_iter=8000, tol=1e-9,
                       random_state=args.seed)
    mlp.fit(X, y)
    onnx_model = convert_sklearn(
        mlp, initial_types=[("pr", FloatTensorType([None, 1]))],
        target_opset=17)
    _ensure_dir(args.out)
    with open(args.out, "wb") as f:
        f.write(onnx_model.SerializeToString())
    side = {"gamma_train": args.gamma, "a_ref_train": args.a_ref,
            "cd_train": args.cd, "pr_crit": pr_crit_of(args.gamma),
            "source": f"mlp-synthetic(cd={args.cd})", "hidden": "24x24 tanh"}
    with open(os.path.splitext(args.out)[0] + ".meta.json", "w",
              encoding="utf-8") as f:
        json.dump(side, f, ensure_ascii=False, indent=2)
    print(f"[train] ONNX 模型已写出: {args.out}（MLP 24x24 tanh，"
          f"R²={mlp.score(X, y):.6f}）")
    # 推理对拍（onnxruntime 可选）
    try:
        from pysas.elements.surrogate.model_bank import load_model
        rt = load_model(args.out)
        probe = s["pr"][::7]
        fit = np.array([rt.flow_coefficient(float(p)) for p in probe])
        ref = phi_orifice(probe, args.cd, args.gamma)
        print(f"[train] onnxruntime 对拍 max|ΔΦ|={np.abs(fit - ref).max():.2e}"
              f"（vs 闭式）")
    except ImportError:
        print("[train] onnxruntime 未装（pip install onnxruntime）——"
              "跳过推理对拍")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="代理元件模型训练 CLI")
    p.add_argument("--backend", choices=["table", "onnx"], default="table")
    p.add_argument("--out", required=True, help="输出模型文件路径")
    p.add_argument("--cd", type=float, default=0.8)
    p.add_argument("--gamma", type=float, default=1.4)
    p.add_argument("--pr-min", type=float, default=0.02)
    p.add_argument("--n-pr", type=int, default=65)
    p.add_argument("--noise", type=float, default=0.0,
                   help="Φ 乘性高斯噪声强度（合成去噪演练用）")
    p.add_argument("--a-ref", type=float, default=1.0e-4,
                   help="训练基准面积 m²（仅溯源信息，应用侧 A_ref 由"
                        "元件 params[面积比]×口面积决定）")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)
    return train_table(args) if args.backend == "table" else train_onnx(args)


if __name__ == "__main__":
    sys.exit(main())
