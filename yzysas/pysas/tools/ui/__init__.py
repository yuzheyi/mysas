"""ui — 可视化前后处理子包（单文件 HTML 工具，2026-09-30 位置终裁）。

用法: python -m pysas.tools.ui [--open] [-o 路径]

职责：
  * 前处理：浏览器内拖拽搭网络 → 导出 netinf JSON（io/netinf.py 同 schema）
  * 后处理：粘贴 out/ 结果文本 → 表格/流量条形图渲染
  * app.py 本体（SCHEMA 元数据 + HTML 模板）

层级裁决三轮定稿：yzysas 根 → pysas/ui 独立包 → 收编 tools →
tools/ui 子包（与 surrogate/ 训练管线并列，依赖单向纪律不变：
核心包永不 import tools）。
"""
