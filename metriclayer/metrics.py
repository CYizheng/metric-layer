"""指标口径层的加载：把 yaml 里的声明变成可查询的对象。"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

__all__ = ["Metric", "Dimension", "Layer", "load_layer"]


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
        metrics.append(
            Metric(
                key=_need(m, "key", where),
                name=m.get("name", m["key"]),
                expr=_need(m, "expr", where),
                aliases=sorted(aliases, key=len, reverse=True),
                unit=m.get("unit", ""),
                needs_events=bool(m.get("needs_events", False)),
                event_filter=m.get("event_filter", ""),
                note=m.get("note", ""),
            )
        )

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
