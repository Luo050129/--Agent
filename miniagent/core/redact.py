"""落盘前脱敏。规范要求：记录必要且脱敏的模型交互。

三条原则：
1. 宁可可读性差，也不能把密钥写进日志
2. 脱敏在落盘前做，不在展示层做 —— 日志写下去就收不回来
3. 保留键名只抹值：`sk-***` 比 `***` 更有排查价值
"""

from __future__ import annotations

import re
from typing import Any

# (正则, 替换)：替换可以是字符串或接受 match 的函数
_RULES: list[tuple[re.Pattern[str], Any]] = [
    # 各类 API Key
    (re.compile(r"\b(sk|xai|ghp|gho|ghs|glpat|hf)[-_][A-Za-z0-9_-]{12,}"), lambda m: f"{m.group(1)}-***"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "AIza***"),
    # Bearer / Basic
    (re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.I), lambda m: f"{m.group(1)} ***"),
    # key=value 凭据
    (
        re.compile(
            r"\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|authorization)"
            r'\s*[:=]\s*["\']?([^\s"\',;}]{4,})',
            re.I,
        ),
        lambda m: f"{m.group(1)}=***",
    ),
    # 路径里的用户名（lambda 返回字面值：re.sub 的替换串会把 \\U 当转义）
    (re.compile(r"[A-Za-z]:\\Users\\[^\\/\s\"']+"), lambda m: "C:\\Users\\***"),
    (re.compile(r"/Users/[^/\s\"']+"), "/Users/***"),
    (re.compile(r"/home/[^/\s\"']+"), "/home/***"),
    # 邮箱
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"), "***@***"),
]


def redact(text: str) -> str:
    """抹掉敏感片段。"""
    if not text:
        return text
    out = text
    for pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)
    return out


def redact_deep(value: Any) -> Any:
    """递归脱敏（记录事件用）。"""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_deep(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_deep(item) for key, item in value.items()}
    return value


def summarize(text: str, limit: int = 200) -> str:
    """摘要：留前段 + 总长度，避免长输出撑爆日志。"""
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= limit:
        return flat
    return f"{flat[:limit]}…（共 {len(text)} 字符）"
