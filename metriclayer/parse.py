"""自然语言 → QuerySpec。

这一层刻意做成**规则**而不是模型：问句里出现的指标名、维度名、状态值都是
封闭词表，用词典命中就够了，而且结果可预测、可测试。模型放在可选位置
（见 llm.py），只在她处理不了的复杂句上兜底。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field as dc_field

from .metrics import Layer, Metric

__all__ = ["QuerySpec", "ParseError", "parse"]


class ParseError(ValueError):
    """问句无法解析成受支持的查询。"""


@dataclass
class QuerySpec:
    metric: Metric
    question: str
    group: str | None = None            # dimension key
    filters: list[tuple[str, str]] = dc_field(default_factory=list)  # (说明, SQL)
    order_desc: bool | None = None
    limit: int | None = None
    time_sql: str = ""
    time_label: str = "全部时间"
    hits: dict[str, str] = dc_field(default_factory=dict)

    def describe(self) -> str:
        bits = [f"指标={self.metric.name}"]
        if self.group:
            bits.append(f"分组={self.group}")
        if self.filters:
            bits.append("过滤=" + "；".join(d for d, _ in self.filters))
        if self.time_sql:
            bits.append(f"时间={self.time_label}")
        if self.order_desc is not None:
            bits.append("排序=" + ("降序" if self.order_desc else "升序"))
        if self.limit:
            bits.append(f"前 {self.limit}")
        return " · ".join(bits)


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s)


def _month_range(today: dt.date, offset: int = 0) -> tuple[dt.date, dt.date]:
    y, m = today.year, today.month + offset
    while m <= 0:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    start = dt.date(y, m, 1)
    end = dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return start, end


def _frag(col: str, start: dt.date, end: dt.date) -> str:
    return f"{col} >= '{start.isoformat()}' AND {col} < '{end.isoformat()}'"


def _parse_time(q: str, layer: Layer, today: dt.date) -> tuple[str, str]:
    """返回 (SQL 片段, 人读标签)；无时间词时返回空串与「全部时间」。"""
    col = layer.time_field

    if m := re.search(r"(?:近|前|过去)\s*(\d+|[一二两三四五六七八九十])\s*天", q):
        n = _cn_int(m.group(1)) or 7
        start = today - dt.timedelta(days=n)
        return f"{col} >= '{start.isoformat()}'", f"近 {n} 天"

    if re.search(r"近一周|本周内|这一周", q):
        start = today - dt.timedelta(days=7)
        return f"{col} >= '{start.isoformat()}'", "近 7 天"

    if re.search(r"本月|这个月|当月", q):
        s, e = _month_range(today)
        return _frag(col, s, e), f"{s.year} 年 {s.month} 月"

    if re.search(r"上月|上个月", q):
        s, e = _month_range(today, -1)
        return _frag(col, s, e), f"{s.year} 年 {s.month} 月"

    if re.search(r"今年|本年度", q):
        s, e = dt.date(today.year, 1, 1), dt.date(today.year + 1, 1, 1)
        return _frag(col, s, e), f"{today.year} 年"

    if re.search(r"去年|上一年", q):
        s, e = dt.date(today.year - 1, 1, 1), dt.date(today.year, 1, 1)
        return _frag(col, s, e), f"{today.year - 1} 年"

    if m := re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", q):
        y, mo = int(m.group(1)), int(m.group(2))
        s = dt.date(y, mo, 1)
        e = dt.date(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
        return _frag(col, s, e), f"{y} 年 {mo} 月"

    if m := re.search(r"(\d{4})-(\d{2})(?!-)", q):
        y, mo = int(m.group(1)), int(m.group(2))
        s = dt.date(y, mo, 1)
        e = dt.date(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
        return _frag(col, s, e), f"{y} 年 {mo} 月"

    return "", "全部时间"


def _match_metric(q: str, layer: Layer) -> Metric:
    best: tuple[int, Metric] | None = None
    for m in layer.metrics:
        for a in m.aliases:
            if a in q and (best is None or len(a) > best[0]):
                best = (len(a), m)
    if best is None:
        names = "、".join(m.name for m in layer.metrics)
        raise ParseError(f"没认出指标。口径层里已定义：{names}")
    return best[1]


def _match_group(q: str, layer: Layer) -> str | None:
    best: tuple[int, str] | None = None
    for d in layer.dimensions:
        for a in d.aliases:
            if a in q and (best is None or len(a) > best[0]):
                best = (len(a), d.key)
    return best[1] if best else None


def parse(question: str, layer: Layer, today: dt.date | None = None) -> QuerySpec:
    q = question.strip()
    if not q:
        raise ParseError("问句为空")
    today = today or dt.date.today()

    metric = _match_metric(q, layer)

    # 分组维度：显式点名的优先，其次取 default_true 的维度
    group = _match_group(q, layer)
    if group is None:
        for d in layer.dimensions:
            if d.default_true and re.search(r"趋势|变化|走势|分布|按月|各月", q):
                group = d.key
                break

    spec = QuerySpec(metric=metric, question=q, group=group)
    spec.hits["metric"] = metric.name

    # 时间
    spec.time_sql, spec.time_label = _parse_time(q, layer, today)

    # 枚举过滤：问句里出现状态值就加条件（与分组维度无关）
    for ename, edef in layer.enums.items():
        for v in edef.values:
            if v in q:
                spec.filters.append((f"{ename}={v}", f"{edef.column} = '{v}'"))
                spec.hits[ename] = v
                break

    # 排序与限额
    if re.search(r"最多|最高|最大|排名|排行", q):
        spec.order_desc = True
    elif re.search(r"最少|最低|最小", q):
        spec.order_desc = False

    # 限额：必须带单位，或出现在句尾。「前30天」不能被当成「前 30 条」。
    if m := re.search(r"前\s*(\d+|[一二两三四五六七八九十])\s*(?:名|个|家|位|条)", q):
        spec.limit = _cn_int(m.group(1))
    elif m := re.search(r"前\s*(\d+|[一二两三四五六七八九十])\s*$", q):
        spec.limit = _cn_int(m.group(1))
    elif m := re.search(r"(?:top|Top|TOP)\s*(\d+)", q):
        spec.limit = int(m.group(1))

    if spec.limit and spec.order_desc is None:
        spec.order_desc = True

    return spec
