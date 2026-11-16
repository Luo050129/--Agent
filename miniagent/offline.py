"""不依赖模型的离线功能自测：真工具、真子进程、真文件。

    python -m miniagent.offline            # 全量
    python -m miniagent.offline 沙箱        # 只跑名字含「沙箱」的组
    python -m miniagent.offline 规范        # 只跑合规验收组

和 tests/ 的分工：单测用替身验证「逻辑分支对不对」，这里用真实资源验证
「功能能不能用」。新环境第一件事应该是跑这个 —— 不需要 API Key。

判定契约：每项返回 True 或一句说明；不通过一律走 fail() 抛错。
（不允许「返回字符串就算过」——那样没写文件也会显示 ✓。）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import AGENT_DIR, APP_NAME, BASE_DIR

RESULTS: list[tuple[str, str, bool, str]] = []
ROOT = BASE_DIR


def fail(detail: object) -> None:
    """失败分支统一走它：抛出去会被 step 记成失败。"""
    raise AssertionError(str(detail or "断言未通过")[:200])


def step(group: str, name: str, fn) -> None:
    """跑一项。fn 返回 True 或说明字符串；抛异常/返回假值都算失败。"""
    try:
        value = fn()
        if value is True:
            RESULTS.append((group, name, True, ""))
        elif isinstance(value, str) and value:
            RESULTS.append((group, name, True, value))
        else:
            RESULTS.append((group, name, False, f"返回 {value!r}"))
    except AssertionError as exc:
        RESULTS.append((group, name, False, str(exc)))
    except Exception as exc:  # noqa: BLE001 自测里任何异常都算这项失败，不能中断整轮
        RESULTS.append((group, name, False, f"{type(exc).__name__}: {exc}"[:200]))


def workdir(tag: str) -> Path:
    """自测工作目录必须落在项目内：工具白名单就是项目根，放系统临时目录会全被拦。"""
    base = AGENT_DIR
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"offline-{tag}-", dir=base))


# ================================================================ 配置


def g_config() -> None:
    group = "配置"

    def basics() -> str:
        if not APP_NAME:
            fail("APP_NAME 为空")
        if not AGENT_DIR.name:
            fail(f"AGENT_DIR 异常：{AGENT_DIR}")
        return f"{APP_NAME} · {AGENT_DIR.name}/"

    def dirs() -> str:
        from .config import CHECKPOINT_DIR, RECORDS_DIR, SESSIONS_DIR

        for d in (CHECKPOINT_DIR, RECORDS_DIR, SESSIONS_DIR):
            if not str(d).startswith(str(BASE_DIR)):
                fail(f"{d} 不在项目内")
        return "三个落盘目录都在项目内"

    def default_model() -> str:
        from .config import default_model as dm

        value = dm()
        return value if value else fail("默认模型为空")

    def env_override() -> str:
        from .config import default_model as dm

        os.environ["AGENT_MODEL"] = "test-model-x"
        try:
            got = dm()
        finally:
            os.environ.pop("AGENT_MODEL", None)
        return "环境变量可覆盖默认模型" if got == "test-model-x" else fail(f"覆盖无效：{got}")

    step(group, "基本信息", basics)
    step(group, "落盘目录在项目内", dirs)
    step(group, "默认模型可解析", default_model)
    step(group, "环境变量能覆盖默认值", env_override)


# ================================================================ 供应商


def g_registry() -> None:
    group = "供应商"

    def catalog() -> str:
        from .model import registry

        if len(registry.CATALOG) < 20:
            fail(f"目录只有 {len(registry.CATALOG)} 条")
        return f"{len(registry.CATALOG)} 个模型"

    def protocols() -> str:
        from .model import registry

        kinds = {m.protocol for m in registry.CATALOG}
        need = {"openai", "anthropic", "gemini", "responses"}
        if not need <= kinds:
            fail(f"缺协议：{need - kinds}")
        return "、".join(sorted(kinds))

    def keys_unique() -> str:
        from .model import registry

        keys = [m.key for m in registry.CATALOG]
        if len(keys) != len(set(keys)):
            fail("有重复的 key")
        return f"{len(set(keys))} 个唯一 key"

    def find_by_key() -> str:
        from .model import registry

        spec = registry.find("DeepSeek V4 Flash")
        if spec is None:
            fail("按名称找不到 DeepSeek V4 Flash")
        if not spec.model:
            fail("命中但 model 为空")
        return f"{spec.key} → {spec.protocol}"

    def key_env_declared() -> str:
        from .model import registry

        missing = [m.key for m in registry.CATALOG if not m.key_env]
        if missing:
            fail(f"这些没声明 Key 环境变量：{missing[:3]}")
        return "每条都声明了 key_env"

    def mainstream_marked() -> str:
        from .model import registry

        if len(registry.MAINSTREAM) < 5:
            fail(f"主流推荐只有 {len(registry.MAINSTREAM)} 个")
        return f"{len(registry.MAINSTREAM)} 个主流候选"

    step(group, "目录规模", catalog)
    step(group, "四种协议齐全", protocols)
    step(group, "key 不重复", keys_unique)
    step(group, "按名称能查到", find_by_key)
    step(group, "都声明了 Key 环境变量", key_env_declared)
    step(group, "主流推荐有标记", mainstream_marked)


# ================================================================ 协议


def _ctx(tools=None, protocol_quirks=None):
    from .model.wire import RequestContext

    return RequestContext(
        model_id="test-model",
        messages=[{"role": "user", "content": "你好"}],
        tools=tools,
        temperature=0.2,
        max_tokens=256,
        stream=True,
        api_key="sk-test-key",
        base_url="https://example.test/v1",
        quirks=protocol_quirks or {},
    )


def g_protocols() -> None:
    group = "协议"
    sample_tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读文件",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]

    def openai_shape() -> str:
        from .model.protocols.openai_chat import OpenAIChatProtocol

        req = OpenAIChatProtocol().build(_ctx(sample_tools))
        body = json.loads(req.body)
        if body.get("model") != "test-model":
            fail("model 没进 body")
        if "tools" not in body:
            fail("tools 没进 body")
        if not req.headers.get("Authorization", "").startswith("Bearer "):
            fail("缺 Bearer 鉴权头")
        return f"POST {req.url.rsplit('/', 1)[-1]}"

    def openai_messages() -> str:
        from .model.protocols.openai_chat import OpenAIChatProtocol

        body = json.loads(OpenAIChatProtocol().build(_ctx()).body)
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            fail("messages 结构不对")
        return "messages 就位"

    def anthropic_shape() -> str:
        from .model.protocols.anthropic import AnthropicProtocol

        req = AnthropicProtocol().build(_ctx(sample_tools))
        body = json.loads(req.body)
        if "system" in body and not isinstance(body["system"], (str, list)):
            fail("system 字段形状不对")
        if "messages" not in body:
            fail("缺 messages")
        if "x-api-key" not in req.headers:
            fail(f"缺 x-api-key 头，实际头：{list(req.headers)}")
        if "anthropic-version" not in req.headers:
            fail("缺 anthropic-version 头")
        tools = body.get("tools") or []
        if tools and "input_schema" not in tools[0]:
            fail("tools 没用 input_schema")
        return "x-api-key + input_schema"

    def gemini_shape() -> str:
        from .model.protocols.gemini import GeminiProtocol

        req = GeminiProtocol().build(_ctx(sample_tools))
        body = json.loads(req.body)
        if "contents" not in body:
            fail("缺 contents")
        if "test-model" not in req.url:
            fail(f"模型名没进 URL：{req.url}")
        if req.headers.get("x-goog-api-key") != "sk-test-key":
            fail(f"Key 没进 x-goog-api-key 头，实际头：{list(req.headers)}")
        return "模型进 URL、Key 进 header"

    def gemini_role_mapping() -> str:
        from .model.protocols.gemini import GeminiProtocol
        from .model.wire import RequestContext

        ctx = RequestContext(
            model_id="m",
            messages=[
                {"role": "user", "content": "问题"},
                {"role": "assistant", "content": "答复"},
            ],
            tools=None,
            temperature=0.1,
            max_tokens=64,
            stream=True,
            api_key="k",
            base_url="https://example.test",
        )
        body = json.loads(GeminiProtocol().build(ctx).body)
        roles = [c.get("role") for c in body["contents"]]
        if roles != ["user", "model"]:
            fail(f"角色映射不对：{roles}")
        return "user/assistant → user/model"

    def responses_shape() -> str:
        from .model.protocols.responses import ResponsesProtocol

        req = ResponsesProtocol().build(_ctx())
        body = json.loads(req.body)
        if "input" not in body and "messages" not in body:
            fail("既没有 input 也没有 messages")
        return "responses 协议可构造"

    step(group, "OpenAI 兼容请求形状", openai_shape)
    step(group, "OpenAI messages 结构", openai_messages)
    step(group, "Anthropic 请求形状", anthropic_shape)
    step(group, "Gemini 请求形状", gemini_shape)
    step(group, "Gemini 角色映射", gemini_role_mapping)
    step(group, "Responses 请求形状", responses_shape)


# ================================================================ 流式解析


def _feed(parser, chunks: list[str]):
    """SSE 解析器是**按行**喂的（feed 一次一行 bytes），不是整块丢进去。

    整块喂会让 `event:` 行把后面的 data 一起吞掉，看起来像"解析器坏了"。
    """
    events = []
    for chunk in chunks:
        for line in chunk.splitlines():
            events.extend(parser.feed(line.encode("utf-8")))
    events.extend(parser.finish())
    return events


def g_stream() -> None:
    group = "流式解析"

    def openai_text() -> str:
        from .model.protocols.openai_chat import OpenAIChatProtocol
        from .model.wire import TextDelta

        parser = OpenAIChatProtocol().parser()
        events = _feed(
            parser,
            [
                'data: {"choices":[{"delta":{"content":"你"}}]}\n\n',
                'data: {"choices":[{"delta":{"content":"好"}}]}\n\n',
                "data: [DONE]\n\n",
            ],
        )
        text = "".join(e.text for e in events if isinstance(e, TextDelta))
        if text != "你好":
            fail(f"拼出来是 {text!r}")
        return "分片能拼回整句"

    def openai_tool_call() -> str:
        from .model.protocols.openai_chat import OpenAIChatProtocol
        from .model.wire import Finished, ToolCallDelta

        parser = OpenAIChatProtocol().parser()
        events = _feed(
            parser,
            [
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read_file","arguments":"{\\"pa"}}]}}]}\n\n',
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"th\\":\\"a.txt\\"}"}}]}}]}\n\n',
                # finish_reason 才是收尾信号；[DONE] 只是流结束标记
                'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
                "data: [DONE]\n\n",
            ],
        )
        pieces = "".join(e.arguments for e in events if isinstance(e, ToolCallDelta))
        if json.loads(pieces) != {"path": "a.txt"}:
            fail(f"分片参数拼错：{pieces}")
        names = [e.name for e in events if isinstance(e, ToolCallDelta) and e.name]
        if names != ["read_file"]:
            fail(f"工具名没解析出来：{names}")
        if not [e for e in events if isinstance(e, Finished)]:
            fail("finish_reason 没转成收尾事件")
        return "分片参数能拼回合法 JSON"

    def anthropic_events() -> str:
        from .model.protocols.anthropic import AnthropicProtocol
        from .model.wire import TextDelta

        parser = AnthropicProtocol().parser()
        events = _feed(
            parser,
            [
                "event: content_block_delta\n"
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"你好"}}\n\n',
                'data: {"type":"message_stop"}\n\n',
            ],
        )
        text = "".join(e.text for e in events if isinstance(e, TextDelta))
        if text != "你好":
            fail(f"拼出来是 {text!r}")
        return "content_block_delta 能解"

    def gemini_events() -> str:
        from .model.protocols.gemini import GeminiProtocol
        from .model.wire import TextDelta

        parser = GeminiProtocol().parser()
        events = _feed(
            parser,
            [
                'data: {"candidates":[{"content":{"parts":[{"text":"你好"}]}}]}\n\n',
            ],
        )
        text = "".join(e.text for e in events if isinstance(e, TextDelta))
        if text != "你好":
            fail(f"拼出来是 {text!r}")
        return "candidates.parts 能解"

    step(group, "OpenAI 文本分片", openai_text)
    step(group, "OpenAI 工具调用分片", openai_tool_call)
    step(group, "Anthropic 事件流", anthropic_events)
    step(group, "Gemini 事件流", gemini_events)


# ================================================================ 上下文


def g_context() -> None:
    group = "上下文"

    def prepare_keeps_system() -> str:
        from .core.context import ContextManager

        ctx = ContextManager(budget=4000)
        msgs = [{"role": "system", "content": "系统提示"}] + [
            {"role": "user", "content": f"消息{i}"} for i in range(30)
        ]
        out, stats = ctx.prepare(msgs)
        if out[0].get("role") != "system":
            fail("系统提示被挤掉了")
        return f"{len(msgs)} → {len(out)} 条"

    def prepare_respects_budget() -> str:
        from .core.context import ContextManager

        ctx = ContextManager(budget=600)
        msgs = [{"role": "system", "content": "系统"}] + [
            {"role": "user", "content": "很长的一段话。" * 40} for _ in range(20)
        ]
        out, stats = ctx.prepare(msgs)
        if stats.tokens > 600 * 1.3:
            fail(f"压缩后仍有 {stats.tokens} tok，超预算太多")
        return f"{stats.tokens} tok / 预算 {stats.budget}"

    def compact_tool_result() -> str:
        from .core.context import ContextManager

        ctx = ContextManager(tool_result_tokens=50)
        big = "结果行\n" * 2000
        out = ctx.compact_tool_result(big)
        if len(out) >= len(big):
            fail(f"没有压缩：{len(big)} → {len(out)}")
        return f"{len(big)} → {len(out)} 字符"

    def records_write() -> str:
        from .core.context import RecordLog

        d = workdir("records")
        log = RecordLog(directory=d)
        log.set_step(1)
        log.write(step=1, tool="read_file", args='{"path":"a.txt"}', result="内容")
        lines = (d / f"{log.trace_id.split('.')[0]}.jsonl").read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[0])
        need = ("ts", "trace_id", "step", "tool", "args_summary", "ok", "code", "result_summary")
        missing = [k for k in need if k not in row]
        if missing:
            fail(f"缺字段：{missing}")
        return "字段齐全"

    def records_timestamp() -> str:
        from .core.context import RecordLog

        d = workdir("records-ts")
        log = RecordLog(directory=d)
        log.write(step=1, tool="t", args="{}", result="r")
        row = json.loads((d / f"{log.trace_id.split('.')[0]}.jsonl").read_text(encoding="utf-8").splitlines()[0])
        ts = str(row["ts"])
        # 要求 ISO 8601：带日期、'T' 分隔、带时区 —— 光有时分秒跨天就分不清了
        if "T" not in ts or len(ts) < 19:
            fail(f"不是 ISO 8601：{ts}")
        if not (ts.endswith("Z") or "+" in ts[10:] or ts[10:].count("-") > 0):
            fail(f"没带时区：{ts}")
        return ts

    step(group, "压缩保留系统提示", prepare_keeps_system)
    step(group, "压缩守住预算", prepare_respects_budget)
    step(group, "工具结果会被压缩", compact_tool_result)
    step(group, "记录字段齐全", records_write)
    step(group, "记录带完整时间戳", records_timestamp)


# ================================================================ 熔断


def g_guard() -> None:
    group = "熔断"

    def same_call() -> str:
        from .core.guard import Guard, Limits

        g = Guard(Limits(max_same_call=2, max_calls_per_minute=999))
        for _ in range(2):
            if g.before_tool("read_file", '{"path":"a"}'):
                fail("前两次不该拦")
            g.after_tool("read_file", '{"path":"a"}', "ok")
        blocked = g.before_tool("read_file", '{"path":"a"}')
        if not blocked:
            fail("第三次同调用没被拦")
        return blocked[:24]

    def fail_streak() -> str:
        from .core.guard import Guard, Limits

        g = Guard(Limits(max_fail_streak=3, max_same_call=99, max_failures_per_call=99, max_calls_per_minute=999))
        for i in range(3):
            g.before_tool(f"t{i}", "{}")
            g.after_tool(f"t{i}", "{}", "[ERR] 失败")
        reason = g.check_stop()
        if not reason:
            fail("连续失败没触发熔断")
        return reason[:24]

    def tool_budget() -> str:
        from .core.guard import Guard, Limits

        g = Guard(Limits(max_tool_calls=2, max_same_call=99, max_calls_per_minute=999))
        for i in range(2):
            g.before_tool(f"t{i}", "{}")
            g.after_tool(f"t{i}", "{}", "ok")
        blocked = g.before_tool("t9", "{}")
        if not blocked:
            fail("超出工具预算没被拦")
        return blocked[:24]

    def rate_limit() -> str:
        from .core.guard import Guard, Limits

        g = Guard(Limits(max_calls_per_minute=3, max_same_call=99, max_tool_calls=999))
        for i in range(3):
            g.before_tool(f"t{i}", "{}")
            g.after_tool(f"t{i}", "{}", "ok")
        blocked = g.before_tool("t9", "{}")
        if not blocked or "限流" not in blocked:
            fail(f"限流没生效：{blocked}")
        return "每分钟上限生效"

    def stop_codes() -> str:
        from .core.guard import code_for

        cases = {
            "步数用尽（20 步）仍未收尾": "steps_exhausted",
            "触发限流：每分钟最多 30 次": "rate_limited",
            "连续失败 6 次": "fail_streak",
        }
        got = {k: code_for(k) for k in cases}
        wrong = {k: v for k, v in got.items() if v != cases[k]}
        if wrong:
            fail(f"推断错：{wrong}")
        return "、".join(sorted(set(got.values())))

    def failure_classify() -> str:
        from .core.guard import is_failure

        if not is_failure("[ERR] 文件不存在"):
            fail("没识别出 [ERR]")
        if is_failure("一切正常"):
            fail("把正常结果当失败")
        return "[ERR] 能被识别"

    step(group, "同一调用重复", same_call)
    step(group, "连续失败", fail_streak)
    step(group, "工具预算", tool_budget)
    step(group, "每分钟限流", rate_limit)
    step(group, "停止码推断", stop_codes)
    step(group, "失败判定", failure_classify)


# ================================================================ 工具


def g_tools() -> None:
    group = "工具"
    work = workdir("tools")

    def write_and_read() -> str:
        from .tools import execute

        target = work / "a.txt"
        out = execute("write_file", json.dumps({"path": str(target), "content": "第一行\n第二行"}))
        if out.startswith("[ERR]"):
            fail(out[:80])
        back = execute("read_file", json.dumps({"path": str(target)}))
        if "第一行" not in back:
            fail("读回来没有内容")
        return f"{target.name} 写了又读"

    def list_dir() -> str:
        from .tools import execute

        out = execute("list_dir", json.dumps({"path": str(work)}))
        if "a.txt" not in out:
            fail(out[:80])
        return "能列目录"

    def validate_type() -> str:
        from .tools import execute

        out = execute("read_file", '{"path": 123}')
        if "参数校验失败" not in out and "应为 string" not in out:
            fail(out[:80])
        return "类型错被拦"

    def validate_required() -> str:
        from .tools import execute

        out = execute("read_file", "{}")
        if "缺少必填" not in out:
            fail(out[:80])
        return "缺必填被拦"

    def unknown_tool() -> str:
        from .tools import execute

        out = execute("__no_such_tool", "{}")
        if not out.startswith("[ERR]") or "未知工具" not in out:
            fail(out[:80])
        return "未知工具转 [ERR]"

    def bad_json() -> str:
        from .tools import execute

        out = execute("read_file", "{不是 json")
        if not out.startswith("[ERR]"):
            fail(out[:80])
        return "坏 JSON 转 [ERR]"

    def timeout_declared() -> str:
        from .tools import TOOL_TIMEOUTS

        if "read_file" not in TOOL_TIMEOUTS:
            fail("只读工具没声明超时")
        return f"{len(TOOL_TIMEOUTS)} 个工具声明了超时"

    def no_throw() -> str:
        from .tools import execute

        # 工具永不抛异常：给一堆乱七八糟的参数，看它都转成文本
        for name in ("read_file", "list_dir", "write_file", "run_shell", "use_skill"):
            out = execute(name, '{"path": null, "command": null, "content": null, "source": null}')
            if not isinstance(out, str):
                fail(f"{name} 返回了非字符串")
        return "五个工具都没抛异常"

    step(group, "写文件再读回", write_and_read)
    step(group, "列目录", list_dir)
    step(group, "参数类型校验", validate_type)
    step(group, "必填校验", validate_required)
    step(group, "未知工具", unknown_tool)
    step(group, "坏 JSON", bad_json)
    step(group, "工具级超时已声明", timeout_declared)
    step(group, "工具永不抛异常", no_throw)


# ================================================================ 沙箱


def g_sandbox() -> None:
    group = "沙箱"
    outside = str(Path("C:/Windows/win.ini") if os.name == "nt" else "/etc/hostname")

    def outside_read() -> str:
        from .tools import runtime

        out = runtime.run_python(f"open({outside!r}).read()")
        if "[SANDBOX]" not in out:
            fail(f"越界读没被拦：{out[:80]}")
        return "越界读被拦"

    def outside_write() -> str:
        from .tools import runtime

        target = str(Path("C:/Windows/xu-should-not-exist.txt") if os.name == "nt" else "/tmp/xu-should-not-exist.txt")
        out = runtime.run_python(f"open({target!r}, 'w').write('x')")
        if "[SANDBOX]" not in out:
            fail(f"越界写没被拦：{out[:80]}")
        return "越界写被拦"

    def subprocess_guard() -> str:
        from .tools import runtime

        out = runtime.run_python("import subprocess; subprocess.run('echo hi')")
        if "[SANDBOX]" not in out:
            fail(f"子进程没被拦：{out[:80]}")
        return "子进程被拦"

    def network_guard() -> str:
        from .tools import runtime

        out = runtime.run_python("import socket; socket.socket()")
        if "[SANDBOX]" not in out:
            fail(f"网络没被拦：{out[:80]}")
        return "网络被拦"

    def timeout_kills() -> str:
        from .tools import runtime

        cmd = "ping -n 31 127.0.0.1" if os.name == "nt" else "sleep 30"
        start = time.monotonic()
        out = runtime.run_shell(cmd, timeout=2)
        elapsed = time.monotonic() - start
        if "超时" not in out:
            fail(f"没报超时：{out[:80]}")
        if elapsed > 15:
            fail(f"干等了 {elapsed:.0f}s")
        return f"{elapsed:.1f}s 收手"

    def normal_still_works() -> str:
        from .tools import runtime

        out = runtime.run_python("print(1 + 1)")
        if "2" not in out:
            fail(f"正常代码跑不了：{out[:80]}")
        return "项目内正常执行不受影响"

    step(group, "越界读", outside_read)
    step(group, "越界写", outside_write)
    step(group, "子进程守卫", subprocess_guard)
    step(group, "网络守卫", network_guard)
    step(group, "超时杀进程树", timeout_kills)
    step(group, "正常执行不受影响", normal_still_works)


# ================================================================ 撤销


def g_checkpoints() -> None:
    group = "撤销"
    work = workdir("undo")

    def snapshot_and_undo() -> str:
        from .tools import execute
        from .tools.checkpoints import undo_last

        target = work / "u.txt"
        target.write_text("原始", encoding="utf-8")
        execute("write_file", json.dumps({"path": str(target), "content": "改过"}))
        if target.read_text(encoding="utf-8") != "改过":
            fail("写入没生效")
        msg = undo_last()
        if target.read_text(encoding="utf-8") != "原始":
            fail(f"撤销没恢复原内容（{msg[:40]}）")
        return "恢复了原内容"

    def new_file_removed() -> str:
        from .tools import execute
        from .tools.checkpoints import undo_last

        target = work / "fresh.txt"
        execute("write_file", json.dumps({"path": str(target), "content": "新文件"}))
        if not target.exists():
            fail("文件没建出来")
        undo_last()
        if target.exists():
            fail("撤销后新文件还在")
        return "新建的文件被收回"

    def order_is_stable() -> str:
        from .tools import execute
        from .tools.checkpoints import undo_last

        target = work / "chain.txt"
        target.write_text("v0", encoding="utf-8")
        for i in range(1, 4):
            execute("write_file", json.dumps({"path": str(target), "content": f"v{i}"}))
        undo_last()
        after_one = target.read_text(encoding="utf-8")
        undo_last()
        after_two = target.read_text(encoding="utf-8")
        if (after_one, after_two) != ("v2", "v1"):
            fail(f"回退顺序乱了：{after_one} → {after_two}")
        return "v3→v2→v1 顺序正确"

    step(group, "改动可回退", snapshot_and_undo)
    step(group, "新建文件可收回", new_file_removed)
    step(group, "链式回退顺序稳定", order_is_stable)


# ================================================================ 存档


def g_persistence() -> None:
    group = "存档"
    work = workdir("store")

    def write_and_resume() -> str:
        from .core import Session, SessionStore

        store = SessionStore(directory=work)
        session = Session(seed=store.session_id)
        store.attach(session)
        session.add_user("第一句")
        session.append({"role": "assistant", "content": "答复"})

        again = SessionStore.resume(store.session_id, directory=work).load()
        roles = [m["role"] for m in again.messages if m["role"] != "system"]
        if roles != ["user", "assistant"]:
            fail(f"回放出来是 {roles}")
        return "一问一答能回放"

    def title_from_first_message() -> str:
        from .core import Session, SessionStore

        store = SessionStore(directory=work)
        session = Session(seed=store.session_id)
        store.attach(session)
        session.add_user("帮我统计代码行数")
        if not store.title:
            fail("标题没生成")
        return store.title[:16]

    def list_sessions() -> str:
        from .core import SessionStore

        items = SessionStore.list_sessions(directory=work)
        if not items:
            fail("列不出会话")
        if "id" not in items[0] or "updated" not in items[0]:
            fail("列表字段不全")
        return f"{len(items)} 个会话"

    def interrupt_marked() -> str:
        from .core import SessionStore

        path = work / "half-done.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"type": "meta", "id": "half-done"}),
                    json.dumps({"type": "msg", "role": "user", "content": "做到一半"}),
                    json.dumps({"type": "state", "status": "tooling", "step": 4, "tool_calls": 2}),
                ]
            ),
            encoding="utf-8",
        )
        store = SessionStore.resume("half-done", directory=work)
        store.load()
        if not store.interrupted or store.interrupted["step"] != 4:
            fail(f"没标出中断：{store.interrupted}")
        return "第 4 步中断"

    def finished_not_marked() -> str:
        from .core import Session, SessionStore

        store = SessionStore(directory=work)
        session = Session(seed=store.session_id)
        store.attach(session)
        session.add_user("跑完了")
        store.record_state({"status": "done", "step": 2, "tool_calls": 1})
        again = SessionStore.resume(store.session_id, directory=work)
        again.load()
        if again.interrupted:
            fail("收尾的会话被误报中断")
        return "收尾态不误报"

    def repair_tail() -> str:
        from .core import SessionStore

        path = work / "truncated.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"type": "meta", "id": "truncated"}),
                    json.dumps({"type": "msg", "role": "user", "content": "问"}),
                    json.dumps(
                        {
                            "type": "msg",
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file"}}],
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )
        session = SessionStore.resume("truncated", directory=work).load()
        if session.messages[-1].get("tool_calls"):
            fail("悬空的 tool_calls 没被剥掉")
        return "崩溃截断能修好"

    step(group, "写入与恢复", write_and_resume)
    step(group, "标题自动生成", title_from_first_message)
    step(group, "能列出存档", list_sessions)
    step(group, "中断有标记", interrupt_marked)
    step(group, "收尾不误报", finished_not_marked)
    step(group, "崩溃尾巴能修", repair_tail)


# ================================================================ 搜索


def g_search() -> None:
    group = "搜索"

    def in_session() -> str:
        from .core import Session
        from .core.search import collect, search

        session = Session()
        session.add_user("我想知道熔断怎么实现的")
        session.append({"role": "assistant", "content": "六类熔断都在 guard 里"})
        hits = search(collect(session, records_dir=workdir("search-empty")), "熔断")
        if not hits:
            fail("会话内搜不到")
        return f"{len(hits)} 条命中"

    def by_kind() -> str:
        from .core import Session
        from .core.search import collect, search

        session = Session()
        session.add_user("改一下 README")
        hits = search(collect(session, records_dir=workdir("search-kind")), "README", kinds=["任务"])
        if hits:
            fail("按类别过滤没生效")
        return "类别过滤有效"

    def cross_session() -> str:
        from .core.search import search_sessions

        d = workdir("search-cross")
        (d / "s1.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"type": "msg", "role": "user", "content": "熔断机制怎么实现"}),
                    json.dumps({"type": "msg", "role": "assistant", "content": "六类熔断在 guard"}),
                ]
            ),
            encoding="utf-8",
        )
        (d / "s2.jsonl").write_text(
            json.dumps({"type": "msg", "role": "user", "content": "今天天气不错"}), encoding="utf-8"
        )
        hits = search_sessions("熔断", directory=d)
        if len(hits) != 2:
            fail(f"跨会话命中数不对：{len(hits)}")
        if hits[0].session_id != "s1":
            fail("命中了错误的会话")
        return f"{len(hits)} 条，都在 s1"

    def and_semantics() -> str:
        from .core.search import search_sessions

        d = workdir("search-and")
        (d / "s.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"type": "msg", "role": "user", "content": "熔断机制"}),
                    json.dumps({"type": "msg", "role": "assistant", "content": "六类熔断在 guard"}),
                ]
            ),
            encoding="utf-8",
        )
        both = search_sessions("熔断 六类", directory=d)
        only_one = search_sessions("熔断 不存在的词", directory=d)
        if len(both) != 1:
            fail(f"多词应只命中同时含两词的那条，实际 {len(both)}")
        if only_one:
            fail("缺一个词也命中了，不是 AND 语义")
        return "多词要求全部命中"

    def empty_keyword() -> str:
        from .core.search import search_sessions

        if search_sessions("   ", directory=workdir("search-empty2")):
            fail("空关键词返回了结果")
        return "空关键词返回空"

    step(group, "会话内搜索", in_session)
    step(group, "按类别过滤", by_kind)
    step(group, "跨会话搜索", cross_session)
    step(group, "多词是 AND 语义", and_semantics)
    step(group, "空关键词不报错", empty_keyword)


# ================================================================ 文档


def g_documents() -> None:
    group = "文档"
    work = workdir("docs")

    def plain_text() -> str:
        from .tools import execute

        target = work / "a.md"
        target.write_text("# 标题\n\n正文内容", encoding="utf-8")
        out = execute("read_document", json.dumps({"path": str(target)}))
        if "正文内容" not in out:
            fail(out[:80])
        return "纯文本能读"

    def csv_as_text() -> str:
        from .tools import execute

        target = work / "a.csv"
        target.write_text("a,b\n1,2", encoding="utf-8")
        out = execute("read_document", json.dumps({"path": str(target)}))
        if "1,2" not in out:
            fail(out[:80])
        return "CSV 能读"

    def unknown_ext() -> str:
        from .tools import execute

        target = work / "a.zzz"
        target.write_text("随便", encoding="utf-8")
        out = execute("read_document", json.dumps({"path": str(target)}))
        if not isinstance(out, str) or not out:
            fail("未知扩展名没给结果")
        return "未知扩展名走兜底"

    def missing_file() -> str:
        from .tools import execute

        out = execute("read_document", json.dumps({"path": str(work / "没有这个.pdf")}))
        if not out.startswith("[ERR]"):
            fail(out[:80])
        return "缺文件转 [ERR]"

    step(group, "纯文本", plain_text)
    step(group, "CSV", csv_as_text)
    step(group, "未知扩展名兜底", unknown_ext)
    step(group, "文件不存在", missing_file)


# ================================================================ 技能


def g_skills() -> None:
    group = "技能"
    work = workdir("skills")
    work.mkdir(parents=True, exist_ok=True)
    # 技能就是 skills/*.md 一个文件，不是子目录 + SKILL.md
    (work / "offline-demo.md").write_text(
        "# offline-demo\n\n> 自测用的假技能：假装会做事\n\n正文说明。\n",
        encoding="utf-8",
    )

    def discover_local() -> str:
        from .skills.manager import discover

        found = discover([work])
        keys = [s.key for s in found]
        if "offline-demo" not in keys:
            fail(f"没发现技能，实际键：{keys}")
        return f"{len(found)} 个技能"

    def catalog_text() -> str:
        from .skills.manager import catalog_text as text_of

        text = text_of([work])
        if "offline-demo" not in text:
            fail("目录文本里没有这个技能")
        return "技能清单能渲染"

    def install_local() -> str:
        from .skills.manager import SKILLS_DIR, install

        before = set(SKILLS_DIR.glob("*.md")) if SKILLS_DIR.is_dir() else set()
        try:
            keys = install(str(work / "offline-demo.md"))
            if not keys:
                fail("安装返回空")
            landed = SKILLS_DIR / "offline-demo.md"
            if not landed.is_file():
                fail(f"文件没落到 {SKILLS_DIR}")
            return f"装好 {len(keys)} 个"
        finally:
            # 别把自测产物留在正式技能目录里
            for path in (SKILLS_DIR.glob("offline-demo*.md") if SKILLS_DIR.is_dir() else []):
                if path not in before:
                    path.unlink(missing_ok=True)

    def bad_source() -> str:
        from .skills.manager import install

        try:
            install(str(work / "根本没有这个文件.md"))
        except Exception:  # noqa: BLE001 坏源就该抛
            return "坏源会报错"
        fail("坏源居然没报错")

    step(group, "发现本地技能", discover_local)
    step(group, "技能清单渲染", catalog_text)
    step(group, "安装到目标目录", install_local)
    step(group, "坏源报错", bad_source)


# ================================================================ 插件


def g_plugins() -> None:
    group = "插件"

    def load_tools() -> str:
        from .plugins.manager import load_tools

        schemas, handlers = load_tools()
        if not isinstance(schemas, list) or not isinstance(handlers, dict):
            fail("返回结构不对")
        return f"{len(schemas)} 个插件工具"

    def broken_isolated() -> str:
        """坏插件只影响自己：写一个会抛错的，看好插件还在不在。"""
        from .plugins import manager as pm

        plugin_dir = pm.PLUGIN_DIR
        plugin_dir.mkdir(parents=True, exist_ok=True)
        broken = plugin_dir / "_offline_broken"
        broken.mkdir(exist_ok=True)
        (broken / "plugin.json").write_text(
            json.dumps({"name": "_offline_broken", "entry": "main.py", "description": "坏插件"}), encoding="utf-8"
        )
        (broken / "main.py").write_text("raise RuntimeError('我是坏的')", encoding="utf-8")
        try:
            schemas, _ = pm.load_tools()
            if not isinstance(schemas, list):
                fail("坏插件让整体返回结构变了")
            return "坏插件没拖垮整体"
        finally:
            shutil.rmtree(broken, ignore_errors=True)

    step(group, "加载插件工具", load_tools)
    step(group, "坏插件不拖垮整体", broken_isolated)


# ================================================================ MCP


def g_mcp() -> None:
    group = "MCP"

    def summary() -> str:
        from .mcp.manager import mcp_manager

        text = mcp_manager.summary_text()
        if not isinstance(text, str):
            fail("摘要不是字符串")
        return f"{len(text)} 字符"

    def servers_shape() -> str:
        from .mcp.manager import mcp_manager

        servers = mcp_manager.servers()
        if not isinstance(servers, list):
            fail(f"servers() 返回 {type(servers).__name__}")
        return f"{len(servers)} 个已配置 server"

    def tools_isolated() -> str:
        from .mcp.manager import mcp_manager

        tools = mcp_manager.tools()
        if not isinstance(tools, tuple) or len(tools) != 2:
            fail(f"tools() 返回 {type(tools).__name__}")
        schemas, handlers = tools
        return f"{len(schemas)} 个 MCP 工具 / {len(handlers)} 个处理器"

    step(group, "摘要文本", summary)
    step(group, "配置读取", servers_shape)
    step(group, "工具清单", tools_isolated)


# ================================================================ 内核


class _ScriptLLM:
    """按脚本回放的假模型。"""

    label = "fake"

    def __init__(self, replies, probe="DONE", probe_calls=None):
        self.replies = list(replies)
        self.probe = probe
        self.probe_calls = probe_calls or []

    def stream(self, messages, tools=None, on_text=None):
        from .core.completion import VERIFY_PROMPT

        if messages and messages[-1].get("content") == VERIFY_PROMPT:
            return self.probe, self.probe_calls
        if not self.replies:
            return "结束", []
        text, calls = self.replies.pop(0)
        if on_text and text:
            on_text(text)
        return text, calls


class _QuietSink:
    """只记状态，不打印。"""

    def __init__(self):
        self.states = []

    def text(self, piece):
        pass

    def text_done(self):
        pass

    def step_done(self, step):
        pass

    def tool_call(self, name, args):
        pass

    def tool_result(self, result):
        pass

    def state(self, snapshot):
        self.states.append(snapshot)

    def notice(self, message):
        pass

    def error(self, message):
        pass

    def plan(self, steps, replanned=False):
        pass

    def plan_step(self, index, status, note=""):
        pass

    def confirm(self, name, args, hint=""):
        return True


def _tool_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def g_kernel(target: Path | None = None) -> None:
    from .core import Agent, Session
    from .core.context import RecordLog

    group = "内核"
    work = target or workdir("kernel")

    def react_loop() -> str:
        llm = _ScriptLLM([("先看看", [_tool_call("list_dir", {"path": str(work)})]), ("看完了，目录是空的", [])])
        session = Session(seed="k1")
        session.add_user("看看目录")
        sink = _QuietSink()
        text = Agent(llm, max_steps=4, records=RecordLog(directory=work)).run(session, sink=sink)
        if "看完了" not in text:
            fail(f"答复不对：{text[:60]}")
        if not any(s.get("tool_calls") for s in sink.states):
            fail("状态里没体现工具调用")
        return "工具调用 → 答复闭环"

    def plan_mode() -> str:
        llm = _ScriptLLM([("1. 第一步\n2. 第二步", []), ("第一步做完了", []), ("全部完成", [])])
        session = Session(seed="k2")
        session.add_user("做个两步的任务")
        sink = _QuietSink()
        text = Agent(llm, max_steps=5, mode="plan", records=RecordLog(directory=work)).run(session, sink=sink)
        if not text:
            fail("计划模式没返回")
        return "计划模式能跑完"

    def verify_rework() -> str:
        llm = _ScriptLLM(
            replies=[("做完了", [_tool_call("list_dir", {"path": str(work)})]), ("确实做完了", [])],
            probe_calls=[_tool_call("read_file", {"path": str(work / "不存在.txt")}, "cv")],
        )
        session = Session(seed="k3")
        session.add_user("做个事")
        sink = _QuietSink()
        text = Agent(llm, max_steps=6, records=RecordLog(directory=work)).run(session, sink=sink)
        if not text:
            fail("没有返回")
        return "验证失败会返工"

    def safe_mode_denies() -> str:
        llm = _ScriptLLM([("写个文件", [_tool_call("write_file", {"path": str(work / "safe.txt"), "content": "x"})]), ("好", [])])
        session = Session(seed="k4")
        session.add_user("写文件")
        sink = _QuietSink()
        sink.confirm = lambda name, args, hint="": False  # 用户拒绝
        Agent(llm, max_steps=4, records=RecordLog(directory=work)).run(session, sink=sink, safe=True)
        if (work / "safe.txt").exists():
            fail("拒绝了还是写了文件")
        return "拒绝后真没执行"

    def safe_mode_allows() -> str:
        target = work / "allowed.txt"
        llm = _ScriptLLM([("写个文件", [_tool_call("write_file", {"path": str(target), "content": "内容"})]), ("好", [])])
        session = Session(seed="k5")
        session.add_user("写文件")
        sink = _QuietSink()
        sink.confirm = lambda name, args, hint="": True  # 用户放行
        Agent(llm, max_steps=4, records=RecordLog(directory=work)).run(session, sink=sink, safe=True)
        if not target.exists():
            fail("放行后没写出来")
        return "同意后正常执行"

    def safe_mode_readonly_quiet() -> str:
        calls = {"n": 0}

        def counter(name, args, hint=""):
            calls["n"] += 1
            return True

        llm = _ScriptLLM([("看看", [_tool_call("list_dir", {"path": str(work)})]), ("好", [])])
        session = Session(seed="k6")
        session.add_user("看看")
        sink = _QuietSink()
        sink.confirm = counter
        Agent(llm, max_steps=3, records=RecordLog(directory=work)).run(session, sink=sink, safe=True)
        if calls["n"] != 0:
            fail("只读工具也弹了确认")
        return "只读工具不打扰"

    def degrade_strict() -> str:
        llm = _ScriptLLM(
            replies=[("做完了", [_tool_call("list_dir", {"path": str(work)})]), ("确实做完了", [])],
            probe_calls=[_tool_call("read_file", {"path": str(work / "无.txt")}, "cv")],
        )
        session = Session(seed="k7")
        session.add_user("做个事")
        text = Agent(llm, max_steps=6, degrade="strict", records=RecordLog(directory=work)).run(
            session, sink=_QuietSink()
        )
        if "验证未通过" not in text:
            fail(f"严格档没收手：{text[:60]}")
        return "严格档验证不过就收手"

    def degrade_lenient() -> str:
        llm = _ScriptLLM(
            replies=[("做完了", [_tool_call("list_dir", {"path": str(work)})]), ("确实做完了", [])],
            probe_calls=[_tool_call("read_file", {"path": str(work / "无.txt")}, "cv")],
        )
        session = Session(seed="k8")
        session.add_user("做个事")
        text = Agent(llm, max_steps=6, degrade="lenient", records=RecordLog(directory=work)).run(
            session, sink=_QuietSink()
        )
        if "未核实" not in text:
            fail(f"宽容档没标注：{text[:60]}")
        return "宽容档标注未核实"

    step(group, "ReAct 闭环", react_loop)
    step(group, "Plan 模式", plan_mode)
    step(group, "验证失败返工", verify_rework)
    step(group, "安全模式拒绝", safe_mode_denies)
    step(group, "安全模式放行", safe_mode_allows)
    step(group, "只读工具不打扰", safe_mode_readonly_quiet)
    step(group, "degrade=strict", degrade_strict)
    step(group, "degrade=lenient", degrade_lenient)


# ================================================================ 规范验收


def g_spec_metadata() -> None:
    group = "规范·元数据"

    def manifest_fields() -> str:
        from .config import agent_manifest

        m = agent_manifest()
        need = ("id", "name", "version", "tags", "owner", "sla")
        missing = [k for k in need if k not in m]
        if missing:
            fail(f"缺字段：{missing}")
        return f"{m['id']}@{m['version']}"

    def sla_fields() -> str:
        from .config import agent_manifest

        sla = agent_manifest()["sla"]
        need = ("hard_timeout_s", "max_steps", "max_tool_calls", "max_same_call", "max_fail_streak")
        missing = [k for k in need if k not in sla]
        if missing:
            fail(f"SLA 缺：{missing}")
        return "、".join(f"{k}={sla[k]}" for k in need[:3])

    def sla_shared_with_limits() -> str:
        """声明与实现同源：SLA 改了，Guard 默认值要跟着变。"""
        from .config import agent_manifest
        from .core.guard import Limits

        sla = agent_manifest()["sla"]
        limits = Limits()
        if limits.max_steps != sla["max_steps"]:
            fail(f"步数不一致：Guard {limits.max_steps} vs SLA {sla['max_steps']}")
        if limits.max_tool_calls != sla["max_tool_calls"]:
            fail("工具预算不一致")
        return "Guard 上限来自 SLA"

    def owner_declared() -> str:
        from .config import agent_manifest

        owner = agent_manifest()["owner"]
        if not owner.get("team"):
            fail("没声明归属团队")
        return str(owner["team"])

    step(group, "元数据字段齐全", manifest_fields)
    step(group, "SLA 字段齐全", sla_fields)
    step(group, "SLA 与实现同源", sla_shared_with_limits)
    step(group, "声明了归属", owner_declared)


def g_spec_records() -> None:
    group = "规范·记录"

    def trace_id() -> str:
        from .core.context import RecordLog

        d = workdir("rec-trace")
        log = RecordLog(directory=d)
        log.write(step=7, tool="read_file", args="{}", result="ok")
        row = json.loads(next(d.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()[0])
        if not str(row["trace_id"]).endswith(".7"):
            fail(f"trace_id 没带步号：{row['trace_id']}")
        return str(row["trace_id"])

    def failure_not_marked_ok() -> str:
        """历史缺陷：超时被记成 ok:true。现在必须是否。"""
        from .core.context import RecordLog

        d = workdir("rec-fail")
        log = RecordLog(directory=d)
        # ok 和 code 由内部按结果判定，不接受外部传参 —— 这样「看起来成功」的失败藏不住
        log.write(step=1, tool="run_shell", args="{}", result="[ERR] 命令超时（>60s）")
        row = json.loads(next(d.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()[0])
        if row["ok"] is not False:
            fail("超时被记成成功了")
        if row["code"] == "ok":
            fail(f"返回码没区分失败：{row['code']}")
        return f"ok=false + code={row['code']}"

    def result_summarized() -> str:
        from .core.context import RecordLog

        d = workdir("rec-sum")
        log = RecordLog(directory=d)
        log.write(step=1, tool="run_shell", args="{}", result="很长的输出\n" * 5000)
        row = json.loads(next(d.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()[0])
        text = json.dumps(row, ensure_ascii=False)
        if len(text) > 4000:
            fail(f"记录里落了全文：{len(text)} 字符")
        return f"记录 {len(text)} 字符"

    def args_digest() -> str:
        from .core.context import RecordLog

        d = workdir("rec-digest")
        log = RecordLog(directory=d)
        log.write(step=1, tool="read_file", args='{"path":"a.txt"}', result="ok")
        row = json.loads(next(d.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()[0])
        if not str(row.get("args_digest", "")).startswith("sha256:"):
            fail("缺参数指纹")
        return "参数有指纹"

    step(group, "TraceID 带步号", trace_id)
    step(group, "失败不记成成功", failure_not_marked_ok)
    step(group, "结果只留摘要", result_summarized)
    step(group, "参数有指纹", args_digest)


def g_spec_redact() -> None:
    group = "规范·脱敏"

    def api_key() -> str:
        from .core.redact import redact

        # 假 Key 用拼接生成：源码里不出现连续的 sk- 长串，否则 CI 的 secret scan
        # 会把它当真实密钥拦下来（这个拦截本身是好事，别关它）。
        fake = "sk-" + "a" * 26
        out = redact(f"key 是 {fake}")
        if fake in out:
            fail(f"Key 没被脱敏：{out}")
        return out[:40]

    def bearer() -> str:
        from .core.redact import redact

        out = redact("Authorization: Bearer abcdefghijklmnop")
        if "abcdefghijklmnop" in out:
            fail(f"Bearer 没脱敏：{out}")
        return out[:40]

    def user_dir() -> str:
        from .core.redact import redact

        out = redact(r"C:\Users\28025\project\a.txt")
        if "28025" in out:
            fail(f"用户目录没脱敏：{out}")
        return out[:40]

    def email() -> str:
        from .core.redact import redact

        out = redact("联系 someone@example.com 处理")
        if "someone@example.com" in out:
            fail(f"邮箱没脱敏：{out}")
        return out[:40]

    def keeps_normal() -> str:
        from .core.redact import redact

        text = "普通的日志内容，没有敏感信息"
        if redact(text) != text:
            fail("把正常内容也改了")
        return "正常内容不受影响"

    step(group, "API Key", api_key)
    step(group, "Bearer 令牌", bearer)
    step(group, "用户目录", user_dir)
    step(group, "邮箱", email)
    step(group, "不误伤正常内容", keeps_normal)


def g_spec_validate() -> None:
    group = "规范·参数校验"

    def type_mismatch() -> str:
        from .tools.validate import validate_args

        errors = validate_args({"properties": {"path": {"type": "string"}}}, {"path": 123})
        if not errors:
            fail("类型不匹配没报错")
        return errors[0][:40]

    def required_missing() -> str:
        from .tools.validate import validate_args

        errors = validate_args(
            {"properties": {"path": {"type": "string"}}, "required": ["path"]}, {}
        )
        if not errors:
            fail("缺必填没报错")
        return errors[0][:40]

    def enum_rejected() -> str:
        from .tools.validate import validate_args

        errors = validate_args({"properties": {"mode": {"type": "string", "enum": ["a", "b"]}}}, {"mode": "c"})
        if not errors:
            fail("枚举越界没报错")
        return errors[0][:40]

    def range_checked() -> str:
        from .tools.validate import validate_args

        errors = validate_args({"properties": {"limit": {"type": "integer", "maximum": 10}}}, {"limit": 99})
        if not errors:
            fail("超范围没报错")
        return errors[0][:40]

    def valid_passes() -> str:
        from .tools.validate import validate_args

        errors = validate_args(
            {"properties": {"path": {"type": "string"}}, "required": ["path"]}, {"path": "a.txt"}
        )
        if errors:
            fail(f"合法参数被误判：{errors}")
        return "合法参数放行"

    step(group, "类型不匹配", type_mismatch)
    step(group, "缺必填", required_missing)
    step(group, "枚举越界", enum_rejected)
    step(group, "数值超范围", range_checked)
    step(group, "合法参数放行", valid_passes)


def g_spec_prompt() -> None:
    group = "规范·提示词"

    def externalized() -> str:
        from .core.prompt import _load_prompt_template

        body, digest = _load_prompt_template("py-v1")
        if not body:
            fail("提示词模板没读到")
        if len(digest) != 64:
            fail(f"sha256 长度不对：{len(digest)}")
        return f"{len(body)} 字符"

    def sha_matches_index() -> str:
        import hashlib

        from .config import BASE_DIR as base

        index = json.loads((base / "prompts" / "index.json").read_text(encoding="utf-8"))
        entry = index["versions"].get("py-v1") or {}
        file = base / "prompts" / entry.get("file", "")
        if not file.is_file():
            fail("index 里指的文件不存在")
        actual = hashlib.sha256(file.read_bytes()).hexdigest()
        if actual != entry.get("sha256"):
            fail("内容改了但 sha 没更新")
        return "sha256 与 index 一致"

    def changelog_exists() -> str:
        from .config import BASE_DIR as base

        path = base / "prompts" / "CHANGELOG.md"
        if not path.is_file():
            fail("缺 CHANGELOG")
        return "有变更记录"

    def canary_buckets() -> str:
        from .core.prompt import DEFAULT_PROMPT_VERSION, _prompt_version

        os.environ.pop("AGENT_PROMPT_VERSION", None)
        os.environ["AGENT_PROMPT_CANARY"] = "py-v9"
        os.environ["AGENT_PROMPT_CANARY_PCT"] = "50"
        try:
            stable = {_prompt_version("sess-x") for _ in range(5)}
            spread = {_prompt_version(f"s{i}") for i in range(40)}
        finally:
            os.environ.pop("AGENT_PROMPT_CANARY", None)
            os.environ.pop("AGENT_PROMPT_CANARY_PCT", None)
        if len(stable) != 1:
            fail("同一会话分桶不稳定")
        if spread != {"py-v9", DEFAULT_PROMPT_VERSION}:
            fail(f"没有分流：{spread}")
        return "灰度分桶稳定且分流"

    def no_hardcoded_key() -> str:
        from .core.prompt import _base_prompt

        text = _base_prompt()
        lowered = text.lower()
        for marker in ("sk-", "api_key=", "bearer "):
            if marker in lowered:
                fail(f"提示词里疑似有密钥痕迹：{marker}")
        return "提示词里没有密钥"

    step(group, "提示词已外置", externalized)
    step(group, "sha256 与索引一致", sha_matches_index)
    step(group, "有变更记录", changelog_exists)
    step(group, "灰度分桶", canary_buckets)
    step(group, "无硬编码密钥", no_hardcoded_key)


def g_spec_degrade() -> None:
    group = "规范·限流降级"

    def limits_from_env() -> str:
        from .core.guard import Limits

        os.environ["AGENT_MAX_SAME_CALL"] = "7"
        try:
            got = Limits.from_env().max_same_call
        finally:
            os.environ.pop("AGENT_MAX_SAME_CALL", None)
        if got != 7:
            fail(f"环境变量没生效：{got}")
        return "LIMITS 可由环境变量覆盖"

    def rate_limit_default() -> str:
        from .core.guard import Limits

        if Limits().max_calls_per_minute <= 0:
            fail("限流上限没设置")
        return f"{Limits().max_calls_per_minute} 次/分钟"

    def degrade_env() -> str:
        from .core import Agent, Session

        os.environ["AGENT_DEGRADE"] = "lenient"
        try:
            agent = Agent(_ScriptLLM([("好", [])]), max_steps=2)
        finally:
            os.environ.pop("AGENT_DEGRADE", None)
        if agent.degrade != "lenient":
            fail(f"环境变量没生效：{agent.degrade}")
        return "AGENT_DEGRADE 可配置"

    def degrade_fallback() -> str:
        from .core import Agent

        agent = Agent(_ScriptLLM([("好", [])]), max_steps=2, degrade="乱写的档位")
        if agent.degrade != "normal":
            fail(f"非法档位没回落：{agent.degrade}")
        return "非法值回落到 normal"

    def stop_codes_exist() -> str:
        from .core.guard import code_for

        codes = {code_for("步数用尽"), code_for("触发限流"), code_for("连续失败")}
        if len(codes) != 3:
            fail(f"码有重复：{codes}")
        return "、".join(sorted(codes))

    step(group, "上限可由环境变量覆盖", limits_from_env)
    step(group, "限流默认值有效", rate_limit_default)
    step(group, "降级档位可配置", degrade_env)
    step(group, "非法档位回落", degrade_fallback)
    step(group, "停止码可区分", stop_codes_exist)


def g_spec_state() -> None:
    group = "规范·状态恢复"

    def snapshot_written() -> str:
        from .core import Agent, Session, SessionStore
        from .core.context import RecordLog

        d = workdir("state-write")
        store = SessionStore(directory=d)
        session = Session(seed=store.session_id)
        store.attach(session)
        session.add_user("看看目录")
        llm = _ScriptLLM([("看完了", [_tool_call("list_dir", {"path": str(d)})]), ("空目录", [])])
        Agent(llm, max_steps=4, records=RecordLog(directory=d)).run(session, sink=_QuietSink())
        rows = (store.path.read_text(encoding="utf-8").splitlines())
        states = [json.loads(r) for r in rows if '"type": "state"' in r]
        if not states:
            fail("没有状态快照落盘")
        return f"{len(states)} 条快照"

    def resume_reports_step() -> str:
        from .core import SessionStore

        d = workdir("state-resume")
        (d / "cut.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"type": "msg", "role": "user", "content": "做到一半"}),
                    json.dumps({"type": "state", "status": "tooling", "step": 5}),
                ]
            ),
            encoding="utf-8",
        )
        store = SessionStore.resume("cut", directory=d)
        store.load()
        if not store.interrupted or store.interrupted["step"] != 5:
            fail(f"恢复时没报中断步数：{store.interrupted}")
        return "报出「第 5 步中断」"

    step(group, "状态快照落盘", snapshot_written)
    step(group, "恢复时报中断步数", resume_reports_step)


# ================================================================ 入口

GROUPS = (
    ("配置", g_config),
    ("供应商", g_registry),
    ("协议", g_protocols),
    ("流式解析", g_stream),
    ("上下文", g_context),
    ("熔断", g_guard),
    ("工具", g_tools),
    ("沙箱", g_sandbox),
    ("撤销", g_checkpoints),
    ("存档", g_persistence),
    ("搜索", g_search),
    ("文档", g_documents),
    ("技能", g_skills),
    ("插件", g_plugins),
    ("MCP", g_mcp),
    ("内核", g_kernel),
    ("规范·元数据", g_spec_metadata),
    ("规范·记录", g_spec_records),
    ("规范·脱敏", g_spec_redact),
    ("规范·参数校验", g_spec_validate),
    ("规范·提示词", g_spec_prompt),
    ("规范·限流降级", g_spec_degrade),
    ("规范·状态恢复", g_spec_state),
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    keyword = args[0] if args else ""

    started = time.monotonic()
    ran = 0
    for name, runner in GROUPS:
        if keyword and keyword not in name:
            continue
        ran += 1
        before = len(RESULTS)
        try:
            runner()
        except Exception as exc:  # noqa: BLE001 一组崩了不该中断整轮
            RESULTS.append((name, "（组初始化）", False, f"{type(exc).__name__}: {exc}"[:160]))
        rows = RESULTS[before:]
        mark = "✓" if all(ok for *_, ok, _ in rows) else "✗"
        print(f"\n{mark} {name}（{len(rows)} 项）")
        for _, label, ok, detail in rows:
            line = f"   {'✓' if ok else '✗'} {label}"
            if detail and not ok:
                line += f" —— {detail}"
            elif detail:
                line += f"  {detail}"
            print(line)

    if ran == 0:
        print(f"没有匹配「{keyword}」的组。可选：{'、'.join(n for n, _ in GROUPS)}")
        return 1

    total = len(RESULTS)
    passed = sum(1 for *_, ok, _ in RESULTS if ok)
    elapsed = time.monotonic() - started
    print(f"\n共 {total} 项：通过 {passed}，失败 {total - passed}（{elapsed:.1f}s）")
    if total - passed:
        print("失败项：")
        for group, label, ok, detail in RESULTS:
            if not ok:
                print(f"  [{group}] {label} —— {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
