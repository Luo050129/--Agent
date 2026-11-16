"""核心层：搜索。

三个来源：

    对话 —— 当前会话里的提问与回答
    产物 —— 工具写出来的文件（`write_file` 的目标路径）
    任务 —— `.agent/records` 里的工具调用

只做关键词匹配，不建索引：数据量是几百条，扫一遍比维护索引便宜。
会话持久化做出来之后，把来源换成跨会话即可，界面不用动。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..config import RECORDS_DIR
from .loop import INTERNAL_PREFIXES

KINDS = ("对话", "任务", "产物")
KIND_ORDER = {kind: index for index, kind in enumerate(KINDS)}
MAX_HITS = 80
MAX_SCAN_FILES = 30


@dataclass
class Hit:
    kind: str
    title: str
    detail: str
    anchor: str | None = None  # 只有对话有：聊天区里的文本位置
    # 跨会话检索才有：来自哪个存档、角色、打分
    session_id: str = ""
    role: str = ""
    score: float = 0.0


def _arg_value(raw: str) -> str:
    """从工具参数里抠路径或命令。解析失败就算了，不值得为它抛异常。"""
    try:
        data = json.loads(raw, strict=False) if raw.strip() else {}
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("path") or data.get("command") or "")[:120]


def collect(session, records_dir=None) -> list[Hit]:
    """把可搜的东西收进来。会话在前，记录在后。"""
    hits: list[Hit] = []

    for msg in session.messages:
        role = msg.get("role")
        text = str(msg.get("content") or "").strip()
        if role not in ("user", "assistant") or not text:
            continue
        if text.startswith(INTERNAL_PREFIXES):
            continue
        first = text.splitlines()[0]
        hits.append(Hit("对话", first[:80], text, anchor=first[:40]))

    directory = records_dir or RECORDS_DIR
    if not directory.is_dir():
        return hits

    files = sorted(directory.glob("*.jsonl"), reverse=True)[:MAX_SCAN_FILES]
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if len(hits) >= MAX_HITS * 4:
                return hits
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            tool = str(row.get("tool") or "")
            args = str(row.get("args") or "")
            result = str(row.get("result") or "")
            if not tool:
                continue
            if tool == "write_file":
                target = _arg_value(args) or "（未知路径）"
                hits.append(Hit("产物", target, f"{target}\n\n{result[:400]}"))
            else:
                brief = args.replace("\n", " ")[:70]
                hits.append(
                    Hit("任务", f"{tool}  {brief}", f"{tool}\n{args[:400]}\n\n结果：\n{result[:600]}")
                )
    return hits


def counts(hits: list[Hit]) -> dict[str, int]:
    """按类别数一下，给筛选按钮显示数字用。"""
    result = {kind: 0 for kind in KINDS}
    for hit in hits:
        if hit.kind in result:
            result[hit.kind] += 1
    return result


def search_sessions(keyword: str, limit: int = 12, directory=None) -> list[Hit]:
    """跨会话检索：扫所有会话存档，按「命中词数 + 标题加权 + 用户原话 + 新近度」排序。

    不建索引。几百个会话、每个几十行，扫一遍是毫秒级；建索引反而多一处要维护的
    一致性。要检索的关键词先按空白拆开，每个词都命中才算匹配。
    """
    words = [w for w in keyword.strip().lower().split() if w]
    if not words:
        return []

    import time

    from ..config import SESSIONS_DIR

    target = directory or SESSIONS_DIR
    if not target.is_dir():
        return []

    # 拿文件修改时间算新近度（越新分越高，最多加 1.5 分）
    now = time.time()
    files = sorted(target.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)

    found: list[Hit] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        age_days = max(0.0, (now - path.stat().st_mtime) / 86400)
        recency = max(0.0, 1.5 - age_days * 0.05)

        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = str(row.get("role") or "")
            if role not in ("user", "assistant"):
                continue
            text = str(row.get("content") or "").strip()
            if not text:
                continue

            low = text.lower()
            hit_words = sum(1 for w in words if w in low)
            if hit_words < len(words):
                continue  # 要求全部命中：多敲一个词是为了缩小范围，不是扩大

            # 打分：命中词数为主，用户原话优先（找的多半是自己说过的）
            score = float(hit_words) * 2.0
            if role == "user":
                score += 1.0
            score += recency

            found.append(
                Hit(
                    kind="对话",
                    title=_snippet(text, 60),
                    detail=text,
                    session_id=path.stem,
                    role=role,
                    score=score,
                )
            )

    found.sort(key=lambda h: h.score, reverse=True)
    return found[:limit]


def _snippet(text: str, width: int = 60) -> str:
    """截一句能看懂上下文的话：合并空白，超长从中间截。"""
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    return flat[: width - 1] + "…"


def search(hits: list[Hit], keyword: str, kinds=None) -> list[Hit]:
    """关键词匹配。`kinds` 给了就只在那几类里找（侧栏筛选）。"""
    kw = keyword.strip().lower()
    if not kw:
        return []
    allowed = set(kinds) if kinds else None
    matched = [
        hit
        for hit in hits
        if (allowed is None or hit.kind in allowed)
        and kw in (hit.title + "\n" + hit.detail).lower()
    ]
    # 对话排前面：用户找的多半是自己说过的话
    matched.sort(key=lambda h: (KIND_ORDER.get(h.kind, 99), len(h.title)))
    return matched[:MAX_HITS]
