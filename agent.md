# AGENTS.md

给在本仓库工作的编码 Agent。人类看 `README.md`，文档规范看 `docs/doc-style-guide.md`。

实现在 `miniagent/`，是一个零第三方依赖的 Python 项目（只用标准库，Python 3.11+）。
所有命令都在仓库根执行。

## First Message

用户第一条消息没给具体任务时，先读 `README.md`，再问要动哪一块。不要自己挑一块开工。
改代码前先跑 `python -m miniagent.offline`，确认基线是绿的。

## Architecture

六层，依赖单向。反了就重写，别加注释解释为什么例外。

```
ui → core → tools → (skills, plugins, mcp) → config
model 独立，只依赖 config / wire
markers.py、tokens.py 是叶子：谁都能 import，它们不 import 任何内部模块
```

| 层 | 目录 | 硬约束 |
| --- | --- | --- |
| ui | `miniagent/ui/` · `gui.py` | 不写业务逻辑，不直接调模型 API；只做事件渲染与交互 |
| cli | `miniagent/ui/console.py` · `agent.py` | 只做参数解析、终端渲染、交互 |
| core | `miniagent/core/` | 不 `print`（一律走 `Sink` 接口），不 import ui |
| model | `miniagent/model/` | 不 import core / tools；新供应商优先加 `registry.py` 一行，协议不同才写适配器 |
| tools | `miniagent/tools/` | 不知道谁在调用它；除白名单外不自己判权限；**永不抛异常** |
| ext | `miniagent/skills/` · `miniagent/plugins/` · `miniagent/mcp/` | 只发现与调用，不持有业务状态；坏插件 / 坏 server 必须跳过而不是拖垮启动 |
| config | `miniagent/config.py` | 唯一读 `os.environ` 与 `.env` 的地方；**默认不带任何密钥** |

## 三条不能破的约定

1. **工具永不抛异常**：失败一律转成 `[ERR]` 文本 + `code` 回灌，让模型自己换做法。
2. **历史单口写入**：所有消息走 `Session.append()`，持久化、记录、计数都挂它上面；旁路写入等于漏记。
3. **落盘前脱敏**：`records` 与会话存档写盘前必须过 `redact()`；只在展示层脱敏不算数。

## 改代码的固定动作

```bash
python -m miniagent.offline                                   # 110 项功能自测（真工具、真子进程、真文件）
for f in tests/test_*.py; do python "$f" || echo "FAIL $f"; done   # 25 个单元套件
```

- 改 `core/`：至少跑 offline 的「内核」「熔断」「上下文」组 + 相关单测
- 改 `tools/`：跑 `python -m miniagent.offline 沙箱` / `... 工具`
- 改提示词：更新 `prompts/python.system.vN.md`，同步 `prompts/index.json` 的 sha256 与 `CHANGELOG.md`，
  否则离线自测会报「sha256 与 index 不一致」
- 改 `manifest.json`：熔断上限与工具预算会自动跟着变（Guard 从 SLA 读），不用改代码
- 加工具：在 `miniagent/tools/xxx.py` 写函数 + `SCHEMAS`，在 `tools/__init__.py` 的 `DISPATCH` 注册；
  只读工具记得加进 `TOOL_TIMEOUTS`（超时）与 `x-idempotent`（失败重试）
- 加供应商：先看 `model/registry.py` 能不能一行搞定；需要新协议才写 `protocols/`

## 别做的事

- 别引入第三方运行时依赖。标准库能做的就自己做（文档解析、MCP 协议都是这么来的）。
- 别在 `core/` 里 `print`——事件必须走 `Sink`，否则 GUI 收不到。
- 别把测试写成依赖网络或真实 API Key 的形式。
- 别在 README 里写没有对应测试输出的数字。
- 别在代码里另写一份熔断上限——只从 `manifest.json` 读。

## 界面截图怎么更新

README 顶部那张图是图形界面的真实截图，不是设计稿：

```bash
python gui.py          # 打开窗口
# 输一条会产生工具调用的任务，等它答完，用系统截图工具截窗口：
#   Win + Shift + S  框选窗口 → 存成 docs_gui_preview.png
```

改界面后重截一次，别让图比代码旧。
