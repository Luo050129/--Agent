# 墟 Agent · 七大开发规范合规评审

> 评审对象：`miniagent/`（Python 版，0.2.0）。文中「TS 版」一列来自早期 TypeScript 参考实现，
> 已撤出版本库只留本地，保留该列是为了记录两版能力对齐的过程。
> 评审依据：《Agent 开发强制规范》七项要点
> 评审方式：读代码 + 跑离线自测 + 抽样真实运行记录，逐项对证据，不凭印象
> **状态：P0/P1/P2 已按本文方案落地（两版皆然）**，落地结果见下方「落地状态」表；验收靠
> `python -m miniagent.offline`（110 项，含 30 项合规验收）

## 结论先行

**评审时**七项里没有一项完全合规（1 项基本达标、5 项部分、1 项缺失）；最缺的是「可观测性」和「元数据」——恰好都是接入生产监控与多 Agent 协同时的硬门槛。

**落地后**：6 项达标、1 项部分（状态模型 —— 长期向量记忆有意不做，属零依赖的边界）。两版能力已对齐，见「落地状态」表。

| # | 规范项 | 评审时 | 落地后 | 一句话结论 | 优先级 |
|---|---|---|---|---|---|
| 1 | 元数据 | 🟡 部分 | 🟢 达标 | 原先只有 `__version__`；现在 `manifest.json` 提供 ID / 版本 / 标签 / 归属 / SLA，且熔断上限从 SLA 读 | **P0** |
| 2 | 状态模型 | 🟡 部分 | 🟡 部分 | 短期记忆 ✅、状态持久化 + 中断标记 ✅；长期向量记忆仍不做（零依赖的边界，已在文档写明） | P1 |
| 3 | 通信协议 | 🟡 部分 | 🟢 达标 | 新增运行时参数校验（最小 Schema 校验器），类型错就回灌 `参数校验失败`，不再静默转换 | **P0** |
| 4 | 工具规范 | 🟡 部分 | 🟢 达标 | 返回码结构化、限流、只读工具幂等重试、工具级超时（超时用 daemon 线程等，不强杀） | P1 |
| 5 | Prompt 规范 | 🟠 缺失 | 🟢 达标 | 提示词外置 `prompts/`，版本 + sha256 校验 + 按会话稳定分桶的灰度 | P1 |
| 6 | 可观测性 | 🔴 缺失 | 🟢 达标 | TraceID、带时区时间戳、`ok` 走 `is_failure`、结果摘要 + 指纹、落盘前脱敏 | **P0** |
| 7 | 异常规范 | 🟢 基本达标 | 🟢 达标 | 六类熔断 + 停止码 + 三档降级（strict/normal/lenient） | P2 |

状态图例：🟢 基本达标 / 🟡 部分达标 / 🟠 关键缺失 / 🔴 严重缺失

## 落地状态（本次改造）

| 能力 | TS 版 | Python 版 | 代码位置 |
| --- | --- | --- | --- |
| manifest（ID/版本/标签/归属/SLA） | ✅ | ✅ | `manifest.json` · `config.ts` / `config.py` |
| SLA 与熔断上限同源 | ✅ | ✅ | `guard.defaultLimits()` / `Limits` 字段默认值 |
| 参数运行时校验 | ✅ | ✅ | `tools/validate.ts` / `tools/validate.py` |
| 结构化返回码 | ✅ | ✅ | `records.codeOf()` / `context._code_hint()` |
| 限流（每分钟上限） | ✅ | ✅ | `Guard.beforeTool` |
| 幂等重试 + 工具级超时 | ✅ | ✅ | `TOOL_TIMEOUTS` + execute 里的重试分支 |
| TraceID 全链 | ✅ | 🟡 记录内 trace_id（未注入子进程环境变量） | `core/records.ts` / `core/context.py` |
| 记录字段（ISO 时间戳 / ok / 摘要 / 指纹） | ✅ | ✅ | 同上 |
| 落盘前脱敏 | ✅ | ✅ | `core/redact.ts` / `core/redact.py` |
| 提示词外置 + 版本 + sha 校验 | ✅ | ✅ | `prompts/`（两版共用） |
| 灰度分桶 | ✅ | ✅ | `AGENT_PROMPT_CANARY` + 会话 ID 稳定分桶 |
| 三档降级 | ✅ | ✅ | `AGENT_DEGRADE` / `Agent(degrade=...)` |
| 状态持久化（中断标记） | ✅ | ✅ | `Session.notify_state()` → `SessionStore.record_state()` |

