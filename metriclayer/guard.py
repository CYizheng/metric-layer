"""只读守卫：任何 SQL 在执行前都必须过这一关。

三层防护，缺一层都不够：
1. 连接层 —— 用 `mode=ro` 打开 + `PRAGMA query_only`，就算判断逻辑被绕过也写不进去；
2. 语句层 —— 必须是单条 SELECT/WITH，禁用写操作与扩展关键字；
3. 结果层 —— 强制 LIMIT，避免一条误写把内存打爆。

判断前先剥掉注释和字符串字面量，否则字段值里出现 "drop" 就会被误判。
"""

from __future__ import annotations

import os
import re
import sqlite3
import time

__all__ = ["GuardError", "open_readonly", "check_sql", "run"]

FORBIDDEN = {
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "attach", "detach", "pragma", "vacuum", "analyze", "reindex",
    "begin", "commit", "rollback", "savepoint", "release", "trigger",
}


class GuardError(ValueError):
    """SQL 未通过只读校验。"""


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _strip_literals(sql: str) -> str:
    """把 '...' 与 "..." 的内容抹掉，避免字段值里的关键字触发误判。"""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:  # 转义的连续引号
                        i += 2
                        continue
                    break
                i += 1
            out.append("''")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def open_readonly(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    uri = "file:" + os.path.abspath(db_path).replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.execute("PRAGMA query_only = ON")
    return con


def check_sql(sql: str, default_limit: int = 500) -> str:
    """校验并返回可执行的 SQL（必要时补 LIMIT）。"""
    if not sql or not sql.strip():
        raise GuardError("SQL 为空")

    body = _strip_comments(sql).strip().rstrip(";").strip()
    if not body:
        raise GuardError("SQL 里只有注释")

    bare = _strip_literals(body)

    if ";" in bare:
        raise GuardError("只允许单条语句，检测到分号分隔的多条语句")

    tokens = re.findall(r"[A-Za-z_]+", bare)
    head = tokens[0].lower() if tokens else ""
    if head not in ("select", "with"):
        raise GuardError(f"只允许 SELECT / WITH 查询，检测到以 {head!r} 开头")

    words = set(re.findall(r"[A-Za-z_]+", bare.lower()))
    bad = sorted(words & FORBIDDEN)
    if bad:
        raise GuardError("包含被禁止的关键字：" + "、".join(bad))

    if not re.search(r"\blimit\b", bare.lower()):
        body = f"{body}\nLIMIT {default_limit}"

    return body


def run(con: sqlite3.Connection, sql: str, max_rows: int = 500):
    """执行已校验的 SQL，返回 (列名, 行, 耗时毫秒, 是否被截断)。"""
    checked = check_sql(sql, default_limit=max_rows)
    t0 = time.perf_counter()
    cur = con.execute(checked)
    cols = [d[0] for d in cur.description or []]
    rows = cur.fetchmany(max_rows + 1)
    ms = (time.perf_counter() - t0) * 1000
    truncated = len(rows) > max_rows
    return cols, rows[:max_rows], ms, truncated
