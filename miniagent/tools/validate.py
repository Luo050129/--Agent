"""最小 JSON Schema 校验（零依赖）。规范第 3 条：执行参数必须校验。

只支持工具参数需要的子集：type / properties / required / enum /
minimum / maximum / minLength / maxLength / pattern / items。

只返回错误列表，不抛异常 —— 调用方要把错误文本回灌给模型。
"""

from __future__ import annotations

import re
from typing import Any


def _type_ok(want: str, value: Any) -> bool:
    if want == "string":
        return isinstance(value, str)
    if want == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if want == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if want == "boolean":
        return isinstance(value, bool)
    if want == "array":
        return isinstance(value, list)
    if want == "object":
        return isinstance(value, dict)
    return True


def _check_value(key: str, spec: dict[str, Any], value: Any, errors: list[str]) -> None:
    want = spec.get("type")
    if want and not _type_ok(want, value):
        errors.append(f"{key} 应为 {want}，收到 {type(value).__name__}")
        return

    enum = spec.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{key} 只能是 {' / '.join(repr(item) for item in enum)}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = spec.get("minimum")
        maximum = spec.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{key} 不能小于 {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{key} 不能大于 {maximum}")

    if isinstance(value, str):
        min_len = spec.get("minLength")
        max_len = spec.get("maxLength")
        if isinstance(max_len, int) and len(value) > max_len:
            errors.append(f"{key} 太长（{len(value)} > {max_len}）")
        if isinstance(min_len, int) and len(value) < min_len:
            errors.append(f"{key} 太短（{len(value)} < {min_len}）")
        pattern = spec.get("pattern")
        if isinstance(pattern, str):
            try:
                if re.search(pattern, value) is None:
                    errors.append(f"{key} 不符合格式要求")
            except re.error:
                pass

    items = spec.get("items")
    if isinstance(value, list) and isinstance(items, dict):
        for index, item in enumerate(value):
            _check_value(f"{key}[{index}]", items, item, errors)


def validate_args(schema: dict[str, Any] | None, args: dict[str, Any]) -> list[str]:
    """返回错误列表，空表示通过。"""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    properties = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if args.get(key) is None:
            errors.append(f"缺少必填参数：{key}")

    for key, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        value = args.get(key)
        if value is None:
            continue
        _check_value(key, spec, value, errors)

    return errors


def args_schema_of(tool: dict[str, Any]) -> dict[str, Any] | None:
    """从工具定义里取参数 schema。"""
    fn = tool.get("function") if isinstance(tool, dict) else None
    if isinstance(fn, dict):
        params = fn.get("parameters")
        return params if isinstance(params, dict) else None
    params = tool.get("parameters") if isinstance(tool, dict) else None
    return params if isinstance(params, dict) else None


def timeout_of(tool: dict[str, Any]) -> int | None:
    """工具级超时覆盖（秒）。"""
    schema = args_schema_of(tool) or {}
    value = schema.get("x-timeout-s")
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def idempotent_of(tool: dict[str, Any]) -> bool:
    """是否幂等（可安全重试）。没标就当作不幂等。"""
    schema = args_schema_of(tool) or {}
    return schema.get("x-idempotent") is True