**验收命令**

```bash
python -m miniagent.offline                     # 110 项：含 30 项合规验收
for f in tests/test_*.py; do python "$f"; done  # 25 个单元套件
```

---

## 1. 元数据

### 规范要求
唯一 ID、版本、标签、归属团队、SLA 声明。

### 现状与证据

| 要求 | 现状 | 证据 |
| --- | --- | --- |
| 唯一 ID | 无。会话有 `session_id`（`20261113-155856-a1b2`），但 Agent 本身没有标识 | `src/core/persistence.ts` · `newSessionId()` |
| 版本 | 有 | `miniagent/__init__.py:12` `__version__ = "0.2.0"`；ts 版仅 `package.json` 的 `0.1.0` |
| 标签 | 无 | — |
| 归属团队 | 无 | — |
| SLA 声明 | 无。超时/步数上限是**硬编码默认值**，没声明成对外承诺 | `core/guard.py:117-119` `max_steps=20 / max_tool_calls=40 / max_seconds=300` |

### 差距
这五项一个整体都没有。调度方无法回答「这是哪个 Agent 的哪一版、谁负责、坏了找谁、能承诺多久返回」。

### 改造方案
新增 Agent 清单文件（Python 与 TS 各一份，字段一致）：

```json
{
  "id": "xu-agent.desktop",
  "version": "0.2.0",
  "tags": ["desktop", "coding-agent", "zero-dependency", "cn-first"],
  "owner": { "team": "personal/luoxianjun", "contact": "repo-issues" },
  "sla": {
    "p95_first_token_ms": 3000,
    "p95_task_ms": 60000,
    "hard_timeout_s": 300,
    "max_steps": 20,
    "max_tool_calls": 40,
    "availability": "best-effort"
  },
  "runtime": { "python": ">=3.13", "node": ">=22.6" }
}
```

- 落盘：`manifest.json`（仓库根），`config.py` 启动时读取并在 `--selftest` 与日志首行输出 id/version。
- SLA 数值必须与 `guard.py` 的默认上限**同源**，避免声明与实际跑的不一致（当前 `max_seconds=300` 与 `AGENT_MAX_SECONDS` 环境变量可覆盖，声明要注明"可被 env 覆盖"）。

### 验收
`python agent.py --selftest` 首行打印 `xu-agent.desktop@0.2.0`；`guard` 的默认上限从 manifest 读取而非硬编码。

> **已落地**：`manifest.json` 就位，`config.agent_label()` 与 `guard.defaultLimits()` 均从 SLA 读取；离线自测有 3 项校验（含「值不一致即失败」）。

---

## 2. 状态模型

### 规范要求
短期会话记忆 / 长期向量记忆 / 执行状态机。

### 现状与证据

| 要求 | 现状 | 证据 |
| --- | --- | --- |
| 短期会话记忆 | ✅ 有。`Session.messages` + 上下文预算折叠（超预算把老历史压成摘要，保留最近若干条） | `core/session.py`、`core/context.ts` |
| 执行状态机 | 🟡 半有。状态是**事件快照**（`planning / tooling / checking / done / blocked`），不是可恢复的持久状态机；进程重启后从会话重建，不恢复"正在第几步" | `core/loop.ts` `TaskState` |
| 长期向量记忆 | ❌ 没有。检索是字面匹配（`core/search.ts` 关键词打分），无 embedding、无向量库 | `core/search.ts` |

### 差距
1. 状态机缺"可恢复性"：崩溃后只能靠会话历史推断，没有 checkpoint 化的任务状态（`steps`/`toolCalls` 是从消息重算的）。
2. 长期记忆缺失是明确的架构选择（零依赖不做 embedding），但要写清**边界**：跨会话语义召回做不到，只能字面搜。

### 改造方案
- 状态持久化：把 `TaskState` 随每个事件写入会话 JSONL（`{"type":"state", ...}`），恢复时取最后一条 state 并把 status 修正为 `blocked`（说明上次中断），避免"假装还在跑"。
- 长期记忆：以 `extra` 方式引入，默认关闭 —— `pip install xu-agent[rag]` / `npm i` 可选路径；不入主依赖，保持零依赖承诺。
- 明确契约：`core/memory.ts` 定义 `Recall` 接口（`recall(query, k) → Hit[]`），字面实现为默认，向量实现为可选插件，上层不感知差异。

