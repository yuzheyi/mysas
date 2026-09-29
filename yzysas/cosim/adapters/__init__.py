"""adapters — FE 适配层（维度扩展点）。

fe_base.py     抽象契约（本模块定义，coupling/ 只依赖它）
fe_axisym.py   2D 轴对称实现（包装 pyfem）
fe_3d.py       将来 3D 实现落点（skfem MeshTet，暂不建）
"""
