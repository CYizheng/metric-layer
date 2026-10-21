"""指标口径层的加载：把 yaml 里的声明变成可查询的对象。"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

__all__ = ["MetricVersion", "Metric", "Dimension", "Layer", "load_layer"]


@dataclass
class MetricVersion:
    """指标的一个口径版本。

    口径一旦对外用过，`expr` 就不能原地改——历史数字是按旧口径算的，
    原地改等于让过去的数据偷偷换了含义。新增一条版本记录，旧版本留着，
    才能回答「按当时的口径，这个月应该是多少」。
    """

    version: int
    expr: str
    effective_from: str = ""     # ISO 日期；空表示不参与按日期回查
    note: str = ""
    reason: str = ""             # 为什么要改，以及被否掉的替代方案
    approved_by: str = ""

    def describe(self) -> str:
        when = self.effective_from or "未标注生效日"
        return f"v{self.version}（自 {when}）"


@dataclass
class Metric:
    key: str
    name: str
    expr: str
    aliases: list[str]
    unit: str = ""
    needs_events: bool = False
    event_filter: str = ""
    note: str = ""
    version: int = 1
    history: list[MetricVersion] = dc_field(default_factory=list)

    def versions(self) -> list[MetricVersion]:
        """全部版本，按版本号升序。没写 history 时把当前定义当作唯一版本。"""
        if self.history:
            return sorted(self.history, key=lambda v: v.version)
        return [MetricVersion(version=self.version, expr=self.expr, note=self.note)]

    def version_at(self, asof: str) -> MetricVersion:
        """返回 asof（含当天）时生效的版本。

        早于首个版本的生效日时退回首个版本——那时还没有别的口径可用，
        退回比报错更接近事实。
        """
        picked = self.versions()[0]
        for v in self.versions():
            if v.effective_from and v.effective_from <= asof:
                picked = v
        return picked

    def version_by_number(self, n: int) -> MetricVersion:
        for v in self.versions():
            if v.version == n:
                return v
        raise ValueError(
            f"指标「{self.name}」没有 v{n}；现有 "
            + "、".join("v%d" % v.version for v in self.versions())
        )


@dataclass
class Dimension:
    key: str
    name: str
    column: str
    aliases: list[str]
    default_true: bool = False
    time_grain: bool = False   # 时间粒度维度：按维度升序排，不按指标降序


@dataclass
class EnumDef:
    name: str
    column: str
    values: list[str]


@dataclass
class Layer:
    name: str
    main_table: str
    main_alias: str
    time_field: str
    time_format: str
    metrics: list[Metric]
    dimensions: list[Dimension]
    enums: dict[str, EnumDef]
    labels: dict[str, str]
    events_table: str | None = None
    events_alias: str | None = None
    events_on: str | None = None

    def metric(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)

    def dimension(self, key: str) -> Dimension | None:
        return next((d for d in self.dimensions if d.key == key), None)

    @property
    def needs_events(self) -> bool:
        return self.events_table is not None


def _need(d: dict, k: str, where: str):
    if k not in d:
        raise ValueError(f"{where} 缺少必填项 {k}")
    return d[k]


def _norm_expr(s) -> str:
    return " ".join(str(s).split())


def _is_iso_date(s: str) -> bool:
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return False
    import datetime as _dt

    try:
        _dt.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _parse_history(raw, where: str) -> list[MetricVersion]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{where}.history 必须是列表")
    out: list[MetricVersion] = []
    for j, h in enumerate(raw):
        w = f"{where}.history[{j}]"
        if not isinstance(h, dict):
            raise ValueError(f"{w} 必须是映射")
        ef = str(h.get("effective_from") or "")
        if not ef:
            raise ValueError(f"{w} 缺少 effective_from；没有生效日就无法按日期回查")
        if not _is_iso_date(ef):
            raise ValueError(f"{w}.effective_from 必须是 YYYY-MM-DD，实际 {ef!r}")
        out.append(
            MetricVersion(
                version=int(_need(h, "version", w)),
                expr=str(_need(h, "expr", w)),
                effective_from=ef,
                note=h.get("note", ""),
                reason=h.get("reason", ""),
                approved_by=h.get("approved_by", ""),
            )
        )
    _validate_history(out, where)
    return out


def _validate_history(history: list[MetricVersion], where: str) -> None:
    nums = sorted(v.version for v in history)
    if len(set(nums)) != len(nums):
        raise ValueError(f"{where}.history 版本号重复：{nums}")
    if nums != list(range(1, len(nums) + 1)):
        raise ValueError(f"{where}.history 版本号必须从 1 连续递增，实际 {nums}")
    seq = [v.effective_from for v in sorted(history, key=lambda v: v.version)]
    if seq != sorted(seq):
        raise ValueError(
            f"{where}.history 的 effective_from 必须随版本号递增，实际 {seq}"
        )


def _check_current_matches_history(metric: Metric, where: str) -> None:
    """当前 expr 必须等于 history 最后一版。

    不相等说明改了 expr 却没记版本——那样过去按旧口径算出的数字会被
    悄悄改写，正是这个模块要防的事，所以直接拒绝加载，不放过。
    """
    if not metric.history:
        return
    last = sorted(metric.history, key=lambda v: v.version)[-1]
    if _norm_expr(last.expr) != _norm_expr(metric.expr):
        raise ValueError(
            f"{where} 的 expr 与 history 最后一版 v{last.version} 不一致；"
            "改了口径请新增一条 history，不要直接改 expr"
        )
    if metric.version != last.version:
        raise ValueError(
            f"{where} 的 version={metric.version} 与 history 最后一版 "
            f"v{last.version} 不一致"
        )


def load_layer(path: str) -> Layer:
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"{path} 顶层必须是映射")

    tables = raw.get("tables") or {}
    ev = tables.get("events") or {}
    time_cfg = raw.get("time") or {}

    metrics: list[Metric] = []
    for i, m in enumerate(raw.get("metrics") or []):
        where = f"metrics[{i}]"
        aliases = m.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        metric = Metric(
            key=_need(m, "key", where),
            name=m.get("name", m["key"]),
            expr=_need(m, "expr", where),
            aliases=sorted(aliases, key=len, reverse=True),
            unit=m.get("unit", ""),
            needs_events=bool(m.get("needs_events", False)),
            event_filter=m.get("event_filter", ""),
            note=m.get("note", ""),
            version=int(m.get("version", 1)),
            history=_parse_history(m.get("history"), where),
        )
        _check_current_matches_history(metric, where)
        metrics.append(metric)

    dimensions: list[Dimension] = []
    for i, d in enumerate(raw.get("dimensions") or []):
        where = f"dimensions[{i}]"
        aliases = d.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        dimensions.append(
            Dimension(
                key=_need(d, "key", where),
                name=d.get("name", d["key"]),
                column=_need(d, "column", where),
                aliases=sorted(aliases, key=len, reverse=True),
                default_true=bool(d.get("default_true", False)),
                time_grain=bool(d.get("time_grain", False)),
            )
        )

    enums: dict[str, EnumDef] = {}
    for name, cfg in (raw.get("enums") or {}).items():
        enums[name] = EnumDef(
            name=name,
            column=_need(cfg, "column", f"enums.{name}"),
            values=list(cfg.get("values") or []),
        )

    events_table = ev.get("table")
    events_alias = ev.get("alias", "e")
    # 同时接受 join_on 与 on：YAML 1.1 会把裸 on 解析成布尔 True，
    # 所以文档里统一写 join_on，这里对旧的 on 也做一次兜底。
    events_on = ev.get("join_on") or (ev.get(True) if True in ev else None)
    if events_table and not events_on:
        raise ValueError(
            "tables.events 声明了事件表但缺少 join_on；"
            "注意不要写成 on（YAML 会把它当布尔值）"
        )

    return Layer(
        name=raw.get("name", "未命名口径层"),
        main_table=_need(tables, "main", "tables"),
        main_alias=tables.get("main_alias", "a"),
        time_field=_need(time_cfg, "field", "time"),
        time_format=time_cfg.get("format", "%Y-%m-%d"),
        metrics=metrics,
        dimensions=dimensions,
        enums=enums,
        labels=raw.get("labels") or {},
        events_table=events_table,
        events_alias=events_alias,
        events_on=events_on,
    )
