"""tools — pysas 工具子包（重依赖隔离区）。

纪律：核心包（datamodel/fluids/elements/assembly/solver/io）**永不
import tools**；tools 只消费 pysas 公开 API（依赖单向 tools → 核心）。
重依赖（sklearn/skl2onnx/onnxruntime 等）只允许出现在本子包，且一律
函数内 lazy import——`import pysas` 本身保持 numpy/scipy-only。

住户：
  surrogate/  代理元件训练管线（采样 → 训练 → 导出 .npz 表 / .onnx）
"""
