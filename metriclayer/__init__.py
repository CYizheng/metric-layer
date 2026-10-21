"""metric-layer —— 指标口径层与自然语言取数。"""

from .guard import GuardError, check_sql, open_readonly, run
from .metrics import Dimension, Layer, Metric, load_layer
from .parse import ParseError, QuerySpec, parse
from .sqlgen import build_sql

__version__ = "0.1.0"

__all__ = [
    "Dimension",
    "GuardError",
    "Layer",
    "Metric",
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
