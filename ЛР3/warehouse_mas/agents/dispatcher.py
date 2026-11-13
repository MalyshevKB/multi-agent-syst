"""Агент-диспетчер: распределение задач между роботами.

Режимы (механизмы распределения, ЛР2):
  central    — централизованная диспетчеризация по модели состояния роботов (статусы от роботов);
  cnp        — контрактная сеть (Contract Net Protocol): CFP -> PROPOSE/REFUSE -> ACCEPT/REJECT,
               отдельный тендер на каждую задачу;
  auction    — периодический пакетный аукцион с закрытыми ставками: лот из нескольких задач,
               каждый робот присылает вектор ставок, победители определяются жадно;
  delegation — распределённое делегирование: задача передаётся по кольцу роботов, пока кто-то
               не возьмёт её по порогу стоимости; иначе — лучшему из опрошенных.
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
        self.robot_ids = list(robot_positions)
        # аукцион
        self.auction: dict | None = None
        self.last_auction = -10 ** 6
        self.auction_rounds = 0
        # делегирование
        self.delegating: dict[str, int] = {}   # задача -> такт начала делегирования
        self._rr = 0
        self.delegation_rounds = 0
        # метрики
        self.assigned_at: dict[str, int] = {}

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
            if self.awaiting.get(tid) == msg.sender or self.delegating.pop(tid, None) is not None:
                self.awaiting.pop(tid, None)
                self._confirm(tid, msg.sender)
        elif ev == "tasks_accepted":   # аукцион: подтверждение пакета
            for tid in c["task_ids"]:
                if self.awaiting.get(tid) == msg.sender:
                    del self.awaiting[tid]
                    self._confirm(tid, msg.sender)
        elif ev == "robot_failed":
            self._robot_lost(c["robot"])

    def on_agree(self, msg) -> None:  # central: робот принял назначение
        tid = msg.content.get("task_id")
        if self.awaiting.get(tid) == msg.sender:
            del self.awaiting[tid]
            self._confirm(tid, msg.sender)

    def on_refuse(self, msg) -> None:
        tid = msg.content.get("task_id")
        if msg.protocol == "auction":
            return
        if msg.protocol == "cnp":
            neg = self.negotiations.get(tid)
            if neg is not None:
                neg["refused"].add(msg.sender)
        elif self.awaiting.get(tid) == msg.sender:
            del self.awaiting[tid]
            self._requeue(tid, count=False)

    def on_propose(self, msg) -> None:
        if msg.protocol == "auction":
            a = self.auction
            if a is not None and msg.content["auction_id"] == a["id"]:
                a["bids"][msg.sender] = msg.content["bids"]
                a["free"][msg.sender] = msg.content["free"]
            return
        neg = self.negotiations.get(msg.content["task_id"])
        if neg is not None:
            neg["bids"][msg.sender] = msg.content["cost"]

    def on_failure(self, msg) -> None:
        if msg.protocol == "delegation":   # круг пройден, исполнитель не найден
            for tid in msg.content.get("task_ids", []):
                if self.delegating.pop(tid, None) is not None:
                    self._requeue(tid, count=False, delay=self.cfg.deleg_retry)
            return
        for tid in msg.content.get("task_ids", []):
            if self.assigned.get(tid) == msg.sender or self.awaiting.get(tid) == msg.sender:
                self.assigned.pop(tid, None)
                self.awaiting.pop(tid, None)
                self._requeue(tid, count=msg.content.get("reason") != "capacity")
        if msg.content.get("reason") == "battery_depleted":
            self.dead.add(msg.sender)

    # ------------------------------------------------------------------ служебное
    def _confirm(self, tid, robot) -> None:
        self.assigned[tid] = robot
        self.assigned_at.setdefault(tid, self.now)

    def _ready(self, t):
        """Разделить очередь на готовые к распределению задачи и ждущие паузы повтора."""
        ready, remaining = [], deque()
        for tid in self.pending:
            (ready if self.retry_at.get(tid, 0) <= t else remaining).append(tid)
        # ЛР3: правило приоритезации заказов — экспресс-задачи первыми, затем по сроку (EDF)
        ready.sort(key=lambda x: (-self.tasks[x].get("priority", 1), self.tasks[x].get("deadline", 0)))
        return ready, remaining

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
        elif self.mode == "auction":
            self._auction_resolve(t)
            self._auction_announce(t)
        elif self.mode == "delegation":
            self._delegation_start(t)
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

    # ------------------------------------------------------------------ аукцион
    def _auction_announce(self, t: int) -> None:
        if self.auction is not None or t - self.last_auction < self.cfg.auction_period:
            return
        ready, remaining = self._ready(t)
        if not ready:
            return
        lot, rest = ready[: self.cfg.auction_lot], ready[self.cfg.auction_lot:]
        self.pending = deque(rest) + remaining
        self.last_auction = t
        self.auction_rounds += 1
        aid = f"A{self.auction_rounds}"
        self.auction = {"id": aid, "tasks": lot, "bids": {}, "free": {}, "deadline": t + 1}
        self.send("group:robots", P.CFP, {"auction_id": aid, "tasks": [self.tasks[x] for x in lot]}, "auction", aid)

    def _auction_resolve(self, t: int) -> None:
        a = self.auction
        if a is None or a["deadline"] > t:
            return
        self.auction = None
        unassigned = list(a["tasks"])
        wins: dict[str, list[str]] = {r: [] for r in a["bids"]}
        while True:
            # жадное определение победителей: минимальная скорректированная ставка среди всех пар;
            # каждая следующая выигранная роботом задача удорожается на оценку длительности задачи
            best = None
            for r, bids in a["bids"].items():
                if len(wins[r]) >= a["free"].get(r, 0):
                    continue
                for tid, cost in bids.items():
                    if tid in unassigned:
                        bonus = 30 if self.tasks[tid].get("priority", 1) > 1 else 0   # экспресс — вперёд
                        key = (cost + len(wins[r]) * self.cfg.est_task_time - bonus, r, tid)
                        if best is None or key < best:
                            best = key
            if best is None:
                break
            _, r, tid = best
            wins[r].append(tid)
            unassigned.remove(tid)
        for r, won in wins.items():
            if won:
                for tid in won:
                    self.awaiting[tid] = r
                self.send(r, P.ACCEPT_PROPOSAL, {"auction_id": a["id"], "tasks": [self.tasks[x] for x in won]},
                          "auction", a["id"])
            else:
                self.send(r, P.REJECT_PROPOSAL, {"auction_id": a["id"]}, "auction", a["id"])
        for tid in reversed(unassigned):     # не разыгранные задачи — в следующий аукцион
            self.pending.appendleft(tid)

    # ------------------------------------------------------------------ делегирование
    def _delegation_start(self, t: int) -> None:
        for tid, since in list(self.delegating.items()):   # потерянные в кольце задачи
            if t - since > self.cfg.deleg_timeout:
                del self.delegating[tid]
                self._requeue(tid, count=False)
        ready, remaining = self._ready(t)
        started = 0
        rest = deque()
        alive = [r for r in self.robot_ids if r not in self.dead]
        for tid in ready:
            if started >= self.cfg.deleg_parallel or not alive:
                rest.append(tid)
                continue
            target = alive[self._rr % len(alive)]   # точка входа в кольцо — по очереди
            self._rr += 1
            self.delegating[tid] = t
            self.delegation_rounds += 1
            started += 1
            self.send(target, P.REQUEST, {"action": "delegate", "task": self.tasks[tid], "hops": 0,
                                          "best": None, "best_cost": None}, "delegation", tid)
        self.pending = rest + remaining