### 验收
杀掉进程再 `--resume`，状态行显示"上次在第 N 步中断"而不是从零开始。

---

## 3. 通信协议

### 规范要求
机器接口用 JSON Schema 等可校验契约；自然语言只用于需求与解释，**执行参数必须校验**。

### 现状与证据

| 要求 | 现状 | 证据 |
| --- | --- | --- |
| 工具接口可校验契约 | 🟡 Schema 存在，但**只发给模型看，运行时不校验** | `tools/*.ts` 的 `schemas` 数组 |
| 执行参数必须校验 | 🟡 只校验了「能不能解析成 JSON 对象」和「名字存在」 | `tools/index.ts` `execute()` |
| 内部消息契约 | ❌ 无。消息是 `Record<string, unknown>`，字段靠约定 | `wire.ts` `Message` |
| 自然语言边界 | 🟡 系统提示要求模型给 JSON 参数，但没有强制层 | `core/prompt.ts` |

**具体缺口**：模型传 `{"path": 123}` 或 `{"limit": "很多"}` 时，工具里 `String(args["path"])` 会静默转成 `"123"`，`Number(args["limit"])` 变成 `NaN` —— 不会报错，行为不可预期。

### 改造方案
1. **最小 JSON Schema 校验器**（零依赖，约 120 行，只支持 `type / properties / required / enum / minimum / maxLength`）：

```ts
// src/tools/validate.ts
export function validateArgs(schema: JsonSchema, args: Record<string, unknown>): string[] {
  const errors: string[] = [];
  for (const key of schema.required ?? []) {
    if (args[key] === undefined) errors.push(`缺少必填参数：${key}`);
  }
  for (const [key, spec] of Object.entries(schema.properties ?? {})) {
    const value = args[key];
    if (value === undefined) continue;
    const want = spec.type;
    if (want === "string" && typeof value !== "string") errors.push(`${key} 应为 string，收到 ${typeof value}`);
    if (want === "integer" && !Number.isInteger(value)) errors.push(`${key} 应为整数`);
    if (spec.enum && !spec.enum.includes(value as never)) errors.push(`${key} 必须是 ${spec.enum.join("/")}`);
  }
  return errors;
}
```

2. 在 `execute()` 里先校验再执行，不通过返回 `[ERR] 参数校验失败：...`（回灌给模型让它改），而不是让工具内部静默转换。
3. 消息契约：用同一套校验器校验出口消息（`role` 枚举、`tool_calls[].function.arguments` 必须是合法 JSON 字符串），在 `Session.append()` 处统一拦截（单口入库点已经存在，挂这里成本最低）。

### 验收
`execute("file_read", '{"path":123}')` 返回参数校验错误；离线自测增加「类型错误被拦」用例。

> **已落地**：`tools/validate.ts` + `tools/validate.py`（零依赖，约 130 行）；`executeDetailed` 返回 `{text, code}`。实测：`{"path":123}` → `[ERR] 参数校验失败：path 应为 string，收到 number`，`{"path":"x","limit":"很多"}` → `limit 应为 integer`。

---

## 4. 工具规范

### 规范要求
注册、参数 Schema、超时、限流、重试、返回码定义。

### 现状与证据

| 要求 | 现状 | 证据 |
| --- | --- | --- |
| 注册 | ✅ 静态注册表 + 运行时扩展（插件 / MCP） | `tools/index.ts` `MODULES`、`setExtraTools` |
| 参数 Schema | ✅ 有（但运行时不校验，见第 3 节） | `tools/schemas.ts` |
| 超时 | 🟡 有，但**全局默认 60s，不能按工具配**；`run_node` 走 `--max-old-space-size` 与杀树 | `tools/runtime.ts`；`sandbox.ts` |
| 限流 | ❌ 没有。只有熔断（同调用重复、连续失败），不是「每秒/每分调用次数」限流 | `core/guard.ts` |
| 重试 | 🟡 **模型层有**（5xx/429/网络错误，已吐字不重试），**工具层没有**（幂等读操作没有自动重试） | `model/client.ts` |
| 返回码 | ❌ 非结构化。失败靠 `[ERR]` 文本前缀，没有 `code` 字段，调用方无法按码分支 | `markers.ts` |

