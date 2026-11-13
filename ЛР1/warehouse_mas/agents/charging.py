"""Агент зарядной станции: распределяет ограниченные слоты зарядки (очередь FIFO)
и места ожидания; отвечает на запросы-оценки времени ожидания."""
from __future__ import annotations

from collections import deque

from ..messages import Performative as P
from .base import Agent


class ChargingStationAgent(Agent):
    role = "charging_station"

    def __init__(self, aid, bus, slots, wait_cells, est_charge_time: int = 20):
        super().__init__(aid, bus)
        self.slots: dict[tuple, str | None] = {c: None for c in slots}
        self.wait_cells: dict[tuple, str | None] = {c: None for c in wait_cells}
        self.queue: deque[str] = deque()
        self.est_charge_time = est_charge_time
        self._queued_at: dict[str, int] = {}
        self.served = 0
        self.total_wait = 0
        self.max_queue = 0

    # ---------------------------------------------------------------- протокол
    def on_request(self, msg) -> None:
        a, r = msg.content.get("action"), msg.sender
        if a == "charge_query":   # оценка ожидания для выбора станции роботом
            free = sum(1 for o in self.slots.values() if o is None)
            est = 0 if free > len(self.queue) else \
                (len(self.queue) - free + 1) * self.est_charge_time // len(self.slots)
            self.send(r, P.INFORM, {"event": "charge_offer", "est_wait": est}, "charging")
            return
        if a != "charge" or r in self.slots.values() or r in self.queue:
            return
        self.queue.append(r)
        self._queued_at[r] = self.now
        self.max_queue = max(self.max_queue, len(self.queue))
        self._serve()
        if r in self.queue:
            cell = next((c for c, o in self.wait_cells.items() if o is None), None)
            if cell is not None:
                self.wait_cells[cell] = r
            self.send(r, P.INFORM, {"event": "charge_queued", "position": len(self.queue),
                                    "wait_cell": cell}, "charging")

    def on_inform(self, msg) -> None:
        ev, r = msg.content.get("event"), msg.sender
        if ev == "charging_started":       # робот доехал до слота — место ожидания свободно
            self._free_wait_cell(r)
        elif ev in ("charge_done", "charge_cancel"):
            self._release(r)
        elif ev == "robot_failed":
            failed = msg.content["robot"]
            if failed in self.queue:
                self.queue.remove(failed)
        self._serve()

    # ---------------------------------------------------------------- служебное
    def _free_wait_cell(self, r) -> None:
        for c, o in self.wait_cells.items():
            if o == r:
                self.wait_cells[c] = None

    def _release(self, r) -> None:
        for c, o in self.slots.items():
            if o == r:
                self.slots[c] = None
        self._free_wait_cell(r)
        if r in self.queue:
            self.queue.remove(r)

    def _serve(self) -> None:
        for c, o in self.slots.items():
            if o is None and self.queue:
                r = self.queue.popleft()
                self.slots[c] = r
                self.served += 1
                self.total_wait += self.now - self._queued_at.pop(r, self.now)
                self.send(r, P.AGREE, {"action": "charge", "slot": c}, "charging")
