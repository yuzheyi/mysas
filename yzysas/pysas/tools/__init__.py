"""tools — pysas 工具子包（重依赖隔离区 + 工具集合体）。

纪律：核心包（datamodel/fluids/elements/assembly/solver/io）**永不
import tools**；tools 只消费 pysas 公开 API（依赖单向 tools → 核心）。
重依赖（torch/onnxruntime 等）只允许出现在本子包，且一律
函数内 lazy import——`import pysas` 本身保持 numpy/scipy-only。

住户：
  surrogate/  代理元件训练管线（torch 引擎：采样 → 拟合 → ONNX）
  ui/         可视化前后处理（python -m pysas.tools.ui 生成单文件
              HTML；2026-09-30 位置终裁 tools/ui 子包，与 surrogate/
              并列——tools 是工具集合体，依赖单向纪律不变）
"""