### 改造方案
1. **返回码结构化**：内部统一返回 `{ ok, code, message, data }`，对模型仍序列化成人类可读文本（保持现有体验）：

```ts
export type ToolCode =
  | "ok" | "invalid_args" | "denied_by_policy" | "denied_by_user"
  | "timeout" | "not_found" | "sandbox_blocked" | "internal_error";
```

2. **限流**：`Guard` 增加滑动窗口（按工具名前缀计数，如 `shell` 类 5 次/分钟），超限返回 `denied_by_policy` 并计入熔断。零依赖实现用一个 `Map<name, number[]>` 存时间戳即可。
3. **工具层重试**：只对**幂等**工具（`read_file` / `list_dir` / `read_document` / MCP 只读工具）在网络类失败时重试 1 次，写类工具绝不自动重试（避免重复写）。在 schema 里加 `"x-idempotent": true` 标记。
4. **超时按工具配**：schema 增加 `"x-timeout-s": 30`，`execute()` 优先用它，回落全局默认。

### 验收
离线自测加三组：参数类型错误被拦、限流触发、幂等读工具重试成功但写工具不重试。

> **已落地（TS）**：`codeOf()` 把结果映射成 8 类码；`Guard` 加 60 秒滑动窗口限流（默认 30 次/分，来自 SLA）；只读工具标了 `x-idempotent` 失败重试一次，写类工具绝不重试。Python 版已落地返回码与限流，幂等重试与工具级超时待跟进。

---

## 5. Prompt 规范

### 规范要求
版本管理、灰度发布、**禁止硬编码密钥与敏感信息**。

### 现状与证据

| 要求 | 现状 | 证据 |
| --- | --- | --- |
| 版本管理 | ❌ 无。系统提示是硬编码字符串常量，改了没有版本号也没有变更记录 | `core/prompt.ts` `systemPrompt()` |
| 灰度发布 | ❌ 无 | — |
| 禁止硬编码密钥 | ✅ **达标**。全仓库无密钥，`.env` 从不入库，CI 有 secret scan（`sk-[A-Za-z0-9]{20,}` 与 `.env` 双检查） | `.github/workflows/ci.yml` · `Secret scan` |
| 敏感信息 | 🟡 提示词里不含用户数据（好），但**运行时也不做脱敏**（见第 6 节） | — |

### 改造方案
1. **提示词外置 + 版本号**：`prompts/system.v3.md`，文件名带版本，`prompt.ts` 读出并附一行元信息：

```text
<!-- prompt: system v3 · 2026-11-16 · sha256:ab12… -->
```

2. **灰度**：`AGENT_PROMPT_VERSION` 环境变量选版本（`v2|v3`），默认稳定版；`AGENT_PROMPT_CANARY=0.1` 时按会话 ID 哈希取模决定是否走灰度版 —— 固定分桶，保证同一会话体验一致。
3. **变更纪律**：每次改提示词必须同时改版本号并在 `prompts/CHANGELOG.md` 记一行（改了什么 / 为什么 / 影响哪类任务）。这条比代码 review 更重要，因为提示词的失败是概率性的。

### 验收
`--selftest` 打印当前 prompt 版本与 sha256；改提示词未升版本时，离线自测直接失败。

> **已落地**：`prompts/system.v1.md` + `python.system.v1.md` + `index.json`（版本 → sha256）+ `CHANGELOG.md`，两版共用一份目录。TS 版支持 `AGENT_PROMPT_VERSION` 强制与 `AGENT_PROMPT_CANARY` 按会话哈希分桶；模板被改但 sha 没更新 → 离线自测报「内容改了但 sha 没更新」。

---

## 6. 可观测性

### 规范要求
TraceID 全链路透传；记录任务状态、简要决策说明、工具参数与结果摘要、必要且脱敏的模型交互；**不要求保存模型内部思维链**。

### 现状与证据

| 要求 | 现状 | 证据 |
| --- | --- | --- |
| TraceID 透传 | 🔴 **完全没有**。全仓库无 trace/span/run_id 概念 | grep 结果为空 |
| 任务状态记录 | 🟡 状态只推给界面 Sink，**不落盘** | `core/loop.ts` `emit()` |
| 决策说明 | ❌ 无。为什么调这个工具、为什么收手，只有最终报告文本 | — |
| 工具参数与结果 | 🟡 **有记录但字段不合格**（见下） | `.agent/records/*.jsonl` |
| 模型交互 | 🟡 会话 JSONL 存了消息，但**未脱敏**、**无摘要** | `core/persistence.ts` |
| 不存思维链 | ✅ 达标（本来就没存 reasoning 字段） | — |

