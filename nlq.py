#!/usr/bin/env python
"""自然语言取数 —— 用指标口径层把「问一句」变成「一条可审阅的 SQL」。

用法：
    python nlq.py --db data/demo.db "本月各城市投递数"
    python nlq.py --db data/demo.db "近30天面试场次按月"
    python nlq.py --db data/demo.db --sql "投递最多的前3个城市"
    python nlq.py --db data/demo.db --list-metrics
    python nlq.py --db data/demo.db --schema

退出码：0 成功；1 解析或校验失败；2 参数/数据错误。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metriclayer import guard, llm, render, schema, sqlgen
from metriclayer.metrics import load_layer
from metriclayer.parse import ParseError, QuerySpec, parse

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_METRICS = os.path.join(HERE, "metrics", "ledger.example.yaml")


def _print_rows(cols, rows, ms, truncated):
    numeric = []
    for i in range(len(cols)):
        vals = [r[i] for r in rows if r[i] is not None]
        numeric.append(bool(vals) and all(isinstance(v, (int, float)) for v in vals))
    print(render.render_table(cols, rows, numeric))
    tail = f"{len(rows)} 行 · {ms:.1f} ms"
    if truncated:
        tail += " · 结果已截断，用 --max-rows 放大"
    print(f"\n  {tail}")


def _show_spec(layer, spec: QuerySpec) -> None:
    print(f"  解析：{spec.describe()}")
    if spec.metric.note:
        print(f"  口径：{spec.metric.note}")


def _rule_path(layer, con, question, args) -> int:
    spec = parse(question, layer, today=args.today_date)
    sql = sqlgen.build_sql(layer, spec, default_limit=args.max_rows)
    _show_spec(layer, spec)
    if args.sql:
        print("\n" + "\n".join("  " + ln for ln in sql.splitlines()))
    try:
        cols, rows, ms, truncated = guard.run(con, sql, max_rows=args.max_rows)
    except guard.GuardError as e:
        print(f"\n  守卫拒绝执行：{e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n  执行失败：{type(e).__name__}: {e}", file=sys.stderr)
        print("  生成的 SQL 如下，可直接对照修改口径层：\n" + sql, file=sys.stderr)
        return 1
    print()
    _print_rows(cols, rows, ms, truncated)
    return 0


def _llm_path(layer, con, question, args) -> int:
    schema_sql = schema.describe(con, layer.labels)
    prompt = sqlgen.build_prompt(layer, question, schema_sql)
    last_err = ""
    for attempt in (1, 2):
        try:
            if attempt == 1:
                sql = llm.generate_sql(prompt, model=args.llm)
            else:
                sql = llm.generate_sql(
                    prompt + f"\n\n上一次生成的 SQL 执行失败：{last_err}\n请修正后只输出 SQL。",
                    model=args.llm,
                )
        except llm.LLMError as e:
            print(f"  LLM 不可用：{e}", file=sys.stderr)
            return 1

        print(f"  模型生成（第 {attempt} 次）：")
        print("\n".join("    " + ln for ln in sql.splitlines()))
        try:
            cols, rows, ms, truncated = guard.run(con, sql, max_rows=args.max_rows)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print(f"  未通过：{last_err}", file=sys.stderr)
            continue
        print()
        _print_rows(cols, rows, ms, truncated)
        return 0

    print("  两次都没能生成可执行的 SQL。规则层覆盖不到的复杂句建议拆成两句问。",
          file=sys.stderr)
    return 1


def cmd_list(layer) -> int:
    print(f"口径层：{layer.name}")
    print(f"\n指标（{len(layer.metrics)}）")
    for m in layer.metrics:
        ev = "  [需事件表]" if m.needs_events else ""
        print(f"  {m.name:<8} {m.expr}{ev}")
        print(f"           别名：{'、'.join(m.aliases[:6])}")
    print(f"\n维度（{len(layer.dimensions)}）")
    for d in layer.dimensions:
        print(f"  {d.name:<8} {d.column:<24} 别名：{'、'.join(d.aliases[:5])}")
    for name, e in layer.enums.items():
        print(f"\n枚举 {name}：{'、'.join(e.values)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="nlq", description="指标口径层驱动的自然语言取数（只读）"
    )
    ap.add_argument("-d", "--db", required=True, help="SQLite 文件路径")
    ap.add_argument("-m", "--metrics", default=DEFAULT_METRICS, help="口径层 yaml")
    ap.add_argument("question", nargs="*", help="自然语言问句")
    ap.add_argument("--sql", action="store_true", help="同时打印生成的 SQL")
    ap.add_argument("--schema", action="store_true", help="打印库结构后退出")
    ap.add_argument("--list-metrics", action="store_true", help="列出口径层内容后退出")
    ap.add_argument("--llm", metavar="MODEL", help="用本地 Ollama 模型兜底（如 qwen2.5:3b）")
    ap.add_argument("--force-llm", action="store_true", help="跳过规则层直接走模型")
    ap.add_argument("--max-rows", type=int, default=200, help="结果行数上限，默认 200")
    ap.add_argument("--today", metavar="YYYY-MM-DD", help="固定今天，便于复现")
    args = ap.parse_args(argv)

    for p in (args.db, args.metrics):
        if not os.path.exists(p):
            print(f"文件不存在：{p}", file=sys.stderr)
            return 2

    try:
        args.today_date = (
            dt.date.fromisoformat(args.today) if args.today else None
        )
    except ValueError:
        print("--today 需要 YYYY-MM-DD", file=sys.stderr)
        return 2

    try:
        layer = load_layer(args.metrics)
    except Exception as e:
        print(f"口径层加载失败：{e}", file=sys.stderr)
        return 2

    try:
        con = guard.open_readonly(args.db)
    except Exception as e:
        print(f"打不开数据库（只读模式）：{e}", file=sys.stderr)
        return 2

    try:
        if args.list_metrics:
            return cmd_list(layer)
        if args.schema:
            print(schema.describe(con, layer.labels))
            return 0

        question = " ".join(args.question).strip()
        if not question:
            ap.print_help()
            return 2

        print(f"问：{question}")
        if args.force_llm or args.llm:
            print(f"  路径：{'强制' if args.force_llm else '优先'}模型 {args.llm}")
            if args.force_llm:
                return _llm_path(layer, con, question, args)
            try:
                return _rule_path(layer, con, question, args)
            except ParseError as e:
                print(f"  规则层未命中（{e}），转模型")
                return _llm_path(layer, con, question, args)

        return _rule_path(layer, con, question, args)
    except ParseError as e:
        print(f"\n  无法解析：{e}", file=sys.stderr)
        print("  提示：先用 --list-metrics 看口径层里有哪些词。", file=sys.stderr)
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
