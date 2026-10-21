"""终端表格渲染。中文按显示宽度对齐，不用等宽字体的假象骗自己。"""

from __future__ import annotations

import unicodedata

__all__ = ["render_table", "width_of"]


def width_of(s: str) -> int:
    """中文/全角算 2 列，其余算 1 列。"""
    w = 0
    for ch in str(s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def _pad(s: str, width: int, right: bool = False) -> str:
    gap = width - width_of(s)
    gap = max(gap, 0)
    return (" " * gap + s) if right else (s + " " * gap)


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def render_table(cols: list[str], rows: list[tuple], aligns: list[bool] | None = None) -> str:
    """aligns[i] 为 True 表示该列右对齐（数值列）。"""
    if not cols:
        return "（无结果）"
    cells = [[_fmt(v) for v in row] for row in rows]
    aligns = aligns or [False] * len(cols)

    widths = []
    for i, c in enumerate(cols):
        w = width_of(c)
        for row in cells:
            w = max(w, width_of(row[i]))
        widths.append(min(w, 40))

    out = ["  " + "  ".join(_pad(c, widths[i], aligns[i]) for i, c in enumerate(cols))]
    out.append("  " + "  ".join("─" * w for w in widths))
    for row in cells:
        out.append(
            "  " + "  ".join(_pad(row[i], widths[i], aligns[i]) for i in range(len(cols)))
        )
    return "\n".join(out)
