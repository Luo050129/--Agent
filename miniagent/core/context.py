"""核心层：上下文管理。

三件事：
  1. 估算 token（没有 tiktoken，用中文 1 字≈1 token、英文 4 字符≈1 token 的粗估）
  2. 按预算裁剪历史，被裁掉的压成一条摘要，而不是直接失忆
  3. 工具结果先截断再入库，别让一条日志把窗口撑爆

外带一个记录器：每次工具调用落一行 jsonl，作为「历史结果、记录」。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import RECORDS_DIR, context_budget
from .redact import redact, summarize
from ..tokens import estimate_text, take_prefix, take_suffix  # 单一实现，MCP 层也用它
from .guard import is_failure

_MESSAGE_OVERHEAD = 4  # 每条消息的 role/结构开销


def estimate_messages(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        total += _MESSAGE_OVERHEAD + estimate_text(str(msg.get("content") or ""))
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            total += estimate_text(str(fn.get("name") or "")) + estimate_text(str(fn.get("arguments") or ""))
    return total


@dataclass
class ContextStats:
    tokens: int = 0
    budget: int = 0
    dropped: int = 0
    summarized: bool = False

    @property
    def ratio(self) -> float:
        return min(1.0, self.tokens / self.budget) if self.budget else 0.0


class ContextManager:
    def __init__(
        self,
        budget: int | None = None,
        keep_recent: int = 6,
        tool_result_tokens: int = 750,  # 约等于过去 3000 个英文字符
    ) -> None:
        self.budget = budget or context_budget()
        self.keep_recent = keep_recent
        self.tool_result_tokens = tool_result_tokens

    # ---------------------------------------------------------- 工具结果

    def compact_tool_result(self, text: str) -> str:
        """超长的工具结果留头留尾，中间折叠。

        **按 token 卡，不按字符**：同样 3000 字符，英文约 750 token，
        中文是 3000 token。按字符卡的话，读一份中文文档要多花三倍上下文。
        """
        limit = self.tool_result_tokens
        before = estimate_text(text)
        if before <= limit:
            return text
        head = take_prefix(text, limit * 2 // 3)
        tail = take_suffix(text, limit - limit * 2 // 3)
        return f"{head}\n…（已压缩 {before} → {limit} tok，原 {len(text)} 字符）\n{tail}"

    # ---------------------------------------------------------- 裁剪历史

    def prepare(self, messages: list[dict]) -> tuple[list[dict], ContextStats]:
        """返回「要发给模型的消息」和统计。原始历史不动。"""
        if not messages:
            return [], ContextStats(budget=self.budget)

        system = messages[0] if messages[0].get("role") == "system" else None
        rest = messages[1:] if system else list(messages)

        full_tokens = estimate_messages(messages)
        if full_tokens <= self.budget:
            return list(messages), ContextStats(tokens=full_tokens, budget=self.budget)

        # 预留摘要和回复的空间；小预算时不能把预留算成负数，否则预算形同虚设
        reserve = min(max(600, self.budget // 6), max(1, self.budget // 2))
        target = max(200, self.budget - reserve)
        min_keep = min(self.keep_recent, 2)  # 预算再小也留最近两条，别把上下文清空

        kept: list[dict] = []
        used = estimate_messages([system]) if system else 0
        # 从最新往回捡，捡到装不下为止
        for msg in reversed(rest):
            cost = estimate_messages([msg])
            if len(kept) >= min_keep and used + cost > target:
                break
            kept.insert(0, msg)
            used += cost

        cut = len(rest) - len(kept)
        dropped = rest[:cut]

        # 不能留孤儿 tool 消息：开头的 tool 一定失去了它的 assistant 父消息
        while kept and kept[0].get("role") == "tool":
            dropped.append(kept.pop(0))

        def build() -> list[dict]:
            view: list[dict] = [system] if system else []
            if dropped:
                view.append({"role": "system", "content": self._summarize(dropped, summary_limit)})
            view.extend(kept)
            return view

        # 摘要也算 token，所以先给它定个上限，再核对预算
        summary_limit = max(120, self.budget - (estimate_messages([system]) if system else 0) - 40)
        out = build()
        while kept and estimate_messages(out) > self.budget:
            dropped.append(kept.pop(0))
            while kept and kept[0].get("role") == "tool":
                dropped.append(kept.pop(0))
            out = build()

        stats = ContextStats(
            tokens=estimate_messages(out),
            budget=self.budget,
            dropped=len(dropped),
            summarized=bool(dropped),
        )
        return out, stats

    def _summarize(self, dropped: list[dict], limit_chars: int | None = None) -> str:
        """把丢掉的历史压成一段摘要。纯规则，不再调模型。"""
        user_asks: list[str] = []
        tools: dict[str, int] = {}
        last_answer = ""

        for msg in dropped:
            role = msg.get("role")
            if role == "user":
                text = str(msg.get("content") or "").strip().replace("\n", " ")
                if text:
                    user_asks.append(text[:80])
            elif role == "assistant":
                for call in msg.get("tool_calls") or []:
                    name = (call.get("function") or {}).get("name") or "?"
                    tools[name] = tools.get(name, 0) + 1
                if msg.get("content"):
                    last_answer = str(msg["content"]).strip().replace("\n", " ")[:200]

        parts = [f"[早期上下文摘要] 已折叠 {len(dropped)} 条更早的消息。"]
        if user_asks:
            parts.append("用户此前的请求：" + "；".join(user_asks[-4:]))
        if tools:
            parts.append("调用过的工具：" + "、".join(f"{k}×{v}" for k, v in tools.items()))
        if last_answer:
            parts.append("当时的结论片段：" + last_answer)

        text = "\n".join(parts)
        if limit_chars and len(text) > limit_chars:
            text = text[: max(0, limit_chars - 1)] + "…"
        return text


def _code_hint(result: str) -> str:
    """从结果文本推断错误码（零侵入，不改各 handler 签名）。"""
    head = (result or "")[:300]
    if "参数校验失败" in head or "参数 JSON 解析失败" in head:
        return "invalid_args"
    if "[SANDBOX]" in head or "沙箱拦截" in head:
        return "sandbox_blocked"
    if "用户拒绝" in head:
        return "denied_by_user"
    if "范围之外" in head or "被规则拒绝" in head or "熔断拦截" in head or "限流" in head:
        return "denied_by_policy"
    if "超时" in head:
        return "timeout"
    if "不存在" in head or "找不到" in head:
        return "not_found"
    return "internal_error"


class RecordLog:
    """工具调用记录：一行一个 json，落盘到 .agent/records/。"""

    def __init__(self, directory: Path | None = None, enabled: bool = True) -> None:
        self.enabled = enabled
        self.directory = directory or RECORDS_DIR
        self.session_id = time.strftime("%Y%m%d-%H%M%S")
        self.step = 0
        self._path: Path | None = None
        if self.enabled:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self._path = self.directory / f"{self.session_id}.jsonl"
            except OSError:
                self.enabled = False

    @property
    def trace_id(self) -> str:
        return f"{self.session_id}.{self.step}"

    def set_step(self, step: int) -> None:
        self.step = step

    def write(
        self,
        *,
        step: int,
        tool: str,
        args: str,
        result: str,
        blocked: bool = False,
        duration_ms: float = 0.0,
    ) -> None:
        """一行一事件。字段对齐规范：TraceID、时区时间戳、结构化码、摘要而非全文。"""
        if not self.enabled or self._path is None:
            return
        self.step = step
        from .guard import code_for, is_failure as _is_failure

        failed = blocked or _is_failure(result)
        code = "denied_by_policy" if blocked else ("ok" if not failed else _code_hint(result))
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "event": "tool_call",
            "step": step,
            "tool": tool,
            "args_summary": summarize(redact(args), 160),
            "args_digest": "sha256:" + __import__("hashlib").sha256(args.encode("utf-8")).hexdigest()[:12],
            "ok": not failed,
            "code": code,
            "result_summary": summarize(redact(result), 200),
            "result_chars": len(result),
            "duration_ms": round(duration_ms, 1),
            "blocked": blocked,
            "_code_for_unused": code_for.__name__,
        }
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            self.enabled = False
