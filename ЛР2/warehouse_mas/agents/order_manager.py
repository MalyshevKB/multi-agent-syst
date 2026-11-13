"""Агент приёма заказов: генерирует заказы, декомпозирует их на задачи, учитывает выполнение."""
from __future__ import annotations

import random

from ..messages import Performative as P
from .base import Agent


class OrderManagerAgent(Agent):
    role = "order_manager"

    def __init__(self, bus, env, cfg, rng: random.Random):
        super().__init__("order_manager", bus)
        self.env, self.cfg, self.rng = env, cfg, rng
        self.orders: dict[str, dict] = {}
        self.generated = 0
        self._task_seq = 0
        self.duplicates = 0

    def step(self, t: int) -> None:
        if self.generated < self.cfg.n_orders and self.rng.random() < self.cfg.order_rate:
            self._new_order(t)

    def _new_order(self, t: int) -> None:
        oid = f"O{self.generated + 1}"
        station = self.env.stations[self.generated % len(self.env.stations)]
        self.generated += 1
        k = self.rng.randint(1, self.cfg.max_items)
        order = {"created": t, "tasks": set(), "done": set(), "completed": None, "station": station}
        self.orders[oid] = order
        for shelf in self.rng.sample(self.env.shelves, k):
            self._task_seq += 1
            tid = f"T{self._task_seq}"
            order["tasks"].add(tid)
            task = {"task_id": tid, "order_id": oid, "shelf": shelf,
                    "pick": self.env.pick_point(shelf), "drop": self.env.drop_point(station),
                    "station": station, "created": t}
            self.send("dispatcher", P.REQUEST, {"action": "submit_task", "task": task}, "task-submission", tid)

    def on_inform(self, msg) -> None:
        if msg.content.get("event") != "task_done":
            return
        order = self.orders.get(msg.content["order_id"])
        tid = msg.content["task_id"]
        if order is None or tid not in order["tasks"]:
            return
        if tid in order["done"]:
            self.duplicates += 1
            return
        order["done"].add(tid)
        if order["done"] == order["tasks"]:
            order["completed"] = self.now

    @property
    def all_done(self) -> bool:
        return self.generated >= self.cfg.n_orders and all(o["completed"] is not None for o in self.orders.values())
