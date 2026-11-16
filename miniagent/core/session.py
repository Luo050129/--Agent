"""核心层：会话状态。

消息历史 + 统计信息都放这里，界面只读不写。

「插话」也在这儿：用户在一轮任务跑到一半时想改方向，界面把话交给 interject()，
内核在下一个安全点（每次模型请求前）取走转成消息。不直接改 messages，
免得插在 assistant.tool_calls 和它的 tool 结果之间，把配对搞断。

消息入库只有一个口：append()。所有历史写入（用户输入、模型回复、工具结果、
中途插话）都必须走它，持久化观察者才不会漏记。直接摸 messages 列表的只有
加载存档和改系统提示词这两件事。
"""

from __future__ import annotations

import threading

from .prompt import system_prompt

# 插话队列上限。超过就不收了 —— 攒十几条一次性塞给模型，等于把任务重写一遍，
# 还不如让用户停下来重说。挤掉的是最旧的一条。
MAX_INTERJECTS = 5


class Session:
    def __init__(self, system: str | None = None, seed: str = "") -> None:
        self._auto_system = system is None  # 用的是自动生成的提示词，可以刷新
        self._seed = seed  # 灰度分桶种子（一般就是会话 ID）
        self.messages: list[dict] = [{"role": "system", "content": system or system_prompt(seed=seed)}]
        self.steps = 0  # 已完成的工具步数
        self.tool_calls = 0  # 累计工具调用次数
        self._interjects: list[str] = []
        self._lock = threading.Lock()  # 界面线程写，内核线程取
        self._observer = None  # 持久化观察者：每条入库的消息回调一次
        self._state_observer = None  # 状态快照观察者：存档用它记「停在第几步」

    # ---------------------------------------------------------- 消息入库

    def append(self, msg: dict) -> None:
        """统一入库口。历史写入走这里，观察者（持久化）才不漏不重。

        观察者抛异常就摘掉它 —— 存档坏了不能拖累对话本身。
        """
        self.messages.append(msg)
        observer = self._observer
        if observer is None:
            return
        try:
            observer(msg)
        except Exception:  # noqa: BLE001
            self._observer = None

    def attach_observer(self, observer) -> None:
        self._observer = observer

    def detach_observer(self) -> None:
        self._observer = None

    def attach_state_observer(self, observer) -> None:
        """状态快照观察者。存档用它把「第几步、什么状态」写进会话档。"""
        self._state_observer = observer

    def notify_state(self, snapshot: dict) -> None:
        """把状态快照交给观察者。观察者坏了就摘掉，不影响对话。"""
        observer = self._state_observer
        if observer is None:
            return
        try:
            observer(snapshot)
        except Exception:  # noqa: BLE001
            self._state_observer = None

    def add_user(self, text: str) -> None:
        self.append({"role": "user", "content": text})

    def refresh_system(self, seed: str | None = None) -> None:
        """重算系统提示词（MCP 工具清单变了、或拿到会话 ID 之后）。

        自定义提示词的会话不动。传 seed 会顺带更新灰度分桶种子 ——
        会话 ID 是存档建好之后才有的，所以这条路径必须支持后补种子。
        """
        if not self._auto_system:
            return
        if seed is not None:
            self._seed = seed
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = {"role": "system", "content": system_prompt(seed=self._seed)}

    def reset(self, system: str | None = None) -> None:
        self.messages = [{"role": "system", "content": system or system_prompt(seed=self._seed)}]
        self.steps = 0
        self.tool_calls = 0
        with self._lock:
            self._interjects.clear()

    # ---------------------------------------------------------- 中途插话

    def interject(self, text: str) -> bool:
        """用户中途插话。只排队，不动 messages —— 内核会在安全点取走。

        返回 True 表示队列满了、挤掉了最旧的一条（界面据此提示用户）。
        """
        text = (text or "").strip()
        if not text:
            return False
        with self._lock:
            self._interjects.append(text)
            evicted = False
            while len(self._interjects) > MAX_INTERJECTS:
                self._interjects.pop(0)
                evicted = True
            return evicted

    def take_interjects(self) -> list[str]:
        """取走全部待处理插话。返回值一定完整，不会漏也不会重。"""
        with self._lock:
            items, self._interjects = self._interjects, []
        return items

    @property
    def has_interjects(self) -> bool:
        with self._lock:
            return bool(self._interjects)

    @property
    def pending_interjects(self) -> int:
        with self._lock:
            return len(self._interjects)

    @property
    def turns(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")
