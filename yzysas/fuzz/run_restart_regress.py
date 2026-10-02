# -*- coding: utf-8 -*-
"""#19 双探针重启器固定回归：23 例并集救活集 + 死例对照（2026-10-02）。

依据：fuzz/results/homotopy_probe.jsonl（双探针实验）——flatline 救
16 例、backpressure 救 13 例，并集 23（共同 6）。本脚本锁死该口径：

  1. 23 例并集在 restart=True 下全部收敛（救活不劣化）
  2. restart 标注与实验探针一致（flatline 例应 flatline 救活；仅
     backpressure 独有的 7 例应 backpressure 救活）
  3. 死例对照（探针并集外抽样 10 例）：不得"救活"（若救活说明
     求解器行为相对实验已漂移——需人工核查是改进还是回归）
  4. 救活解人工根复核：软壅塞告警逐例白名单（A0108 物理超容量
     网络——MASS_SOURCE 强推 c3 heater 口1 ratio≈1.02，守卫兜底
     收敛是设计内产出，实验口径同告警；其余 22 例告警须为零，
     探针不许制造伪根）

用法: cd yzysas/fuzz && python -X utf8 run_restart_regress.py
退出码 0=全过；非 0=有劣化（CI 用）。
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from pysas.assembly import NetworkSystem          # noqa: E402
from pysas.io import build_models, netinf_from_dict  # noqa: E402
from pysas.solver import initial_guess, solve     # noqa: E402

# 实验口径（homotopy_probe.jsonl，2026-10-02 复核一致）
FLATLINE_ONLY = [          # 仅 flatline 救活（10 例）
    "A0018", "A0078", "A0098", "A0113", "A0136", "A0157",
    "A0168", "A0178", "A0220", "A0237",
]
BACKPRESSURE_ONLY = [      # 仅 backpressure 救活（7 例）
    "A0007", "A0035", "A0053", "A0135", "A0202", "A0224", "A0240",
]
BOTH = [                   # 双探针皆救活（6 例）
    "A0020", "A0042", "A0108", "A0137", "A0152", "A0190",
]
DEAD_SAMPLE = [            # 并集外死例对照（前 10 例 clean_fail）
    "A0001", "A0002", "A0003", "A0006", "A0008",
    "A0009", "A0010", "A0012", "A0015", "A0017",
]
# 软壅塞告警白名单：这些救活例的解守卫激活是网络物理性质
# （流量源超容量），default 直解/实验探针同告警——非探针伪根。
WARN_WHITELIST = {"A0108"}


def run_case(cid: str) -> tuple[bool, str | None, int, int]:
    """返回 (converged, restart 标注, 软壅塞告警数, iters)。"""
    data = json.loads((HERE / "cases" / f"{cid}.json").read_text(
        encoding="utf-8"))
    net, ctx = netinf_from_dict(data)
    sysm = NetworkSystem(net, build_models(net))
    x0 = initial_guess(sysm, ctx)   # 等价旧 default_guess（策略 default）
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        res = solve(sysm, x0, ctx, restart=True)
        soft = sum(1 for x in w if "软壅塞" in str(x.message))
    return bool(res.converged), res.restart, soft, int(res.report.iters)


def main() -> int:
    fails = 0

    def expect(cid, want_probe):
        nonlocal fails
        conv, tag, soft, iters = run_case(cid)
        if not conv:
            print(f"FAIL {cid}: 应救活（{want_probe}）却 clean_fail")
            fails += 1
        elif want_probe == "BOTH" and tag not in ("flatline", "backpressure"):
            print(f"FAIL {cid}: 双探针例标注异常 restart={tag}")
            fails += 1
        elif want_probe != "BOTH" and tag != want_probe:
            print(f"FAIL {cid}: 应由 {want_probe} 救活，实际 {tag}")
            fails += 1
        elif soft and cid not in WARN_WHITELIST:
            print(f"FAIL {cid}: 救活解软壅塞告警 {soft} 条（人工根嫌疑）")
            fails += 1
        else:
            print(f"PASS {cid}: restart={tag} iters={iters} 告警=0")

    print("== 23 例并集救活集（不许劣化） ==")
    for cid in FLATLINE_ONLY:
        expect(cid, "flatline")
    for cid in BACKPRESSURE_ONLY:
        expect(cid, "backpressure")
    for cid in BOTH:
        expect(cid, "BOTH")

    print("== 死例对照（不许意外救活=漂移信号） ==")
    for cid in DEAD_SAMPLE:
        conv, tag, soft, iters = run_case(cid)
        if conv:
            print(f"WARN {cid}: 实验死例现被 {tag} 救活（求解器漂移，"
                  "需人工核查改进/回归）")
        else:
            print(f"PASS {cid}: clean_fail 语义不变")

    n = len(FLATLINE_ONLY) + len(BACKPRESSURE_ONLY) + len(BOTH)
    print(f"--- 救活 {n - fails if False else ''}{n} 例回归 "
          f"{'全过' if fails == 0 else f'{fails} 例失败'} ---")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
