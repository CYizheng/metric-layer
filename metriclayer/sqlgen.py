"""QuerySpec → SQL。只从声明里拼装片段，不拼接用户输入的任何原始文本。"""

from __future__ import annotations

from .metrics import Layer
from .parse import QuerySpec

__all__ = ["build_sql", "build_prompt"]


def _from_clause(layer: Layer, spec: QuerySpec) -> str:
    base = f"FROM {layer.main_table} {layer.main_alias}"
    if spec.metric.needs_events:
        if not layer.needs_events:
            raise ValueError(
                f"指标「{spec.metric.name}」需要事件表，但口径层没有声明 tables.events"
            )
        base += (
            f"\nLEFT JOIN {layer.events_table} {layer.events_alias}"
            f" ON {layer.events_on}"
        )
    return base


def _where(layer: Layer, spec: QuerySpec) -> str:
    conds: list[str] = []
    if spec.time_sql:
        conds.append(spec.time_sql)
    conds += [sql for _, sql in spec.filters]
    if spec.metric.needs_events and spec.metric.event_filter:
        conds.append(spec.metric.event_filter)
    if not conds:
        return ""
    return "WHERE " + "\n  AND ".join(conds)


def build_sql(layer: Layer, spec: QuerySpec, default_limit: int = 200) -> str:
    a = layer.main_alias
    metric_sql = spec.metric.expr

    if spec.group:
        dim = layer.dimension(spec.group)
        if dim is None:
            raise ValueError(f"未知维度：{spec.group}")
        select = (
            f'SELECT {dim.column} AS "{dim.name}",\n'
            f'       {metric_sql} AS "{spec.metric.name}"'
        )
        group_by = f"GROUP BY {dim.column}"

        # 排序默认值：时间维度按自身升序（看趋势），其余按指标降序（看大头）
        if dim.time_grain:
            order = "ORDER BY 1 ASC"
        elif spec.order_desc is None:
            order = "ORDER BY 2 DESC"
        else:
            order = "ORDER BY 2 " + ("DESC" if spec.order_desc else "ASC")

        limit = spec.limit or default_limit
        parts = [select, _from_clause(layer, spec), _where(layer, spec),
                 group_by, order, f"LIMIT {limit}"]
    else:
        select = f'SELECT {metric_sql} AS "{spec.metric.name}"'
        parts = [select, _from_clause(layer, spec), _where(layer, spec), "LIMIT 1"]

    return "\n".join(p for p in parts if p)


def build_prompt(layer: Layer, question: str, schema_sql: str) -> str:
    """给可选 LLM 后端用的提示词。把口径层的定义完整交给模型，减少自由发挥。"""
    lines = [
        "你是 SQLite 查询生成器。只输出一条 SELECT 语句，不要解释、不要 Markdown 代码块。",
        "",
        "数据库结构：",
        schema_sql.strip(),
        "",
        "口径定义（必须严格使用这里的表达式，不要自己另造口径）：",
    ]
    for m in layer.metrics:
        g = f"（需要 JOIN events，附加条件 {m.event_filter}）" if m.needs_events else ""
        lines.append(f"- {m.name} = {m.expr} {g}")
    lines.append("")
    lines.append("时间字段：" + layer.time_field)
    if layer.events_table:
        lines.append(f"事件表：{layer.events_table} {layer.events_alias}，关联条件 {layer.events_on}")
    lines.append("")
    lines.append("问句：" + question)
    lines.append("")
    lines.append("要求：只读；必须带 LIMIT；不要使用 ATTACH / PRAGMA / 写操作。")
    return "\n".join(lines)
