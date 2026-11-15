"""コマンドの入口（`python -m ...`）で使う関数。"""

import sys


def utf8_stdio() -> None:
    """標準出力と標準エラーを UTF-8 にする。

    Windows では、パイプ（Git Bash、npm）へ出すときの既定がコードページ（cp932 など）になる。
    日本語は書けても、polars の表の罫線（`┆`）などで UnicodeEncodeError になって止まるため。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