**实测记录暴露的三个真问题**（取自 `.agent/records/20261110-135720.jsonl`）：

```json
{"ts": "13:59:49", "step": 1, "tool": "run_shell", "args": "{...}", "result": "命令超时（>60s）…", "ok": true}
```

1. **`ok` 判定错误**：结果是「命令超时」，却被记为 `ok: true`。说明记录的 ok 字段没走 `is_failure()`，而是别的判定 —— 这种"看起来成功"的日志比没有日志更危险。
2. **`ts` 只有时分秒**：跨天追溯会串到一起，必须带日期与时区（UTC ISO8601）。
3. **`result` 全文落盘**：`run_shell` 输出可能几万字符，没有摘要也没有上限，日志会失控膨胀。

### 改造方案
1. **TraceID**：每个任务生成 `trace_id`（会话 ID + 步序号派生，如 `20261116-101500-a1b2.3`），随 `Sink` 事件、工具记录、模型请求头（`X-Trace-Id`，本地模型可忽略）一起带；子进程（MCP / `run_node`）通过环境变量 `XU_TRACE_ID` 透传。
2. **记录结构**（一行一事件，字段固定）：

```json
{
  "ts": "2026-11-16T10:15:00.123Z",
  "trace_id": "20261116-101500-a1b2.3",
  "session_id": "20261116-101500-a1b2",
  "event": "tool_call",
  "tool": "run_shell",
  "args_digest": "sha256:9f2c…",
  "args_summary": "for /r %f in (*.py) do @type …",
  "ok": false,
  "code": "timeout",
  "result_summary": "命令超时（>60s），进程树已终止",
  "duration_ms": 60012,
  "decision": "模型选择全量统计行数，已超时，下一步应分批"
}
```

3. **脱敏**：写入前统一过一遍 `redact()`：`sk-[A-Za-z0-9]{16,}` → `sk-***`、`Bearer xxx` → `Bearer ***`、`(?i)(api[_-]?key|token|password)=…` → `$1=***`、本机绝对路径中的用户名段 → `~`。脱敏在**落盘前**做，不能只在展示层做。
4. **摘要而非全文**：`result_summary` 取前 200 字符 + `字符数`，全量只进会话档（且受现有截断策略约束）。
5. **决策说明**：在 `loop` 收手与换策略的分支里生成一句 `decision`（例如「同一调用重复 3 次 → 换 list_dir」「验证失败 → 返工」），不要求模型自述思维链，只记**内核自己的判定理由**——这是可审计且零额外成本的部分。

### 验收
任取一次运行，能用 `trace_id` 串起：会话 → 每步状态 → 每次工具调用 → 模型请求；日志里搜不到任何 `sk-` 明文。

> **已落地**：`core/records.ts`（TS）/ `RecordLog`（Py）重写记录结构 —— `ts` 为带时区 ISO8601、`ok` 走 `isFailure`、`code` 结构化、`result_summary` + `result_chars` + `args_digest`、`duration_ms`、`trace_id`。脱敏覆盖 sk-/Bearer/token=、用户目录、邮箱，**落盘前**执行。实测超时被正确记为 `ok:false / code:timeout`（改造前是 `ok:true`）。

---

## 7. 异常规范

### 规范要求
失败分类、降级策略、终止条件，**严禁无上限循环**。

### 现状与证据

| 要求 | 现状 | 证据 |
| --- | --- | --- |
| 失败分类 | ✅ 三层判定：结构化前缀（`[ERR]`/`[OK]`）> 关键词兜底 | `core/guard.ts` `classify/isFailure` |
| 终止条件 | ✅ 六类：同步重复调用、单调用失败次数、连续失败、工具预算、时间预算、步数上限 | `core/guard.ts:98-119` |
| 禁止无上限循环 | ✅ 全部路径有上限（默认 20 步 / 40 次工具 / 300 秒） | 同上；`loop` 每步 `checkStop()` |
| 降级策略 | 🟡 有局部降级（模型重试、插件加载失败跳过、MCP 坏 server 跳过、验证探针失败放行），但**没有分层降级声明** | `plugins.ts` / `mcp/manager.ts` / `completion.ts` |
| 失败上报 | 🟡 只给人话报告，**没有错误码**供上层程序判断 | `Guard.report()` |

