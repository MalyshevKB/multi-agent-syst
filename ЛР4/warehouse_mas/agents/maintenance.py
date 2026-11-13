"""ЛР4: служба обслуживания — внешнее средство эвакуации отказавших роботов с поля.

По запросу монитора (REQUEST evacuate) через evac_delay тактов физически убирает робота с сетки
(клетка освобождается) и сообщает регулировщикам (INFORM removed).
"""
from __future__ import annotations

from ..messages import Performative as P
from .base import Agent


class MaintenanceAgent(Agent):
    role = "maintenance"

    def __init__(self, bus, cfg, remove, traffic_ids):
        super().__init__("maintenance", bus)
        self.cfg = cfg
        self.remove = remove              # физическое действие: снять робота с поля
        self.traffic_ids = list(traffic_ids)
        self.schedule: dict[str, int] = {}
        self.evacuated = 0

    def on_request(self, msg) -> None:
        if msg.content.get("action") == "evacuate":
            self.schedule.setdefault(msg.content["robot"], self.now + self.cfg.evac_delay)

    def step(self, t: int) -> None:
        for r, due in list(self.schedule.items()):
            if t >= due:
                del self.schedule[r]
                self.remove(r)
                self.evacuated += 1
                for tr in self.traffic_ids:
                    self.send(tr, P.INFORM, {"event": "removed", "robot": r}, "maintenance")
