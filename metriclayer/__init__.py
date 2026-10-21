"""metric-layer —— 指标口径层与自然语言取数。"""

from .guard import GuardError, check_sql, open_readonly, run
from .metrics import Dimension, Layer, Metric, MetricVersion, load_layer
from .parse import ParseError, QuerySpec, parse
from .sqlgen import build_sql

# 注意：不在这里导出 restate 函数——包级名字会和 metriclayer.restate 子模块撞车，
# 撞了之后 metriclayer.restate 指向函数而不是模块，很难查。用的时候显式写
# `from metriclayer.restate import restate`。

__version__ = "0.2.0"

__all__ = [
    "Dimension",
    "GuardError",
    "Layer",
    "Metric",
    "MetricVersion",
    "ParseError",
    "QuerySpec",
    "build_sql",
    "check_sql",
    "load_layer",
    "open_readonly",
    "parse",
    "run",
    "__version__",
]
