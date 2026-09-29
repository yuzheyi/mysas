"""netelem — pysas 侧耦合元件（外部注册进 pysas 工厂，pysas 零改动）。

import 本模块即完成注册（pysas io/netinf.py 的 _MODEL_REGISTRY 是
公开模块级字典 + register_model 公开装饰器）:

    import cosim.netelem   # ← netinf_from_dict/build_models 前执行一次

模块:
    filmwall.py   WALL_FILM 壁面换热（热耦合，q 分布积分）
    （间隙耦合封严元件 Phase 4+ 加文件）
"""
from cosim.netelem.filmwall import FilmWallModel
from pysas.io.netinf import register_model

register_model(FilmWallModel)
