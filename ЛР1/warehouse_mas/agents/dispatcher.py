"""Агент-диспетчер: распределение задач между роботами.

Режимы:
  central — централизованная диспетчеризация по модели состояния роботов (статусы от роботов);
  cnp     — контрактная сеть (Contract Net Protocol): CFP -> PROPOSE/REFUSE -> ACCEPT/REJECT.
"""
from __future__ import annotations

from collections import deque

from ..messages import Performative as P
from .base import Agent

UNAVAILABLE = {"WAIT_CHARGE", "TO_CHARGER", "CHARGING", "STRANDED"}


class DispatcherAgent(Agent):
    role = "dispatcher"

    def __init__(self, bus, env, cfg, robot_positions: dict[str, tuple[int, int]]):
        super().__init__("dispatcher", bus)
        self.env, self.cfg, self.mode = env, cfg, cfg.mode
        self.tasks: dict[str, dict] = {}
        self.pending: deque[str] = deque()
        self.assigned: dict[str, str] = {}     # задача -> робот (подтверждено)
        self.awaiting: dict[str, str] = {}     # задача -> робот (отправлено, ждём подтверждения)
        self.done: set[str] = set()
        self.retry_at: dict[str, int] = {}
        self.negotiations: dict[str, dict] = {}
        self.dead: set[str] = set()
        self.model = {r: {"pos": p, "battery": cfg.battery_capacity, "load": 0, "state": "IDLE",
                          "tail": p, "energy": 0} for r, p in robot_positions.items()}
        self.reassigned = 0
        self.cnp_rounds = 0

    # ------------------------------------------------------------------ входящие
    def on_request(self, msg) -> None:
        if msg.content.get("action") == "submit_task":
            task = msg.content["task"]
            self.tasks[task["task_id"]] = task
            self.pending.append(task["task_id"])

    def on_inform(self, msg) -> None:
        c = msg.content
        ev = c.get("event")
        if ev == "status":
            if msg.sender in self.model:
                self.model[msg.sender].update({k: c[k] for k in ("pos", "battery", "load", "state", "tail", "energy")})
        elif ev == "task_done":
            tid = c["task_id"]
            self.assigned.pop(tid, None)
            self.awaiting.pop(tid, None)
            if tid in self.pending:
                self.pending.remove(tid)
            self.done.add(tid)
            self.send("order_manager", P.INFORM, {"event": "task_done", "task_id": tid,
                                                  "order_id": self.tasks[tid]["order_id"], "robot": msg.sender},
                      "task-submission", tid)
        elif ev == "task_accepted":
            tid = c["task_id"]
            if self.awaiting.get(tid) == msg.sender:
                del self.awaiting[tid]
                self.assigned[tid] = msg.sender
        elif ev == "robot_failed":
            self._robot_lost(c["robot"])

    def on_agree(self, msg) -> None:  # central: робот принял назначение
        tid = msg.content.get("task_id")
        if self.awaiting.get(tid) == msg.sender:
            del self.awaiting[tid]
            self.assigned[tid] = msg.sender

    def on_refuse(self, msg) -> None:
        tid = msg.content.get("task_id")
        if msg.protocol == "cnp":
            neg = self.negotiations.get(tid)
            if neg is not None:
                neg["refused"].add(msg.sender)
        elif self.awaiting.get(tid) == msg.sender:
            del self.awaiting[tid]
            self._requeue(tid, count=False)

    def on_propose(self, msg) -> None:
        neg = self.negotiations.get(msg.content["task_id"])
        if neg is not None:
            neg["bids"][msg.sender] = msg.content["cost"]

    def on_failure(self, msg) -> None:
        for tid in msg.content.get("task_ids", []):
            if self.assigned.get(tid) == msg.sender or self.awaiting.get(tid) == msg.sender:
                self.assigned.pop(tid, None)
                self.awaiting.pop(tid, None)
                self._requeue(tid, count=msg.content.get("reason") != "capacity")
        if msg.content.get("reason") == "battery_depleted":
            self.dead.add(msg.sender)

    # ------------------------------------------------------------------ служебное
    def _requeue(self, tid, count=True, delay=0) -> None:
        if tid in self.done or tid in self.pending:
            return
        self.pending.appendleft(tid)
        self.retry_at[tid] = self.now + delay
        if count:
            self.reassigned += 1

    def _robot_lost(self, r) -> None:
        self.dead.add(r)
        for table in (self.assigned, self.awaiting):
            for tid, rr in list(table.items()):
                if rr == r:
                    del table[tid]
                    self._requeue(tid)

    def _feasible_by_model(self, m, task) -> tuple[bool, int]:
        d = self.env.dist(tuple(m["tail"]), task["pick"])
        e = d + self.env.dist(task["pick"], task["drop"])
        back = min(self.env.dist(task["drop"], c) for c in self.env.chargers)
        ok = m["battery"] - m["energy"] - e >= back + self.cfg.battery_reserve
        return ok, d

    # ------------------------------------------------------------------ такт
    def step(self, t: int) -> None:
        if self.mode == "cnp":
            self._cnp_award(t)
            self._cnp_announce(t)
        else:
            self._central_assign(t)

    def _central_assign(self, t: int) -> None:
        remaining: deque[str] = deque()
        recharge_sent = False
        while self.pending:
            tid = self.pending.popleft()
            if self.retry_at.get(tid, 0) > t:
                remaining.append(tid)
                continue
            task = self.tasks[tid]
            best, best_score = None, None
            for r, m in self.model.items():
                if r in self.dead or m["state"] in UNAVAILABLE or m["load"] >= self.cfg.robot_queue_cap:
                    continue
                ok, d = self._feasible_by_model(m, task)
                if not ok:
                    continue
                score = (d + m["load"] * self.cfg.est_task_time, r)
                if best_score is None or score < best_score:
                    best, best_score = r, score
            if best is None:
                remaining.append(tid)
                if not recharge_sent:
                    recharge_sent = self._ask_recharge(task)
                continue
            m = self.model[best]
            m["load"] += 1                       # оптимистичное обновление модели
            m["energy"] += self.env.dist(tuple(m["tail"]), task["pick"]) + self.env.dist(task["pick"], task["drop"])
            m["tail"] = task["drop"]
            self.awaiting[tid] = best
            self.send(best, P.REQUEST, {"action": "assign", "task": task, "task_id": tid}, "central-assign", tid)
        self.pending = remaining

    def _ask_recharge(self, task) -> bool:
        """Нет исполнителя: свободного робота, которому не хватает заряда, отправляем на зарядку."""
        idle = [(m["battery"], r) for r, m in self.model.items()
                if r not in self.dead and m["state"] in ("IDLE", "TO_HOME") and m["load"] == 0
                and not self._feasible_by_model(m, task)[0]]
        if not idle:
            return False
        _, r = min(idle)
        self.model[r]["state"] = "WAIT_CHARGE"   # до прихода статуса считаем занятым
        self.send(r, P.REQUEST, {"action": "recharge"}, "central-assign")
        return True

    def _cnp_award(self, t: int) -> None:
        for tid, neg in list(self.negotiations.items()):
            if neg["deadline"] > t:
                continue
            del self.negotiations[tid]
            bids = neg["bids"]
            if not bids:
                self._requeue(tid, count=False, delay=self.cfg.cnp_retry)
                continue
            winner = min(bids, key=lambda r: (bids[r], r))
            for r in bids:
                perf = P.ACCEPT_PROPOSAL if r == winner else P.REJECT_PROPOSAL
                self.send(r, perf, {"task_id": tid, "task": self.tasks[tid]}, "cnp", tid)
            self.awaiting[tid] = winner

    def _cnp_announce(self, t: int) -> None:
        remaining: deque[str] = deque()
        started = 0
        while self.pending:
            tid = self.pending.popleft()
            if started < self.cfg.cnp_parallel and self.retry_at.get(tid, 0) <= t:
                self.negotiations[tid] = {"bids": {}, "refused": set(), "deadline": t + 1}
                self.send("group:robots", P.CFP, {"task_id": tid, "task": self.tasks[tid]}, "cnp", tid)
                self.cnp_rounds += 1
                started += 1
            else:
                remaining.append(tid)
        self.pending = remaining
