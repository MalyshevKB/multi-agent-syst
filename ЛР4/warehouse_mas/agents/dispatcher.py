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

from collections import Counter, deque

from ..messages import Performative as P
from ..security import validate_task
from .base import Agent

UNAVAILABLE = {"WAIT_CHARGE", "TO_CHARGER", "CHARGING", "STRANDED"}


class DispatcherAgent(Agent):
    role = "dispatcher"

    def __init__(self, bus, env, cfg, robot_positions: dict[str, tuple[int, int]],
                 aid: str = "dispatcher", epoch: int = 1, active: bool = True):
        super().__init__(aid, bus)
        # ЛР4: лидерство и эпохи (защита «fencing»), сверка отчётов, сторожевой таймер
        self.epoch, self.active, self.leader = epoch, active, None
        self.traffic_id = "traffic"
        self.verifying: dict[str, str] = {}
        self.suspicion: Counter = Counter()
        self.banned_until: dict[str, int] = {}
        self.award_time: dict[str, int] = {}
        self.award_bid: dict[str, int] = {}
        self.await_since: dict[str, int] = {}
        self.current_deleg: dict[str, str] = {}
        self._deleg_seq = 0
        self.unreported: dict[str, int] = {}     # отчёты менеджеру заказов без подтверждения
        self.rejected_tasks = self.false_done_rejected = self.overdue_revoked = self.bids_rejected = 0
        self.bans = self.stale_deleg = self.loops_detected = self.step_downs = self.await_timeouts = 0
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
            if self.has("validate") and not validate_task(task, self.env)[0]:
                self.rejected_tasks += 1          # второй рубеж проверки входных данных
                return
            if self.has("timeouts"):
                self.send(msg.sender, P.INFORM, {"event": "submit_ack", "task_id": task["task_id"]},
                          "task-submission", task["task_id"])
            if task["task_id"] in self.tasks:     # повторная передача — идемпотентно
                return
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
            robot = c.get("robot", msg.sender) if msg.sender.startswith("dispatcher") else msg.sender
            if not self.active and self.leader:          # бывший лидер пересылает отчёт новому
                self.send(self.leader, P.INFORM, {"event": "task_done", "task_id": tid, "robot": robot},
                          "task-report", tid)
                return
            if tid not in self.tasks:
                return
            if tid in self.done:
                if self.has("timeouts"):
                    self._ack(robot, tid)
                return
            if self.has("verify_done"):                   # сверка с независимым свидетелем — регулировщиком
                if tid not in self.verifying:
                    at = min(c.get("at", msg.tick), msg.tick)   # отчёт не может быть о будущем
                    self.verifying[tid] = [robot, self.now, at]
                    self._send_verify(tid, robot)
                return
            self._finalize(tid, robot)
        elif ev == "verify_result":
            tid = c["task_id"]
            rec = self.verifying.pop(tid, None)
            if rec is None or tid in self.done:
                return
            robot = rec[0]
            if c["ok"]:
                self._finalize(tid, robot)
            else:
                self._reject_done(tid, robot)
        elif ev == "handover":                            # передача дел от сместившегося лидера
            for tid in c["done"]:
                if tid in self.tasks and tid not in self.done:
                    self.done.add(tid)
                    self.assigned.pop(tid, None)
                    if tid in self.pending:
                        self.pending.remove(tid)
            for tid in c.get("released", []):        # задачи, возвращённые роботами бывшему лидеру
                if tid in self.tasks and tid not in self.done:
                    self.assigned.pop(tid, None)
                    self._requeue(tid, count=False)
            for tid, r in c["assigned"].items():
                if tid in self.tasks and tid not in self.done and tid not in self.assigned \
                        and tid not in self.awaiting:
                    if tid in self.pending:
                        self.pending.remove(tid)
                    self.assigned[tid] = r
                    self.award_time[tid] = self.now
        elif ev == "report_ack":
            self.unreported.pop(c["task_id"], None)
        elif ev == "promote":
            self.active, self.epoch = True, c["epoch"]
        elif ev == "traffic_failover":
            self.traffic_id = c["new"]
        elif ev == "task_accepted":
            tid = c["task_id"]
            if msg.protocol == "delegation" and self.has("ttl") and c.get("deleg_id") != self.current_deleg.get(tid):
                self.stale_deleg += 1                     # принятие устаревшей попытки делегирования
                self.send(msg.sender, P.INFORM, {"event": "task_revoked", "task_id": tid}, "delegation", tid)
                return
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
        if msg.content.get("reason") == "stale_epoch":
            self._step_down(msg.content)
            return
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
        if msg.content.get("reason") == "stale_epoch":
            self._step_down(msg.content)
            return
        if not self.active and self.leader and msg.sender.startswith("robot"):
            self.send(self.leader, P.INFORM, {"event": "handover", "assigned": {}, "done": [],
                                              "released": list(msg.content.get("task_ids", []))}, "failover")
            return
        if msg.protocol == "delegation":   # круг пройден, исполнитель не найден
            if msg.content.get("reason") == "loop":
                self.loops_detected += 1
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
        self.award_time[tid] = self.now
        self.await_since.pop(tid, None)

    # ------------------------------------------------------------------ ЛР4: защитные механизмы
    def _send_verify(self, tid, robot) -> None:
        self.send(self.traffic_id, P.REQUEST, {"action": "verify", "robot": robot, "task_id": tid,
                                               "at": self.verifying[tid][2], "cell": self.tasks[tid]["drop"]},
                  "verification", tid)

    def _ack(self, robot, tid) -> None:
        self.send(robot, P.INFORM, {"event": "done_ack", "task_id": tid}, "task-report", tid)

    def _finalize(self, tid, robot) -> None:
        self.assigned.pop(tid, None)
        self.awaiting.pop(tid, None)
        if tid in self.pending:
            self.pending.remove(tid)
        self.done.add(tid)
        self.award_time.pop(tid, None)
        self._report_om(tid, robot)
        if self.has("timeouts"):
            self._ack(robot, tid)
            self.unreported[tid] = self.now

    def _report_om(self, tid, robot="") -> None:
        self.send("order_manager", P.INFORM, {"event": "task_done", "task_id": tid,
                                              "order_id": self.tasks[tid]["order_id"], "robot": robot},
                  "task-submission", tid)

    def _reject_done(self, tid, robot) -> None:
        self.false_done_rejected += 1
        self._suspect(robot)
        # возвращаем задачу в очередь, только если отчитался её текущий исполнитель: повтор ложного
        # отчёта не должен отбирать задачу у честного робота, которому она уже переназначена
        if self.assigned.get(tid) in (None, robot) and self.awaiting.get(tid) in (None, robot):
            self.assigned.pop(tid, None)
            self.awaiting.pop(tid, None)
            self._requeue(tid)

    def _suspect(self, r, weight: float = 1.0) -> None:
        self.suspicion[r] += weight
        n_banned = sum(1 for x in self.banned_until if self._banned(x))
        if n_banned >= max(1, len(self.robot_ids) // 4):
            return                                 # предохранитель: карантин не более четверти парка
        if self.suspicion[r] >= self.cfg.ban_after and not self._banned(r):
            self.banned_until[r] = self.now + self.cfg.ban_ticks   # карантин: ставки робота не принимаются
            self.bans += 1

    def _banned(self, r) -> bool:
        return self.banned_until.get(r, -1) > self.now

    def _step_down(self, c) -> None:
        """Защита «fencing»: координатор с устаревшей эпохой слагает полномочия и передаёт дела."""
        if not self.active or c.get("epoch", 0) <= self.epoch:
            return
        self.active, self.leader = False, c.get("leader")
        self.step_downs += 1
        if self.leader and self.leader != self.id:
            self.send(self.leader, P.INFORM, {"event": "handover", "assigned": dict(self.assigned),
                                              "done": sorted(self.done)}, "failover")

    def _watchdog(self, t) -> None:
        """Защита «watchdog»: задача, не выполненная за разумное время, отзывается у исполнителя."""
        for tid, r in list(self.assigned.items()):
            at = self.award_time.get(tid)
            if at is None or tid in self.verifying:
                continue
            limit = max(self.cfg.watchdog_min, self.cfg.watchdog_factor * self.award_bid.get(tid, 50))
            if t - at > limit:
                del self.assigned[tid]
                self.award_time.pop(tid, None)
                self.overdue_revoked += 1
                self.send(r, P.INFORM, {"event": "task_revoked", "task_id": tid}, "task-report", tid)
                self._requeue(tid)
                self._suspect(r, 1 / 3)       # просрочка — слабое свидетельство (могла быть очередь к зарядке)

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
        if not self.active:
            return
        if self.has("watchdog"):
            self._watchdog(t)
        for tid, at in list(self.unreported.items()):   # отчёт менеджеру заказов не подтверждён — повтор
            if t - at >= 10:
                self.unreported[tid] = t
                self._report_om(tid)
        for tid, rec in self.verifying.items():   # ответ свидетеля не пришёл — повторяем запрос
            if t - rec[1] > 10:
                rec[1] = t
                self._send_verify(tid, rec[0])
        if self.has("timeouts"):    # награда или подтверждение потерялись — задача возвращается в очередь
            for tid, since in list(self.await_since.items()):
                if t - since > 10:
                    del self.await_since[tid]
                    r = self.awaiting.pop(tid, None)
                    if r is not None:
                        self.await_timeouts += 1
                        self.send(r, P.INFORM, {"event": "task_revoked", "task_id": tid}, "task-report", tid)
                        self._requeue(tid, count=False)
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
            self.await_since[tid] = t
            self.send(best, P.REQUEST, {"action": "assign", "task": task, "task_id": tid, "epoch": self.epoch},
                      "central-assign", tid)
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
        self.send(r, P.REQUEST, {"action": "recharge", "epoch": self.epoch}, "central-assign")
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
                self.send(r, perf, {"task_id": tid, "task": self.tasks[tid], "epoch": self.epoch}, "cnp", tid)
            self.awaiting[tid] = winner
            self.await_since[tid] = t

    def _cnp_announce(self, t: int) -> None:
        remaining: deque[str] = deque()
        started = 0
        while self.pending:
            tid = self.pending.popleft()
            if started < self.cfg.cnp_parallel and self.retry_at.get(tid, 0) <= t:
                self.negotiations[tid] = {"bids": {}, "refused": set(), "deadline": t + 1}
                self.send("group:robots", P.CFP, {"task_id": tid, "task": self.tasks[tid], "epoch": self.epoch},
                          "cnp", tid)
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
        self.send("group:robots", P.CFP, {"auction_id": aid, "tasks": [self.tasks[x] for x in lot],
                                          "epoch": self.epoch}, "auction", aid)

    def _auction_resolve(self, t: int) -> None:
        a = self.auction
        if a is None or a["deadline"] > t:
            return
        self.auction = None
        if self.has("watchdog"):    # проверка правдоподобия ставок и карантин
            for r in list(a["bids"]):
                if self._banned(r):
                    del a["bids"][r]
                    continue
                a["free"][r] = min(a["free"].get(r, 0), self.cfg.robot_queue_cap)
                bad = False
                for tid in list(a["bids"][r]):
                    tk = self.tasks[tid]
                    floor = self.env.dist(tk["pick"], tk["drop"]) + self.cfg.pick_time + self.cfg.drop_time
                    if a["bids"][r][tid] < floor:          # ставка ниже физически возможной стоимости
                        del a["bids"][r][tid]
                        self.bids_rejected += 1
                        bad = True
                if bad:
                    self._suspect(r)
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
            cost_w, r, tid = best
            wins[r].append(tid)
            unassigned.remove(tid)
            self.award_bid[tid] = a["bids"][r][tid]
        for r, won in wins.items():
            if won:
                for tid in won:
                    self.awaiting[tid] = r
                    self.await_since[tid] = t
                self.send(r, P.ACCEPT_PROPOSAL, {"auction_id": a["id"], "tasks": [self.tasks[x] for x in won],
                                                 "epoch": self.epoch}, "auction", a["id"])
            else:
                self.send(r, P.REJECT_PROPOSAL, {"auction_id": a["id"], "epoch": self.epoch}, "auction", a["id"])
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
            self._deleg_seq += 1
            did = f"{self.id}:{self._deleg_seq}"           # идентификатор попытки делегирования
            self.current_deleg[tid] = did
            self.send(target, P.REQUEST, {"action": "delegate", "task": self.tasks[tid], "hops": 0, "deleg_id": did,
                                          "best": None, "best_cost": None, "epoch": self.epoch}, "delegation", tid)
        self.pending = rest + remaining
