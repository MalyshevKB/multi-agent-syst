"""Агент управления движением: резервирование клеток, запросы на уступку дороги."""
from __future__ import annotations

from collections import deque

from ..messages import Performative as P
from .base import Agent


class TrafficControllerAgent(Agent):
    role = "traffic"

    def __init__(self, bus, positions: dict[str, tuple[int, int]], aid: str = "traffic", active: bool = True):
        super().__init__(aid, bus)
        self.active = active                              # ЛР4: горячий резерв неактивен до повышения
        self.trail: dict[str, deque] = {r: deque(maxlen=80) for r in positions}   # история положений
        self.last_hb = -10 ** 6
        self.positions = dict(positions)                  # знание: где стоит каждый робот
        self.reservations: dict[tuple, tuple[str, int]] = {}  # клетка -> (робот, такт выдачи)
        self.grants = 0
        self.refusals = 0
        self.yield_requests = 0
        self._last_yield: dict[str, int] = {}
        self.priority: dict[str, int] = {}  # последний заявленный приоритет проезда робота

    def step(self, t: int) -> None:
        if not self.active:
            return
        if self.has("standby") and t - self.last_hb >= 5:   # сигнал живости для монитора
            self.last_hb = t
            self.send("monitor", P.INFORM, {"event": "heartbeat"}, "liveness")
        self.reservations = {c: v for c, v in self.reservations.items() if v[1] >= t - 1}

    def _occupant(self, cell, exclude):
        for r, p in self.positions.items():
            if p == cell and r != exclude:
                return r
        return None

    def _set_pos(self, r, p) -> None:
        self.positions[r] = p
        self.trail.setdefault(r, deque(maxlen=80)).append((self.now, p))

    def on_request(self, msg) -> None:
        if not self.active:
            return
        if msg.content.get("action") == "verify":   # ЛР4: свидетельство для сверки отчёта о выполнении
            c = msg.content
            cell = tuple(c["cell"]) if isinstance(c["cell"], (list, tuple)) else c["cell"]
            at = c.get("at", self.now)                 # такт заявленной сдачи
            known = [p for tk, p in self.trail.get(c["robot"], ()) if tk <= at]
            ok = bool(known) and known[-1] == cell     # где был робот именно в этот такт
            self.send(msg.sender, P.INFORM, {"event": "verify_result", "task_id": c["task_id"], "ok": ok},
                      "verification", c["task_id"])
            return
        if msg.content.get("action") != "reserve":
            return
        r, cell, t = msg.sender, tuple(msg.content["cell"]), self.now
        if self.has("timeouts") and msg.content.get("pos") is not None:
            self._set_pos(r, tuple(msg.content["pos"]))   # самоотчёт о положении — устраняет потерю «moved»
        self.priority[r] = msg.content.get("priority", 0)
        occ = self._occupant(cell, r)
        res = self.reservations.get(cell)
        holder = res[0] if res and res[0] != r and res[1] >= t - 1 else None
        if occ or holder:
            self.refusals += 1
            self.send(r, P.REFUSE, {"action": "reserve", "cell": cell, "blocked_by": occ or holder,
                                     "blocker_priority": self.priority.get(occ or holder, 0)}, "traffic")
            if occ and self._last_yield.get(occ, -99) <= t - 2:
                # просьба уступить дорогу; решение принимает сам занявший клетку робот по приоритетам
                self._last_yield[occ] = t
                self.yield_requests += 1
                self.send(occ, P.REQUEST, {"action": "yield", "requester": r,
                                           "requester_pos": self.positions.get(r),
                                           "priority": msg.content.get("priority", 0)}, "traffic")
            return
        self.reservations[cell] = (r, t)
        self.grants += 1
        self.send(r, P.AGREE, {"action": "reserve", "cell": cell}, "traffic")

    def on_inform(self, msg) -> None:
        ev = msg.content.get("event")
        if ev == "promote":
            self.active = True
            return
        if ev == "removed":                              # робот эвакуирован с поля
            self.positions.pop(msg.content["robot"], None)
            return
        if ev == "position":
            self._set_pos(msg.sender, tuple(msg.content["pos"]))
            return
        if ev == "moved":
            to = tuple(msg.content["to"])
            self._set_pos(msg.sender, to)
            if self.reservations.get(to, (None,))[0] == msg.sender:
                del self.reservations[to]
