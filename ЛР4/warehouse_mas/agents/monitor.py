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
        # ЛР4: живость регулировщика и переключение на горячий резерв, вызов эвакуации
        self.traffic_id, self.traffic_seen, self.failovers = "traffic", 0, 0
        self.dispatchers = ["dispatcher"]

    def _tap(self, msg) -> None:
        if msg.sender == self.traffic_id:
            self.traffic_seen = self.bus.tick
        if msg.sender in self.last_seen:
            self.last_seen[msg.sender] = self.bus.tick
            # ЛР4: разряженный робот жив, но неподвижен и перекрывает проход — тоже вызываем эвакуацию
            if self.has("evacuation") and msg.content.get("state") == "STRANDED" and msg.sender not in self.failed:
                self.failed.add(msg.sender)
                self.detections.append((msg.sender, self.bus.tick))
                self.send("maintenance", P.REQUEST, {"action": "evacuate", "robot": msg.sender}, "maintenance")
        if self.trace is not None:
            self.trace.append(msg.to_dict())

    def step(self, t: int) -> None:
        if self.has("standby") and self.traffic_id == "traffic" and t - self.traffic_seen > 15:
            self.traffic_id = "traffic_b"            # регулировщик молчит — повышаем резерв
            self.failovers += 1
            self.detections.append(("traffic", t))
            self.send("traffic_b", P.INFORM, {"event": "promote"}, "failover")
            self.send("group:robots", P.INFORM, {"event": "traffic_failover", "new": "traffic_b"}, "failover")
            for d in self.dispatchers:
                self.send(d, P.INFORM, {"event": "traffic_failover", "new": "traffic_b"}, "failover")
        for r, seen in self.last_seen.items():
            if r not in self.failed and t - seen > self.cfg.heartbeat_timeout:
                self.failed.add(r)
                self.detections.append((r, t))
                if self.has("evacuation"):
                    self.send("maintenance", P.REQUEST, {"action": "evacuate", "robot": r}, "maintenance")
                for rcpt in self.notify:
                    self.send(rcpt, P.INFORM, {"event": "robot_failed", "robot": r, "last_seen": seen}, "liveness")
