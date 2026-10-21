"""可选的本地 LLM 后端（Ollama）。

**默认关闭，而且不建议当成主路径。**

原因写在这里而不是藏在文档角落：3B 级模型在 CPU 上生成 SQL 的准确率不足以
承担正确性。它适合处理规则层覆盖不到的复杂句（多条件嵌套、跨表聚合），
但每一条输出都必须过 guard 校验并真跑一次，失败就回到规则层——
把「能不能查对」这件事押在模型上，是这个工具最不该做的设计。

用法：
    python nlq.py --db data/demo.db --llm qwen2.5:3b "近30天各城市投递数前3"
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

__all__ = ["available", "generate_sql", "LLMError"]


class LLMError(RuntimeError):
    pass


def available(endpoint: str = "http://127.0.0.1:11434") -> bool:
    try:
        with urllib.request.urlopen(endpoint + "/api/tags", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _extract_sql(text: str) -> str:
    """模型经常把 SQL 包在 ``` 里或加前缀说明，这里只取第一条语句。"""
    m = re.search(r"```(?:sql)?\s*(.+?)```", text, re.S | re.I)
    if m:
        text = m.group(1)
    m = re.search(r"\b(select|with)\b.*", text, re.S | re.I)
    if not m:
        raise LLMError("模型输出里没有 SELECT 语句")
    sql = m.group(0).strip()
    sql = sql.split(";")[0].strip()
    return sql


def generate_sql(
    prompt: str,
    model: str = "qwen2.5:3b",
    endpoint: str = "http://127.0.0.1:11434",
    timeout: int = 120,
) -> str:
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 320},
    }).encode("utf-8")
    req = urllib.request.Request(
        endpoint + "/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"Ollama 返回 {e.code}：{e.reason}") from e
    except Exception as e:
        raise LLMError(f"调用 Ollama 失败：{type(e).__name__}: {e}") from e

    return _extract_sql(payload.get("response", ""))
