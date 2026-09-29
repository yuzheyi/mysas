"""surrogate — 数据驱动代理元件子包（SURROGATE_FLOW，想法 8 / M7 v1）。

与 elements/ 平铺元件的关系：本子包 = 元件本体 + 模型运行时
（纯 numpy 主路径，ONNX 可选 lazy）；训练侧在 pysas/tools/surrogate/
（sklearn/skl2onnx 重依赖隔离区，依赖单向 tools → elements）。

  flow.py        SurrogateFlowModel（两口压差→流量，残差同 orifice）
  runtime.py     Φ(PR) 运行时契约 + Table/Onnx 两后端
  model_bank.py  模型文件加载校验（.npz/.onnx 按扩展名分发）

import 纪律：子包内一律 `from pysas.elements.base import …` 直连路径
（与平铺元件同款）；elements/__init__.py 保持极简不引本子包（防环，
注册走 io/netinf._default_registry）。
"""
from pysas.elements.surrogate.flow import SurrogateFlowModel
from pysas.elements.surrogate.model_bank import load_model

__all__ = ["SurrogateFlowModel", "load_model"]
