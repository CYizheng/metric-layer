"""单元测试。不依赖 pytest：`python tests/test_core.py`。

重点测三处「错了不会立刻发现」的地方：时间解析（跨年跨月）、
只读守卫（防注入）、SQL 生成（JOIN 与 LIMIT）。
"""

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metriclayer import guard, sqlgen
from metriclayer.metrics import load_layer
from metriclayer.parse import ParseError, parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LAYER = load_layer(os.path.join(ROOT, "metrics", "ledger.example.yaml"))
TODAY = dt.date(2026, 10, 21)

PASS, FAIL = 0, 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [通过] {name}")
    else:
        FAIL += 1
        print(f"  [失败] {name} {extra}")


def test_time() -> None:
    print("时间解析")
    s = parse("本月投递数", LAYER, TODAY)
    check("本月", "2026-10-01" in s.time_sql and "2026-11-01" in s.time_sql, s.time_sql)
    s = parse("上月投递数", LAYER, TODAY)
    check("上月", "2026-09-01" in s.time_sql and "2026-10-01" in s.time_sql, s.time_sql)
    # 跨年：1 月的上月是去年 12 月
    s = parse("上月投递数", LAYER, dt.date(2026, 1, 15))
    check("跨年的上月", "2025-12-01" in s.time_sql, s.time_sql)
    s = parse("近7天投递数", LAYER, TODAY)
    check("近 7 天", "2026-10-14" in s.time_sql, s.time_sql)
    s = parse("2026年8月投递数", LAYER, TODAY)
    check("指定年月", "2026-08-01" in s.time_sql and "2026-09-01" in s.time_sql, s.time_sql)
    s = parse("投递数", LAYER, TODAY)
    check("无时间词则不加条件", s.time_sql == "", s.time_sql)


def test_parse() -> None:
    print("问句解析")
    s = parse("投递最多的前3个城市", LAYER, TODAY)
    check("指标命中投递数", s.metric.key == "applications", s.metric.key)
    check("维度命中城市", s.group == "city", s.group)
    check("降序 + 前 3", s.order_desc is True and s.limit == 3, f"{s.order_desc},{s.limit}")

    s = parse("各岗位类型的面试场次前2", LAYER, TODAY)
    check("句尾「前2」也算限额", s.limit == 2, s.limit)
    check("面试场次需要事件表", s.metric.needs_events)

    s = parse("近30天投递数", LAYER, TODAY)
    check("「前30天」不被当成限额 30", s.limit is None, s.limit)

    s = parse("前30天的投递数", LAYER, TODAY)
    check("「前N天」按时间解释，不是限额",
          s.limit is None and s.time_label == "近 30 天", f"{s.limit},{s.time_label}")

    s = parse("在流程中的有多少家", LAYER, TODAY)
    check("状态值自动成为过滤条件", any("流程中" in f[1] for f in s.filters), s.filters)

    try:
        parse("今天天气怎么样", LAYER, TODAY)
        check("认不出指标时报错", False)
    except ParseError:
        check("认不出指标时报错", True)


def test_sqlgen() -> None:
    print("SQL 生成")
    s = parse("近30天面试场次按月", LAYER, TODAY)
    sql = sqlgen.build_sql(LAYER, s)
    check("需要事件表的指标会 JOIN", "LEFT JOIN events e ON e.app_id = a.id" in sql, sql)
    check("带上事件类型条件", "e.kind = '面试'" in sql, sql)
    check("时间维度按自身升序", "ORDER BY 1 ASC" in sql, sql)
    check("默认带 LIMIT", "LIMIT" in sql.upper(), sql)

    s = parse("投递数", LAYER, TODAY)
    sql = sqlgen.build_sql(LAYER, s)
    check("标量查询不 JOIN", "JOIN" not in sql, sql)

    s = parse("投递最多的前3个城市", LAYER, TODAY)
    sql = sqlgen.build_sql(LAYER, s)
    check("非时间维度默认按指标降序", "ORDER BY 2 DESC" in sql, sql)
    check("限额写进 SQL", "LIMIT 3" in sql, sql)

    s = parse("投递数", LAYER, TODAY)
    # 构造一个没声明事件表的口径层，验证用「面试场次」时会明确报错而不是拼出坏 SQL
    from dataclasses import replace

    no_events = replace(LAYER, events_table=None, events_on=None)
    try:
        sqlgen.build_sql(no_events, parse("面试场次", LAYER, TODAY))
        check("缺事件表声明时报错", False)
    except ValueError:
        check("缺事件表声明时报错", True)


def test_guard() -> None:
    print("只读守卫")
    ok = guard.check_sql("SELECT 1")
    check("普通查询放行并补 LIMIT", ok.lower().count("limit") == 1, ok)

    bad_cases = [
        ("DELETE FROM applications", "DELETE"),
        ("DROP TABLE applications", "DROP"),
        ("UPDATE applications SET city='x'", "UPDATE"),
        ("PRAGMA table_info(applications)", "PRAGMA"),
        ("ATTACH DATABASE 'x.db' AS x", "ATTACH"),
        ("SELECT 1; DELETE FROM applications", "多条语句"),
        ("INSERT INTO applications (id) VALUES (1)", "INSERT"),
    ]
    for sql, label in bad_cases:
        try:
            guard.check_sql(sql)
            check(f"拦住 {label}", False, sql)
        except guard.GuardError:
            check(f"拦住 {label}", True)

    # 字段值里出现关键字不该被误杀
    try:
        got = guard.check_sql("SELECT * FROM t WHERE note = 'drop table now'")
        check("字符串字面量里的关键字不误判", "drop table now" in got, got)
    except guard.GuardError as e:
        check("字符串字面量里的关键字不误判", False, str(e))

    # 注释里的关键字同理
    try:
        guard.check_sql("SELECT 1 -- drop table x\n")
        check("注释里的关键字不误判", True)
    except guard.GuardError as e:
        check("注释里的关键字不误判", False, str(e))

    try:
        guard.check_sql("")
        check("空 SQL 被拒", False)
    except guard.GuardError:
        check("空 SQL 被拒", True)


def test_readonly_db() -> None:
    print("只读连接")
    db = os.path.join(ROOT, "data", "demo.db")
    if not os.path.exists(db):
        print("  (跳过：未生成 data/demo.db)")
        return
    con = guard.open_readonly(db)
    try:
        con.execute("INSERT INTO applications (company) VALUES ('x')")
        check("只读连接拦住写入", False)
    except Exception:
        check("只读连接拦住写入", True)
    finally:
        con.close()


def main() -> int:
    test_time()
    test_parse()
    test_sqlgen()
    test_guard()
    test_readonly_db()
    print(f"\n共 {PASS + FAIL} 项：通过 {PASS}，失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
