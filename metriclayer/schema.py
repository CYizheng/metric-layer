"""把 SQLite 的表结构描述成文本，供提示词与 --schema 使用。"""

from __future__ import annotations

import sqlite3

__all__ = ["describe"]


def describe(con: sqlite3.Connection, labels: dict[str, str] | None = None) -> str:
    labels = labels or {}
    lines: list[str] = []
    tables = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for t in tables:
        cols = con.execute(f'PRAGMA table_info("{t}")').fetchall()
        lines.append(f"TABLE {t} (")
        for cid, name, ctype, notnull, dflt, pk in cols:
            key = f"{t[0] if t else 'a'}.{name}"
            cn = labels.get(key) or labels.get(name) or ""
            bits = [ctype or "TEXT"]
            if pk:
                bits.append("PK")
            if notnull:
                bits.append("NOT NULL")
            tail = f"  -- {cn}" if cn else ""
            lines.append(f"  {name} {' '.join(bits)},{tail}")
        lines.append(")")
    return "\n".join(lines)
