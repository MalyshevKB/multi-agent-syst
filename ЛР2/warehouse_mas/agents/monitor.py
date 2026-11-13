"""Агент-наблюдатель: трассировка всех сообщений, контроль живости роботов (heartbeat)."""
from __future__ import annotations

from ..messages import Performative as P
from .base import Agent


class MonitorAgent(Agent):
    role = "monitor"

    def __init__(self, bus, cfg, robot_ids, notify, keep_trace: bool = False):
        super().__init__("monitor", bus)
        self.cfg = cfg
        self.last_seen = {r: 0 for r in robot_ids}
        self.failed: set[str] = set()
        self.detections: list[tuple[str, int]] = []
        self.notify = list(notify)
        self.trace: list[dict] | None = [] if keep_trace else None
        bus.taps.append(self._tap)

    def _tap(self, msg) -> None:
        if msg.sender in self.last_seen:
            self.last_seen[msg.sender] = self.bus.tick
        if self.trace is not None:
            self.trace.append(msg.to_dict())

    def step(self, t: int) -> None:
        for r, seen in self.last_seen.items():
            if r not in self.failed and t - seen > self.cfg.heartbeat_timeout:
                self.failed.add(r)
                self.detections.append((r, t))
                for rcpt in self.notify:
                    self.send(rcpt, P.INFORM, {"event": "robot_failed", "robot": r, "last_seen": seen}, "liveness")
