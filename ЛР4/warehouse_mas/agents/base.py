from __future__ import annotations

import time
from typing import Any

from ..messages import COORD_PROTOCOLS, Message, MessageBus, Performative
from ..security import authorized


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
            self.bus.send(Message(self.id, receiver, performative, dict(content), protocol, conversation_id),
                          origin=self.id)

    def has(self, defense: str) -> bool:
        cfg = self.bus.cfg
        return cfg is not None and defense in cfg.defenses

    def receive(self, msg: Message) -> None:
        # ЛР4, защита «auth»: подлинность отправителя и матрица полномочий
        if self.has("auth"):
            if msg.origin and msg.origin != msg.sender:
                self.bus.security_events.append((self.now, "spoofed", msg.origin, msg.sender, self.id))
                return
            if not authorized(msg, self.id):
                self.bus.security_events.append((self.now, "unauthorized", msg.origin, msg.sender, self.id))
                return
        handler = getattr(self, f"on_{msg.performative.name.lower()}", None)
        if handler is None:
            return
        self.guarded(handler, msg)

    def guarded(self, fn, *args) -> None:
        """Выполнение обработчика. Без защиты исключение означает отказ (падение) агента;
        с защитой «contain» ошибка изолируется: сообщение отбрасывается, агент продолжает работу."""
        try:
            if args and getattr(args[0], "protocol", None) in COORD_PROTOCOLS:
                t0 = time.perf_counter()
                fn(*args)
                self.bus.coord_cpu += time.perf_counter() - t0
            else:
                fn(*args)
        except Exception as e:  # noqa: BLE001 — модель отказа агента
            if self.has("contain"):
                self.bus.contained.append((self.now, self.id, type(e).__name__))
            else:
                self.alive = False
                self.bus.crashes.append((self.now, self.id, f"{type(e).__name__}: {e}"[:120]))

    def step(self, t: int) -> None:  # действия агента на такте
        pass