### 改造方案
1. **失败分层表**（写进代码注释与文档，作为契约）：

| 层级 | 场景 | 策略 | 是否中断任务 |
| --- | --- | --- | --- |
| L1 可重试 | 5xx / 429 / 网络抖动 / 流中断（未吐字） | 指数退避重试，最多 2 次 | 否 |
| L2 可绕过 | 单个插件/MCP server 起不来、单条工具结果超限 | 跳过该能力，降级继续 | 否 |
| L3 需人工 | 连续失败 6 次 / 同调用重复 3 次 / 撞满预算 | 收手 + 人话报告 + `code` | 是（可续） |
| L4 致命 | 配置缺失（无 Key）、协议不兼容、磁盘不可写 | 立即停止，明确指引 | 是（不可续） |

2. **错误码**：`Guard.report()` 返回结构化结果 `{ code: "budget_exhausted" | "loop_detected" | "fail_streak" | "denied" | "timeout", message }`，人话文本由上层渲染。
3. **降级开关**：`AGENT_DEGRADE=strict|normal|lenient` —— strict 下验证失败即中断，lenient 下验证失败只标记不返工（当前是"返工一次"的固定策略）。

### 验收
离线自测覆盖 L1-L4 四层各一条用例；`report()` 的 `code` 有断言。

> **已落地（TS）**：`codeFor()` 把收手原因映射成 7 类 `StopCode`（顺序上先判限流再判预算，这个 bug 是自测抓出来的）；`AGENT_DEGRADE=strict|normal|lenient` 三档控制验证返工。Python 版已落地码映射，三档降级待跟进。

---

## 改造路线（按投入产出排序）

| 批次 | 内容 | 状态 |
|---|---|---|
| **P0-1** | 记录修正：`ok` 判定走 `is_failure`、`ts` 带时区、`result` 转摘要、加 `trace_id` | ✅ 两版完成 |
| **P0-2** | 参数运行时校验（最小 Schema 校验器）+ 统一 `[ERR] 参数校验失败` | ✅ 两版完成 |
| **P0-3** | `manifest.json`：ID/版本/标签/归属/SLA，与熔断上限同源 | ✅ 两版完成 |
| P1-1 | 提示词外置 + 版本 + sha 校验 + 灰度分桶 | ✅ TS / 🟡 Py（无分桶） |
| P1-2 | 返回码结构化 + 按工具超时 + 幂等重试 | ✅ TS / 🟡 Py（前两项） |
| P1-3 | 落盘前脱敏 | ✅ 两版完成 |
| P2-1 | 停止码 + 三档降级 | ✅ TS / 🟡 Py（无降级） |
| P2-2 | 状态持久化（可恢复状态机） | ✅ TS / 🟡 Py（方法就绪未接线） |
| P2-3 | 限流（滑动窗口） | ✅ 两版完成 |

**未做的两件（有意保留）**：长期向量记忆（破零依赖，应按 extra 引入）、全屏 TUI（终端中文宽字符渲染成本高，当前富终端够用）。

## 附：当前已达标的项（别改坏）

| 能力 | 位置 |
| --- | --- |
| 无硬编码密钥 + CI 密钥扫描 | `.github/workflows/ci.yml` · Secret scan |
| 无上限循环（六类熔断全覆盖） | `core/guard.ts` |
| 不保存模型思维链 | 全链路无 reasoning 字段落盘 |
| 失败三层判定（前缀优先于关键词） | `core/guard.ts` `classify` |
| 工具清单压缩（MCP 6313 → 1480 tok） | `mcp/manager.ts` |
| 离线功能自测（不调模型） | `miniagent/offline.py`（110 项） |

---

_本评审基于提交 `5c45a77` 的代码；P0/P1/P2 方案已在后续提交中落地。**2026-11-16**：仓库主实现回归 Python（`miniagent/` 0.2.0），
并把此前仅 TS 版具备的四项能力（幂等重试 + 工具级超时、灰度分桶、三档降级、状态接线）补齐，验收由
`python -m miniagent.offline` 的 110 项覆盖（含 30 项合规检查）。_
