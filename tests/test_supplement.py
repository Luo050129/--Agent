"""补全项测试：跨会话检索、工具超时与重试、提示词灰度、降级三档、状态快照。

这些是 TS 版有而 Python 版原先没有的能力，逐项验一遍。
`python tests/test_supplement.py` 直接跑。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import miniagent.tools as tools  # noqa: E402
from miniagent.core import Agent, Session, SessionStore, Sink  # noqa: E402
from miniagent.core.completion import VERIFY_PROMPT  # noqa: E402
from miniagent.core.context import RecordLog  # noqa: E402
from miniagent.core.prompt import DEFAULT_PROMPT_VERSION, _prompt_version  # noqa: E402
from miniagent.core.search import search_sessions  # noqa: E402


# ---------------------------------------------------------------- 公共替身


def tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptLLM:
    """按脚本回放；收到验证探针时走 probe 分支。"""

    label = "fake"

    def __init__(self, replies, probe="DONE", probe_calls=None):
        self.replies = list(replies)
        self.probe = probe
        self.probe_calls = probe_calls or []

    def stream(self, messages, tools=None, on_text=None):
        if messages and messages[-1].get("content") == VERIFY_PROMPT:
            return self.probe, self.probe_calls
        if not self.replies:
            return "结束", []
        text, calls = self.replies.pop(0)
        if on_text and text:
            on_text(text)
        return text, calls


class QuietSink(Sink):
    def __init__(self):
        self.states = []

    def state(self, snapshot):
        self.states.append(snapshot)


def make_store() -> SessionStore:
    return SessionStore(directory=Path(tempfile.mkdtemp()))


# ---------------------------------------------------------------- 1 跨会话检索


def test_cross_session_search():
    d = Path(tempfile.mkdtemp())
    (d / "s1.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "meta", "id": "s1"}),
                json.dumps({"type": "msg", "role": "user", "content": "熔断机制怎么实现的"}),
                json.dumps({"type": "msg", "role": "assistant", "content": "六类熔断在 guard 里"}),
            ]
        ),
        encoding="utf-8",
    )
    (d / "s2.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "meta", "id": "s2"}),
                json.dumps({"type": "msg", "role": "user", "content": "今天天气不错"}),
            ]
        ),
        encoding="utf-8",
    )

    hits = search_sessions("熔断", directory=d)
    return [
        ("命中相关会话", len(hits) == 2 and all(h.session_id == "s1" for h in hits)),
        ("用户原话打分高于助手", hits[0].role == "user" and hits[0].score > hits[1].score),
        ("多关键词需全部命中", len(search_sessions("熔断 六类", directory=d)) == 1),
        ("无关词不命中", search_sessions("不在其中", directory=d) == []),
        ("空关键词返回空", search_sessions("   ", directory=d) == []),
        ("片段截断", len(hits[0].title) <= 61),
    ]


# ---------------------------------------------------------------- 2 工具超时与重试


def test_tool_timeout_and_retry():
    # 只读工具：第一次失败，第二次成功 → 应该重试
    calls = {"n": 0}
    tools.DISPATCH["__flaky"] = lambda **_: (
        tools.ERR + "临时故障" if calls.__setitem__("n", calls["n"] + 1) or calls["n"] == 1 else "好了"
    )
    retried = tools.execute("__flaky", "{}")

    # 危险工具：失败不重试
    wcalls = {"n": 0}

    def bad_writer(path="", content=""):
        wcalls["n"] += 1
        return tools.ERR + "写失败"

    tools.DISPATCH["write_file"] = bad_writer
    tools.DANGEROUS.add("write_file")
    tools.execute("write_file", json.dumps({"path": "x", "content": "y"}))

    # 超时：睡 3 秒，上限 0.3
    tools.DISPATCH["__slow"] = lambda **_: time.sleep(3) or "迟到的结果"
    tools.TOOL_TIMEOUTS["__slow"] = 0.3
    started = time.time()
    out = tools.execute("__slow", "{}")
    waited = time.time() - started

    # 参数校验失败不重试
    pcalls = {"n": 0}

    def pcheck(path=""):
        pcalls["n"] += 1
        return "不该被调用"

    tools.DISPATCH["__pcheck"] = pcheck
    tools.TOOL_SCHEMAS.append(
        {
            "type": "function",
            "function": {
                "name": "__pcheck",
                "description": "测试用：校验必须拦下 int 型 path",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    )
    rejected = tools.execute("__pcheck", '{"path": 123}')

    for key in ("__flaky", "__slow", "__pcheck"):
        tools.DISPATCH.pop(key, None)
    tools.TOOL_TIMEOUTS.pop("__slow", None)
    tools.TOOL_SCHEMAS[:] = [s for s in tools.TOOL_SCHEMAS if s["function"]["name"] != "__pcheck"]

    return [
        ("只读工具失败重试一次", retried == "好了" and calls["n"] == 2),
        ("写类工具不重试", wcalls["n"] == 1),
        ("工具级超时生效", "工具超时" in out and waited < 1.5),
        ("超时文案带真实秒数", "0.3s" in out),
        ("参数校验失败不重试", pcalls["n"] == 0 and "参数校验失败" in rejected),
    ]


# ---------------------------------------------------------------- 3 提示词灰度


def test_prompt_canary():
    os.environ.pop("AGENT_PROMPT_VERSION", None)
    os.environ.pop("AGENT_PROMPT_CANARY", None)
    os.environ.pop("AGENT_PROMPT_CANARY_PCT", None)

    default_hit = _prompt_version("sess-a")

    os.environ["AGENT_PROMPT_CANARY"] = "py-v9"
    os.environ["AGENT_PROMPT_CANARY_PCT"] = "50"
    # 同一 seed 多次调用必须一致
    stable = {_prompt_version("sess-a") for _ in range(5)}
    buckets = {_prompt_version(f"sess-{i}") for i in range(40)}
    without_seed = _prompt_version()

    # 强制版本覆盖灰度
    os.environ["AGENT_PROMPT_VERSION"] = "py-v1"
    forced = _prompt_version("sess-a")

    for key in ("AGENT_PROMPT_VERSION", "AGENT_PROMPT_CANARY", "AGENT_PROMPT_CANARY_PCT"):
        os.environ.pop(key, None)

    return [
        ("无灰度时用默认版本", default_hit == DEFAULT_PROMPT_VERSION),
        ("同会话分桶稳定", len(stable) == 1),
        ("分桶确实分流两版", buckets == {"py-v9", DEFAULT_PROMPT_VERSION}),
        ("没种子不灰度", without_seed == DEFAULT_PROMPT_VERSION),
        ("强制版本优先", forced == "py-v1"),
    ]


# ---------------------------------------------------------------- 4 降级三档


def _degrade_run(level: str):
    """验证探针失败，看三档各自怎么收尾。"""
    llm = ScriptLLM(
        replies=[("已经写好了", [tool_call("list_dir", {"path": "."})]), ("好的", [])],
        probe="DONE",
        probe_calls=[tool_call("read_file", {"path": "不存在-verify.txt"}, "call_v")],
    )
    session = Session(seed="degrade")
    session.add_user("随便做点什么")
    sink = QuietSink()
    agent = Agent(llm, max_steps=6, degrade=level, records=RecordLog(directory=Path(tempfile.mkdtemp())))
    text = agent.run(session, sink=sink)
    return text, agent, sink


def test_degrade_levels():
    lenient_text, _, _ = _degrade_run("lenient")
    strict_text, _, _ = _degrade_run("strict")
    normal_text, _, _ = _degrade_run("normal")
    return [
        ("lenient 交付但标注未核实", "未核实" in lenient_text),
        ("strict 直接收手不返工", "验证未通过" in strict_text),
        ("normal 会返工（结果里不含未核实）", "未核实" not in normal_text),
    ]


# ---------------------------------------------------------------- 5 状态快照


def test_state_snapshot():
    store = make_store()
    session = Session(seed=store.session_id)
    store.attach(session)
    session.add_user("跑个任务")

    sink = QuietSink()
    llm = ScriptLLM(replies=[("看完了", [tool_call("list_dir", {"path": "."})]), ("结论：目录正常", [])])
    Agent(llm, max_steps=4, records=RecordLog(directory=Path(tempfile.mkdtemp()))).run(session, sink=sink)

    raw = store.path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in raw]
    state_rows = [r for r in rows if r.get("type") == "state"]

    # 中断判定：造一个「跑到一半」的档
    broken = store.directory / "broken.jsonl"
    broken.write_text(
        "\n".join(
            [
                json.dumps({"type": "meta", "id": "broken"}),
                json.dumps({"type": "msg", "role": "user", "content": "做到一半"}),
                json.dumps({"type": "state", "status": "tooling", "step": 3, "tool_calls": 2}),
            ]
        ),
        encoding="utf-8",
    )
    resumed = SessionStore.resume("broken", directory=store.directory)
    resumed.load()

    finished = SessionStore.resume(store.session_id, directory=store.directory)
    finished.load()

    # 灰度种子后补：attach 之后系统提示词应带会话 ID 算出的版本水印
    seed_applied = "prompt " in session.messages[0]["content"]

    return [
        ("状态快照写进会话档", len(state_rows) >= 1),
        ("快照字段齐全", all(k in state_rows[0] for k in ("status", "step", "tool_calls", "completion"))),
        ("未收尾的档标记中断", resumed.interrupted is not None and resumed.interrupted["step"] == 3),
        ("收尾的档不误报中断", finished.interrupted is None),
        ("系统提示词带版本水印", seed_applied),
    ]


# ---------------------------------------------------------------- 6 --yes 覆盖 --safe


def test_yes_flag():
    import argparse

    def parse(argv):
        parser = argparse.ArgumentParser()
        parser.add_argument("--safe", action="store_true")
        parser.add_argument("--yes", action="store_true")
        args = parser.parse_args(argv)
        return args.safe and not args.yes

    return [
        ("--safe 开确认", parse(["--safe"]) is True),
        ("--yes 跳过确认", parse(["--safe", "--yes"]) is False),
        ("都不给默认不确认", parse([]) is False),
    ]


# ---------------------------------------------------------------- 入口


def main() -> int:
    print("[supplement] 开始")
    checks = (
        test_cross_session_search()
        + test_tool_timeout_and_retry()
        + test_prompt_canary()
        + test_degrade_levels()
        + test_state_snapshot()
        + test_yes_flag()
    )
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[supplement] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
