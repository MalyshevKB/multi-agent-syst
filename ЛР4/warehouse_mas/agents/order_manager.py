"""Агент приёма заказов: генерирует заказы, декомпозирует их на задачи, учитывает выполнение."""
from __future__ import annotations

import random

from ..messages import Performative as P
from ..security import validate_task
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
        self.dispatchers = ["dispatcher"]
        self.injected = self.quarantined = 0
        self.unacked: dict[tuple, list] = {}      # (диспетчер, задача) -> [задача, такт отправки, попыток]

    def step(self, t: int) -> None:
        for (d, tid), rec in list(self.unacked.items()):   # повтор неподтверждённых передач задач
            if t - rec[1] >= 10:
                rec[1], rec[2] = t, rec[2] + 1
                self.send(d, P.REQUEST, {"action": "submit_task", "task": rec[0]}, "task-submission", tid)
        if self.generated < self.cfg.n_orders and self.rng.random() < self.cfg.order_rate:
            self._new_order(t)

    def _new_order(self, t: int) -> None:
        oid = f"O{self.generated + 1}"
        station = self.env.stations[self.generated % len(self.env.stations)]
        self.generated += 1
        k = self.rng.randint(1, self.cfg.max_items)
        express = self.rng.random() < self.cfg.express_share
        deadline = t + (self.cfg.express_deadline if express else self.cfg.standard_deadline)
        order = {"created": t, "tasks": set(), "done": set(), "completed": None, "station": station,
                 "express": express, "deadline": deadline, "malicious": None, "rejected": False}
        self.orders[oid] = order
        tasks = []
        for shelf in self.rng.sample(self.env.shelves, k):
            self._task_seq += 1
            tid = f"T{self._task_seq}"
            order["tasks"].add(tid)
            task = {"task_id": tid, "order_id": oid, "shelf": shelf,
                    "pick": self.env.pick_point(shelf), "drop": self.env.drop_point(station),
                    "station": station, "created": t, "priority": 2 if express else 1, "deadline": deadline}
            tasks.append(task)
        if self.cfg.scenario == "injection" and t >= self.cfg.fault_start and self.rng.random() < self.cfg.inject_rate:
            self._inject(order, tasks)              # заказ из внешнего потока содержит вредоносные данные
        if self.has("validate"):                    # защита: схема и семантика до передачи в систему
            if not all(validate_task(tk, self.env)[0] for tk in tasks):
                order["rejected"] = True
                self.quarantined += 1
                return
        for task in tasks:
            for d in self.dispatchers:
                self.send(d, P.REQUEST, {"action": "submit_task", "task": task}, "task-submission", task["task_id"])
                if self.has("timeouts"):
                    self.unacked[(d, task["task_id"])] = [task, self.now, 1]

    def _inject(self, order, tasks) -> None:
        """Вредоносная нагрузка (ЛР4, сценарий «внедрение вредоносных инструкций»)."""
        kind = self.rng.choice(["type_confusion", "out_of_map", "charger_squat"])
        order["malicious"] = kind
        self.injected += 1
        tk = tasks[0]
        if kind == "type_confusion":      # координаты списком вместо кортежа — «отравленное» сообщение
            tk["pick"] = list(tk["pick"])
        elif kind == "out_of_map":        # недостижимая точка подбора
            tk["pick"] = (99, 99)
        else:                             # точка сдачи — слот зарядки: робот занимает ресурс
            tk["drop"] = self.env.chargers[0]

    def on_inform(self, msg) -> None:
        if msg.content.get("event") == "submit_ack":
            self.unacked.pop((msg.sender, msg.content["task_id"]), None)
            return
        if msg.content.get("event") != "task_done":
            return
        if self.has("timeouts"):
            self.send(msg.sender, P.INFORM, {"event": "report_ack", "task_id": msg.content["task_id"]},
                      "task-submission", msg.content["task_id"])
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
        return self.generated >= self.cfg.n_orders and all(
            o["completed"] is not None for o in self.orders.values() if not o["malicious"] and not o["rejected"])
