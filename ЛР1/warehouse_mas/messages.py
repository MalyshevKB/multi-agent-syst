"""Сообщения в стиле FIPA-ACL и шина доставки сообщений."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class Performative(str, Enum):
    REQUEST = "request"
    AGREE = "agree"
    REFUSE = "refuse"
    INFORM = "inform"
    FAILURE = "failure"
    CFP = "cfp"
    PROPOSE = "propose"
    ACCEPT_PROPOSAL = "accept-proposal"
    REJECT_PROPOSAL = "reject-proposal"


@dataclass
class Message:
    sender: str
    receiver: str                 # id агента или "group:<имя>"
    performative: Performative
    content: dict[str, Any]
    protocol: str = ""
    conversation_id: str = ""
    tick: int = -1
    msg_id: int = -1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.msg_id, "tick": self.tick, "from": self.sender, "to": self.receiver,
            "performative": self.performative.value, "protocol": self.protocol,
            "conversation": self.conversation_id, "content": self.content,
        }


class MessageBus:
    """Шина: сообщения, отправленные на такте, доставляются раундами в том же такте.

    Каждая доставленная копия сообщения учитывается в статистике (рассылка группе
    из N агентов = N сообщений) — это и есть «стоимость коммуникации».
    """

    def __init__(self, max_rounds: int = 30):
        self.agents: dict[str, Any] = {}
        self.groups: dict[str, list[str]] = {}
        self.queue: deque[Message] = deque()
        self.tick = 0
        self.max_rounds = max_rounds
        self.stats: Counter[str] = Counter()
        self.by_protocol: Counter[str] = Counter()
        self.taps: list[Callable[[Message], None]] = []
        self._next_id = 1

    def register(self, agent, groups=()) -> None:
        self.agents[agent.id] = agent
        for g in groups:
            self.groups.setdefault(g, []).append(agent.id)

    def send(self, msg: Message) -> None:
        msg.tick = self.tick
        msg.msg_id = self._next_id
        self._next_id += 1
        self.queue.append(msg)

    def deliver(self) -> None:
        rounds = 0
        while self.queue and rounds < self.max_rounds:
            batch = list(self.queue)
            self.queue.clear()
            for msg in batch:
                self._dispatch(msg)
            rounds += 1

    def _recipients(self, msg: Message) -> list[str]:
        if msg.receiver.startswith("group:"):
            return [r for r in self.groups.get(msg.receiver[6:], []) if r != msg.sender]
        return [msg.receiver]

    def _dispatch(self, msg: Message) -> None:
        for tap in self.taps:
            tap(msg)
        for rid in self._recipients(msg):
            self.stats["total"] += 1
            self.stats[msg.performative.value] += 1
            self.by_protocol[msg.protocol or "-"] += 1
            agent = self.agents.get(rid)
            if agent is None or not agent.alive:
                self.stats["lost"] += 1
                continue
            agent.receive(msg)
