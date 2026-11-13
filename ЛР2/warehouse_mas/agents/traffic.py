"""Агент управления движением: резервирование клеток, запросы на уступку дороги."""
from __future__ import annotations

from ..messages import Performative as P
from .base import Agent


class TrafficControllerAgent(Agent):
    role = "traffic"

    def __init__(self, bus, positions: dict[str, tuple[int, int]]):
        super().__init__("traffic", bus)
        self.positions = dict(positions)                  # знание: где стоит каждый робот
        self.reservations: dict[tuple, tuple[str, int]] = {}  # клетка -> (робот, такт выдачи)
        self.grants = 0
        self.refusals = 0
        self.yield_requests = 0
        self._last_yield: dict[str, int] = {}
        self.priority: dict[str, int] = {}  # последний заявленный приоритет проезда робота

    def step(self, t: int) -> None:
        self.reservations = {c: v for c, v in self.reservations.items() if v[1] >= t - 1}

    def _occupant(self, cell, exclude):
        for r, p in self.positions.items():
            if p == cell and r != exclude:
                return r
        return None

    def on_request(self, msg) -> None:
        if msg.content.get("action") != "reserve":
            return
        r, cell, t = msg.sender, tuple(msg.content["cell"]), self.now
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
        if msg.content.get("event") == "moved":
            to = tuple(msg.content["to"])
            self.positions[msg.sender] = to
            if self.reservations.get(to, (None,))[0] == msg.sender:
                del self.reservations[to]
