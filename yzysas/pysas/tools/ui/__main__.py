"""pysas.tools.ui CLI 入口——生成单文件 HTML 前后处理工具。

用法:
    python -m pysas.tools.ui               # 生成 tools/ui/sas_studio.html 并打印路径
    python -m pysas.tools.ui -o my.html    # 指定输出路径
    python -m pysas.tools.ui --open        # 生成后自动用默认浏览器打开

（在 yzysas/ 目录下运行——pysas 是它的子包）
"""
import argparse
import webbrowser
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m pysas.tools.ui",
        description="生成 pysas 网络前处理/结果查看单文件 HTML 工具")
    ap.add_argument("-o", "--out", default=None,
                    help="输出 HTML 路径（缺省 tools/ui/sas_studio.html）")
    ap.add_argument("--open", action="store_true",
                    help="生成后用默认浏览器打开")
    args = ap.parse_args(argv)

    from pysas.tools.ui.app import build_html
    html = build_html()

    out = Path(args.out) if args.out else \
        Path(__file__).resolve().parent / "sas_studio.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"-> 已生成 {out}（{len(html)/1024:.0f} KB）——浏览器打开即用，离线可用")

    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
