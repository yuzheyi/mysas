"""model_bank — 代理模型文件加载与校验（按扩展名分发）。

文件格式（v1 契约，唯一真源在本文件；train.py 产出、TableRuntime 消费）:
  *.npz   一维 Φ 表——键：format="pysas-surrogate-table", version=1,
          pr_grid, phi_grid, pr_crit, gamma_train, a_ref_train, cd_train,
          source, note（gamma_train 供运行期不一致提示；a_ref_train 仅
          溯源信息——应用侧 A_ref 一律由元件 params[面积比]×口面积决定）
  *.onnx  单输入 MLP（OnnxRuntime）+ 同名 .meta.json 边车（可选）

校验一律在构造期完成（坏文件在 build_models 即报，不进牛顿迭代）。
"""
from __future__ import annotations

import json
import os

import numpy as np

from pysas.elements.surrogate.runtime import OnnxRuntime, TableRuntime

TABLE_FORMAT = "pysas-surrogate-table"
TABLE_VERSION = 1


def load_model(model_path: str):
    """模型文件 → 运行时对象（TableRuntime / OnnxRuntime）。

    元件 __init__（build_models 期）调用——坏路径/坏格式在此清晰报错。
    """
    if not model_path:
        raise ValueError("代理元件缺 model_path（Comp.model_path 为空——"
                         "SURROGATE_FLOW 的特性模型由模型文件携带）")
    if not os.path.isfile(model_path):
        raise ValueError(f"代理模型文件不存在: {model_path!r}")
    ext = os.path.splitext(model_path)[1].lower()
    if ext == ".npz":
        return _load_table(model_path)
    if ext == ".onnx":
        return _load_onnx(model_path)
    raise ValueError(f"不支持的代理模型格式 {ext!r}（支持 .npz 表 / .onnx）")


def _load_table(path: str) -> TableRuntime:
    with np.load(path, allow_pickle=False) as z:
        keys = set(z.files)
        if "format" not in keys or str(z["format"].item()) != TABLE_FORMAT:
            raise ValueError(f"{path}: 不是 pysas 代理表模型（缺/错 format 键）")
        if "version" not in keys or int(z["version"]) != TABLE_VERSION:
            raise ValueError(f"{path}: 表版本不受支持（当前 v{TABLE_VERSION}）")
        for need in ("pr_grid", "phi_grid"):
            if need not in keys:
                raise ValueError(f"{path}: 表模型缺 {need}")
        pr, phi = z["pr_grid"], z["phi_grid"]
        meta = {k: z[k].item() for k in
                ("pr_crit", "gamma_train", "a_ref_train", "cd_train",
                 "source", "note") if k in keys}
    # 形状/范围/单调性校验全在构造期（见 TableRuntime.__init__）
    return TableRuntime(pr, phi, meta.pop("pr_crit", None), meta=meta)


def _load_onnx(path: str) -> OnnxRuntime:
    side = os.path.splitext(path)[0] + ".meta.json"
    meta = {}
    if os.path.isfile(side):
        with open(side, encoding="utf-8") as f:
            meta = json.load(f)
    return OnnxRuntime(path, meta=meta)
