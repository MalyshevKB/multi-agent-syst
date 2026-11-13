from __future__ import annotations

import time
from typing import Any

from ..messages import COORD_PROTOCOLS, Message, MessageBus, Performative


class Agent:
    """Базовый агент: идентификатор, роль, доступ к шине, обработка сообщений по перформативу."""

    role = "agent"

    def __init__(self, aid: str, bus: MessageBus, groups=()):
        self.id = aid
        self.bus = bus
        self.alive = True
        bus.register(self, groups)

    @property
    def now(self) -> int:
        return self.bus.tick

    def send(self, receiver: str, performative: Performative, content: dict[str, Any],
             protocol: str = "", conversation_id: str = "") -> None:
        if self.alive:
            self.bus.send(Message(self.id, receiver, performative, dict(content), protocol, conversation_id))

    def receive(self, msg: Message) -> None:
        handler = getattr(self, f"on_{msg.performative.name.lower()}", None)
        if handler is None:
            return
        if msg.protocol in COORD_PROTOCOLS:   # учёт процессорного времени на координацию
            t0 = time.perf_counter()
            handler(msg)
            self.bus.coord_cpu += time.perf_counter() - t0
        else:
            handler(msg)

    def step(self, t: int) -> None:  # действия агента на такте
        pass
