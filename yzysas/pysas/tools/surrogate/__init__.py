"""surrogate — 代理元件训练管线（想法 8 / M7 v1；依赖 → elements/surrogate）。

管线（CLI 入口 train.py）:
  sample.py  合成采样器——独立物理源公式（不 import elements/ 代码）出
             无量纲样本 (PR, Φ; Cd, γ)
  train.py   训练/导出 CLI：--backend table（npz 表）| onnx（sklearn
             MLP → skl2onnx；缺依赖打印 pip 指引不硬崩）

产物契约 = elements/surrogate/model_bank.py 的文件格式（唯一真源）。
真实数据（Fluent/试验 CSV）接入 = 后续阶段：列名 schema 定后替换
sample.py 数据源，train.py 主体不变。
"""
