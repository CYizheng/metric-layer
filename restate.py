#!/usr/bin/env python
"""口径重述 —— 改了口径之后，历史数字按新旧两版各是多少、差落在哪里。

用法：
    python restate.py -d data/demo.db --versions applications
    python restate.py -d data/demo.db --metric applications
    python restate.py -d data/demo.db --metric applications --dimension city
    python restate.py -d data/demo.db --metric interviews --from 1 --to 2
    python restate.py -d data/demo.db --metric applications --from 2026-08-01

退出码：0 成功；1 参数或口径错误；2 数据错误。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metriclayer import guard, render
from metriclayer.metrics import load_layer
from metriclayer.restate import restate as run_restate

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_METRICS = os.path.join(HERE, "metrics", "ledger.example.yaml")


def _signed(v: float) -> str:
    if abs(v) < 1e-12:
        return "0"
    if float(v).is_integer():
        return f"{int(v):+d}"
    return f"{v:+.2f}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.1f}%"


def _coerce(s):
    """版本号传进来是字符串，转成 int 才能走 version_by_number；日期原样传。"""
    if s is None:
        return None
    s = str(s).strip()
    return int(s) if s.isdigit() else s


def cmd_versions(layer, key: str) -> int:
    metric = layer.metric(key)
    if metric is None:
        print(f"口径层里没有指标 {key!r}；现有：" + "、".join(m.key for m in layer.metrics))
        return 1

    print(f"\n  {metric.name}（{metric.key}）的口径版本\n")
    rows = []
    for v in metric.versions():
        rows.append(
            (
                f"v{v.version}",
                v.effective_from or "—",
                "当前" if v.version == metric.version else "",
                v.expr,
            )
        )
    print(render.render_table(["版本", "生效日", "", "表达式"], rows))

    for v in metric.versions():
        if v.note or v.reason:
            print(f"\n  v{v.version}")
            if v.note:
                print(f"    定义：{v.note}")
            if v.reason:
                print(f"    理由：{v.reason}")
            if v.approved_by:
                print(f"    确认：{v.approved_by}")

    if len(metric.versions()) > 1:
        print(f"\n  对账：python restate.py -d <db> --metric {metric.key} --dimension <维度>")
    return 0


def cmd_restate(con, layer, args) -> int:
    before = _coerce(args.from_v)
    after = _coerce(args.to_v)

    rep = run_restate(
        con,
        layer,
        args.metric,
        before=before,
        after=after,
        dimension=args.dimension,
        data_cutoff=args.cutoff,
        limit=args.max_rows,
    )

    print()
    print("  " + rep.explain())
    if rep.data_cutoff:
        print(
            f"  数据截止 {rep.data_cutoff}：两版跑在同一批数据上，"
            "差额只来自口径，不含这期间新补录的记录"
        )

    if args.dimension:
        dim = layer.dimension(args.dimension)
        print()
        rows = [
            (r.key or "（空）", r.before, r.after, _signed(r.delta), _pct(r.pct))
            for r in rep.rows
        ]
        rows.append(
            ("合计", rep.total.before, rep.total.after,
             _signed(rep.total.delta), _pct(rep.total.pct))
        )
        print(
            render.render_table(
                [dim.name, f"v{rep.before.version}", f"v{rep.after.version}",
                 "差额", "变化率"],
                rows,
                [False, True, True, True, True],
            )
        )
        print("\n  差额为负＝新口径算得比旧口径少。按差额绝对值排序。")
    else:
        print()

    if args.sql:
        print("\n  ── v%d ──\n" % rep.before.version)
        print(rep.sql_before)
        print("\n  ── v%d ──\n" % rep.after.version)
        print(rep.sql_after)

    if not rep.moved:
        print("\n  这次变更没有影响任何数字。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="口径重述：同一个指标在两套口径下差多少",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("-d", "--db", required=True, help="SQLite 文件路径")
    ap.add_argument("-m", "--metrics", default=DEFAULT_METRICS, help="口径层 yaml")
    ap.add_argument("--metric", help="指标 key")
    ap.add_argument("--versions", metavar="METRIC", help="列该指标的全部口径版本后退出")
    ap.add_argument("--from", dest="from_v", metavar="版本或日期",
                    help="旧口径：版本号（1）或生效日期（2026-08-01）")
    ap.add_argument("--to", dest="to_v", metavar="版本或日期",
                    help="新口径：版本号或日期")
    ap.add_argument("--dimension", help="按该维度拆解差额")
    ap.add_argument("--cutoff", metavar="YYYY-MM-DD",
                    help="固定数据截止日，默认取库内最晚日期")
    ap.add_argument("--sql", action="store_true", help="打印两版 SQL")
    ap.add_argument("--max-rows", type=int, default=200, help="分组行数上限，默认 200")
    args = ap.parse_args(argv)

    for p in (args.db, args.metrics):
        if not os.path.exists(p):
            print(f"文件不存在：{p}", file=sys.stderr)
            return 2

    try:
        layer = load_layer(args.metrics)
    except Exception as e:  # 口径层写错时给出原文，不吞掉
        print(f"口径层加载失败：{e}", file=sys.stderr)
        return 1

    if args.versions:
        return cmd_versions(layer, args.versions)

    if not args.metric:
        ap.print_help()
        return 1

    try:
        con = guard.open_readonly(args.db)
    except (OSError, sqlite3.Error) as e:
        print(f"打不开数据库：{e}", file=sys.stderr)
        return 2

    try:
        return cmd_restate(con, layer, args)
    except (ValueError, guard.GuardError) as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    except sqlite3.Error as e:
        print(f"查询失败：{e}", file=sys.stderr)
        return 2
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
