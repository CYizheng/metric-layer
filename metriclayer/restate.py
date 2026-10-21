"""口径重述（restatement）：同一个指标在两套口径下差多少，差额落在哪些维度上。

为什么需要这个东西：口径一改，历史数字就断了。报表上 8 月是 114，但 8 月
当时报的是 128——如果不把"按旧口径应该是多少"算出来，就会变成有人记得 128、
有人记得 114，谁都对，也都无法对账。

关键设计：**固定数据截止日**。两套口径必须跑在同一批数据上，否则差额里
混进了"这期间新补录了几条"，那不是口径差异，是数据修订。这两件事必须分开，
分开才能各自归因。
"""

from __future__ import annotations

from dataclasses import dataclass

from .guard import run
from .metrics import Layer, Metric, MetricVersion
from .parse import QuerySpec
from .sqlgen import build_sql

__all__ = ["RestateRow", "RestateReport", "restate", "data_cutoff_of"]


@dataclass
class RestateRow:
    key: str | None
    before: float
    after: float

    @property
    def delta(self) -> float:
        return self.after - self.before

    @property
    def pct(self) -> float | None:
        """相对变化。基期为 0 时无定义，返回 None，不返回 inf。"""
        if not self.before:
            return None
        return (self.after - self.before) / self.before


@dataclass
class RestateReport:
    metric: Metric
    before: MetricVersion
    after: MetricVersion
    dimension: str | None
    data_cutoff: str | None
    total: RestateRow
    rows: list[RestateRow]
    sql_before: str = ""
    sql_after: str = ""

    @property
    def moved(self) -> bool:
        return abs(self.total.delta) > 1e-12

    def explain(self) -> str:
        """一句话说清这次重述的影响，给报告抬头用。"""
        t = self.total
        sign = "+" if t.delta > 0 else ""
        if not self.moved:
            return f"{self.metric.name}：v{self.before.version} → v{self.after.version}，数值无变化"
        pct = "—" if t.pct is None else f"{t.pct * 100:+.1f}%"
        return (
            f"{self.metric.name}：v{self.before.version} → v{self.after.version}，"
            f"{_fmt(t.before)} → {_fmt(t.after)}（{sign}{_fmt(t.delta)}，{pct}）"
        )


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}"


def _num(v) -> float:
    # SUM(...) 在空集上返回 NULL，不是 0；不显式转成 0 会让后续差额变成 nan
    return 0.0 if v is None else float(v)


def data_cutoff_of(conn, layer: Layer) -> str | None:
    """数据里最晚的日期。默认拿它当截止日，保证两次查询看的是同一批数据。

    time_field 通常带表别名（a.applied_at），所以 FROM 必须补上别名，
    否则 SQLite 会报 no such column。
    """
    _, rows, _, _ = run(
        conn,
        f"SELECT MAX({layer.time_field})"
        f" FROM {layer.main_table} {layer.main_alias}",
        max_rows=1,
    )
    return rows[0][0] if rows and rows[0][0] else None


def _query(conn, layer: Layer, metric: Metric, version: MetricVersion,
           dimension: str | None, data_cutoff: str | None, limit: int):
    spec = QuerySpec(metric=metric, question="（重述）")
    spec.group = dimension
    spec.limit = limit
    extra = []
    if data_cutoff:
        extra.append(f"{layer.time_field} <= '{data_cutoff}'")
    sql = build_sql(
        layer, spec, limit, metric_expr=version.expr, extra_conds=extra
    )
    _, rows, _, _ = run(conn, sql, max_rows=limit + 1)
    return sql, rows


def _as_map(rows, grouped: bool) -> dict[str, float]:
    if not grouped:
        return {"": _num(rows[0][0]) if rows else 0.0}
    out: dict[str, float] = {}
    for r in rows:
        key = "" if r[0] is None else str(r[0])
        out[key] = _num(r[1])
    return out


def _pick_version(metric: Metric, ref, default: MetricVersion) -> MetricVersion:
    if ref is None:
        return default
    if isinstance(ref, int):
        return metric.version_by_number(ref)
    return metric.version_at(str(ref))


def restate(
    conn,
    layer: Layer,
    metric_key: str,
    *,
    before=None,
    after=None,
    dimension: str | None = None,
    data_cutoff: str | None = None,
    limit: int = 500,
) -> RestateReport:
    """对比指标的两个口径版本。

    before / after 可以是版本号（int）或日期（str，按当日生效版本取）。
    省略 before 取最早版本，省略 after 取最新版本。
    """
    metric = layer.metric(metric_key)
    if metric is None:
        raise ValueError(
            f"口径层里没有指标 {metric_key!r}；现有："
            + "、".join(m.key for m in layer.metrics)
        )

    versions = metric.versions()
    before_v = _pick_version(metric, before, versions[0])
    after_v = _pick_version(metric, after, versions[-1])

    if dimension is not None and layer.dimension(dimension) is None:
        raise ValueError(
            f"口径层里没有维度 {dimension!r}；现有："
            + "、".join(d.key for d in layer.dimensions)
        )

    # 没指定截止日就取数据最晚日期——两套口径必须看同一批数据，
    # 否则差额里混着「这期间新补录了几条」，归因就错了。
    cutoff = data_cutoff if data_cutoff is not None else data_cutoff_of(conn, layer)

    sql_b, rows_b = _query(conn, layer, metric, before_v, dimension, cutoff, limit)
    sql_a, rows_a = _query(conn, layer, metric, after_v, dimension, cutoff, limit)

    # 总计单独跑一次，不用分组结果求和——分组可能被 LIMIT 截断，求和会失真
    _, tot_b = _query(conn, layer, metric, before_v, None, cutoff, limit)
    _, tot_a = _query(conn, layer, metric, after_v, None, cutoff, limit)
    total = RestateRow(
        None,
        _num(tot_b[0][0]) if tot_b else 0.0,
        _num(tot_a[0][0]) if tot_a else 0.0,
    )

    rows: list[RestateRow] = []
    if dimension is not None:
        mb = _as_map(rows_b, True)
        ma = _as_map(rows_a, True)
        for key in sorted(set(mb) | set(ma)):
            rows.append(RestateRow(key, mb.get(key, 0.0), ma.get(key, 0.0)))
        # 差额大的排前面：重述报告要先回答「哪里变了」
        rows.sort(key=lambda r: abs(r.delta), reverse=True)

    return RestateReport(
        metric=metric,
        before=before_v,
        after=after_v,
        dimension=dimension,
        data_cutoff=cutoff,
        total=total,
        rows=rows,
        sql_before=sql_b,
        sql_after=sql_a,
    )
