"""Агент-робот: гибридная архитектура.

Реактивный уровень — движение по клеткам через резервирование у TrafficController,
ожидание/перепланирование при блокировке, уступка дороги.
Делиберативный уровень — оценка выполнимости задач (энергия, загрузка), ставки в
контрактной сети, решение о зарядке, ведение очереди обязательств.
"""
from __future__ import annotations

import random

from ..messages import Performative as P
from .base import Agent

IDLE, TO_PICK, PICKING, TO_DROP, DROPPING = "IDLE", "TO_PICK", "PICKING", "TO_DROP", "DROPPING"
TO_HOME, WAIT_CHARGE, TO_CHARGER, CHARGING, STRANDED = "TO_HOME", "WAIT_CHARGE", "TO_CHARGER", "CHARGING", "STRANDED"
WORKING = {TO_PICK, PICKING, TO_DROP, DROPPING}
NO_NEW_TASKS = {WAIT_CHARGE, TO_CHARGER, CHARGING, STRANDED}
YIELDING = {IDLE, TO_HOME, WAIT_CHARGE}   # кто может сделать шаг в сторону по просьбе
# приоритет проезда: чем выше, тем меньше оснований уступать
PRIORITY = {TO_CHARGER: 4, TO_DROP: 3, TO_PICK: 2, WAIT_CHARGE: 1, TO_HOME: 0, IDLE: 0}


class RobotAgent(Agent):
    role = "robot"

    def __init__(self, aid, bus, env, cfg, pos, home, stations: dict[str, list], rng: random.Random):
        super().__init__(aid, bus, groups=("robots",))
        self.env, self.cfg, self.rng = env, cfg, rng
        self.pos, self.home = pos, home
        self.stations = stations                  # id станции -> клетки слотов
        self.battery = cfg.battery_capacity
        self.state = IDLE
        self.task: dict | None = None
        self.queue: list[dict] = []
        self.goal = None
        self.path: list = []
        self.reserved = None
        self.awaiting_cell = None
        self.blocked_for = 0
        self.timer = 0
        self.charge_station: str | None = None
        self.last_hb = -10 ** 6
        self.charge_offers: dict[str, int] = {}
        self.low_battery_flag = False             # отказался от задачи из-за заряда
        # статистика
        self.distance = self.wait_ticks = self.replans = self.tasks_done = self.busy_ticks = self.yields = 0
        self.empty_distance = 0                   # пробег без груза к точке подбора (качество распределения)
        self.ring_next: str | None = None         # сосед по кольцу делегирования
        # ЛР3
        self.liar = aid in cfg.liars              # агент с подменённой целью: занижает заряд в заявке
        self.emergency_charges = 0
        self.min_battery = cfg.battery_capacity
        self.yields_given = 0
        self.stuck_for = 0                        # отказов подряд без единого шага
        self.backoffs = 0
        # ЛР4: внедряемые неисправности (агенты с подменённой целью / ошибкой)
        faulty = aid in cfg.fault_robots
        self.cheater = faulty and cfg.scenario == "false_done"        # ложные отчёты о выполнении
        self.saboteur = faulty and cfg.scenario == "saboteur"         # «чёрная дыра» для задач
        self.buggy_forwarder = faulty and cfg.scenario == "delegation_loop"   # сбрасывает счётчик хопов
        self.fake_reports = 0
        # ЛР4: защитные механизмы и физическая «истина» для аудита
        self.traffic_id = "traffic"
        self.max_epoch = 0
        self.owner: dict[str, str] = {}           # задача -> назначивший координатор
        self.pending_done: dict[str, list] = {}   # неподтверждённые отчёты: [владелец, такт отправки, попыток]
        self.awaiting_since = 0
        self.last_station_contact = 0
        self.query_at = 0
        self.seen_deleg: set = set()
        self.physical_drops: list[tuple[str, int]] = []   # (задача, такт) — фактические сдачи товара
        self.stale_refusals = 0

    # ================================================================ оценки
    def load(self) -> int:
        return (1 if self.task else 0) + len(self.queue)

    def _task_cost(self, task, frm) -> tuple[int, int]:
        d1 = self.env.dist(frm, task["pick"])
        d2 = self.env.dist(task["pick"], task["drop"])
        return d1 + self.cfg.pick_time + d2 + self.cfg.drop_time, d1 + d2

    def commitments(self) -> tuple[int, int, tuple]:
        """(время до освобождения, требуемая энергия, клетка после выполнения обязательств)."""
        time = energy = 0
        tail = self.pos
        t = self.task
        if t is not None:
            d2 = self.env.dist(t["pick"], t["drop"])
            if self.state == TO_PICK:
                d1 = self.env.dist(self.pos, t["pick"])
                time, energy = d1 + self.cfg.pick_time + d2 + self.cfg.drop_time, d1 + d2
            elif self.state == PICKING:
                time, energy = self.timer + d2 + self.cfg.drop_time, d2
            elif self.state == TO_DROP:
                d = self.env.dist(self.pos, t["drop"])
                time, energy = d + self.cfg.drop_time, d
            elif self.state == DROPPING:
                time = self.timer
            tail = t["drop"]
        for q in self.queue:
            dt, de = self._task_cost(q, tail)
            time, energy, tail = time + dt, energy + de, q["drop"]
        return time, energy, tail

    def _charger_dist(self, frm) -> int:
        return min(self.env.dist(frm, c) for c in self.env.chargers)

    def can_take(self, task) -> tuple[bool, str]:
        if self.state in NO_NEW_TASKS:
            return False, "charging"
        if self.load() >= self.cfg.robot_queue_cap:
            return False, "capacity"
        _, energy, tail = self.commitments()
        _, e = self._task_cost(task, tail)
        if self.battery - energy - e < self._charger_dist(task["drop"]) + self.cfg.battery_reserve:
            if self.load() == 0:
                self.low_battery_flag = True   # свободен, но не может работать — пора заряжаться
            return False, "battery"
        return True, ""

    def bid(self, task) -> int:
        time, _, tail = self.commitments()
        dt, _ = self._task_cost(task, tail)
        return time + dt

    # ================================================================ статусы
    def _report(self) -> None:
        if self.cfg.mode != "central":
            return  # в контрактной сети диспетчеру не нужна модель роботов
        _, energy, tail = self.commitments()
        self.send("dispatcher", P.INFORM, {"event": "status", "pos": self.pos, "battery": self.battery,
                                           "load": self.load(), "state": self.state, "tail": tail,
                                           "energy": energy}, "status")

    def _set_state(self, s) -> None:
        if s != self.state:
            self.state = s
            self._report()

    def _set_goal(self, goal) -> None:
        self.goal = goal
        self.path = (self.env.path(self.pos, goal) or []) if goal is not None else []
        self.blocked_for = 0

    # ================================================================ такт
    def step(self, t: int) -> None:
        if t - self.last_hb >= self.cfg.heartbeat_every:
            self.last_hb = t
            self.send("monitor", P.INFORM, {"event": "heartbeat", "state": self.state, "battery": self.battery,
                                            "pos": self.pos}, "liveness")
            if self.has("timeouts"):   # маяк положения: восстанавливает картину у регулировщика после потерь
                self.send(self.traffic_id, P.INFORM, {"event": "position", "pos": self.pos}, "traffic")
        if self.has("timeouts"):
            self._timeouts(t)
        if self.state == STRANDED:
            return
        if self.battery <= 0 and self.state != CHARGING:
            self._strand()
            return
        if self.state in WORKING:
            self.busy_ticks += 1
        self.min_battery = min(self.min_battery, self.battery)

        s = self.state
        if s == CHARGING:
            self.battery = min(self.cfg.battery_capacity, self.battery + self.cfg.charge_rate)
            if self.battery >= self.cfg.battery_capacity:
                self.send(self.charge_station, P.INFORM, {"event": "charge_done"}, "charging")
                self.charge_station = None
                self._set_state(IDLE)
        elif s in (PICKING, DROPPING):
            self.timer -= 1
            if self.timer <= 0:
                if s == PICKING:
                    self._set_goal(self.task["drop"])
                    self._set_state(TO_DROP)
                else:
                    tid = self.task["task_id"]
                    if self.pos == self.task["drop"]:
                        self.physical_drops.append((tid, t))     # физический факт сдачи (для аудита)
                    self.task = None
                    self.tasks_done += 1
                    self._report_done(tid)
                    self._set_state(IDLE)
        else:
            if self.state in (TO_PICK, TO_DROP, TO_HOME) and \
                    self.battery <= self._charger_dist(self.pos) + self.cfg.battery_reserve * 3 // 4:
                self._go_charge(abandon_current=True)   # аварийный уход на зарядку
            self._advance()

        if self.state in (IDLE, TO_HOME):
            self._decide()

    def _report_done(self, tid) -> None:
        owner = self.owner.pop(tid, "dispatcher")
        self.send(owner, P.INFORM, {"event": "task_done", "task_id": tid, "at": self.now}, "task-report", tid)
        if self.has("timeouts"):    # повтор до подтверждения
            self.pending_done[tid] = [owner, self.now, 1, self.now]

    def _timeouts(self, t) -> None:
        """Защита «timeouts»: тайм-ауты и повторы для запросов, ответ на которые мог потеряться."""
        if self.awaiting_cell is not None and t - self.awaiting_since > 2:
            self.awaiting_cell = None                 # ответ регулировщика не пришёл — повторим запрос
        for tid, rec in list(self.pending_done.items()):
            if t - rec[1] >= 5:
                if rec[2] >= 10:
                    del self.pending_done[tid]
                    continue
                rec[1], rec[2] = t, rec[2] + 1
                self.send(rec[0], P.INFORM, {"event": "task_done", "task_id": tid, "retry": rec[2], "at": rec[3]},
                          "task-report", tid)
        if self.state == WAIT_CHARGE and self.charge_station is None and t - self.query_at >= 3:
            if self.charge_offers:
                self._choose_station()                 # не все станции ответили — выбираем из ответивших
            else:
                self._query_stations()
        elif self.state in (WAIT_CHARGE, TO_CHARGER) and self.charge_station is not None \
                and t - self.last_station_contact > self.cfg.charge_timeout:
            self.send(self.charge_station, P.INFORM, {"event": "charge_cancel"}, "charging")
            self.charge_station = None
            self._go_charge()                          # станция молчит — заново выбираем станцию

    def _query_stations(self) -> None:
        self.query_at = self.now
        for st in self.stations:  # шаг 1 протокола зарядки: запрос оценок ожидания у всех станций
            self.send(st, P.REQUEST, {"action": "charge_query", "battery": self.battery}, "charging")

    def _decide(self) -> None:
        cap = self.cfg.battery_capacity
        if self.saboteur and self.now >= self.cfg.fault_start:
            return                                     # саботажник копит задачи и не выполняет их
        if self.queue and self.cheater and self.now >= self.cfg.fault_start:
            task = self.queue.pop(0)                   # ложный отчёт: «выполнено» без выполнения
            self.fake_reports += 1
            self._report_done(task["task_id"])
            return
        if self.queue:
            nxt = self.queue[0]
            _, e = self._task_cost(nxt, self.pos)
            if self.battery - e < self._charger_dist(nxt["drop"]) + self.cfg.battery_reserve:
                self._go_charge()
                return
            self.task = self.queue.pop(0)
            self._set_goal(self.task["pick"])
            self._set_state(TO_PICK)
            return
        if self.battery < cap * self.cfg.charge_threshold or self.low_battery_flag:
            self._go_charge()
            return
        if self.state == IDLE and self.pos != self.home and self.goal is None:
            self._set_goal(self.home)
            self._set_state(TO_HOME)

    def _go_charge(self, abandon_current: bool = False) -> None:
        ids = [q["task_id"] for q in self.queue]
        self.queue.clear()
        if abandon_current and self.task is not None:
            ids.insert(0, self.task["task_id"])
            self.task = None
        if abandon_current:
            self.emergency_charges += 1
        if ids:  # отдаём невыполнимые сейчас задачи обратно диспетчеру
            self.send("dispatcher", P.FAILURE, {"task_ids": ids, "reason": "charging"}, "task-report")
        self.charge_station = None
        self.charge_offers = {}
        self.low_battery_flag = False
        self.goal, self.path = None, []
        self._set_state(WAIT_CHARGE)
        self.last_station_contact = self.now
        self._query_stations()

    def _choose_station(self) -> None:
        """Шаг 2: выбор станции по сумме «дорога + ожидание» среди достижимых по заряду; запрос слота."""
        def road(st):
            return min(self.env.dist(self.pos, c) for c in self.stations[st])
        reachable = [s for s in self.charge_offers if road(s) + self.cfg.battery_reserve // 2 <= self.battery]
        candidates = reachable or list(self.charge_offers)
        if reachable:
            st = min(candidates, key=lambda s: (road(s) + self.charge_offers[s], s))
        else:
            st = min(candidates, key=lambda s: (road(s), s))
        self.charge_station = st
        self.last_station_contact = self.now
        # заявка: заявленный заряд определяет срочность; «лжец» занижает его, чтобы пройти без очереди
        claimed = min(self.battery, 5) if self.liar else self.battery
        self.send(st, P.REQUEST, {"action": "charge", "claimed_battery": claimed}, "charging")

    def _strand(self) -> None:
        ids = ([self.task["task_id"]] if self.task else []) + [q["task_id"] for q in self.queue]
        self.task, self.queue = None, []
        if self.charge_station:
            self.send(self.charge_station, P.INFORM, {"event": "charge_cancel"}, "charging")
        self.send("dispatcher", P.FAILURE, {"task_ids": ids, "reason": "battery_depleted"}, "task-report")
        self._set_state(STRANDED)

    # ================================================================ движение
    def _advance(self) -> None:
        if self.goal is None:
            return
        if self.pos == self.goal:
            self._arrive()
            return
        if not self.path:
            self.path = self.env.path(self.pos, self.goal) or []
            if not self.path:
                self.wait_ticks += 1
                return
        nxt = self.path[0]
        if self.reserved == nxt:
            self.reserved = None
            old, self.pos = self.pos, nxt
            self.path.pop(0)
            self.battery -= 1
            self.distance += 1
            self.stuck_for = 0
            if self.state == TO_PICK:
                self.empty_distance += 1
            self.blocked_for = 0
            self.send(self.traffic_id, P.INFORM, {"event": "moved", "from": old, "to": nxt}, "traffic")
            if self.pos == self.goal:
                self._arrive()
                return
            if not self.path:  # был шаг в сторону — перестраиваем маршрут
                self.path = self.env.path(self.pos, self.goal) or []
            if self.path:
                self._request_cell(self.path[0])
        elif self.awaiting_cell is None:
            self._request_cell(nxt)

    def _request_cell(self, cell) -> None:
        self.awaiting_cell = cell
        self.awaiting_since = self.now
        self.send(self.traffic_id, P.REQUEST, {"action": "reserve", "cell": cell, "pos": self.pos,
                                               "priority": PRIORITY.get(self.state, 0)}, "traffic")

    def _arrive(self) -> None:
        self.goal, self.path = None, []
        s = self.state
        if s == TO_PICK:
            self.timer = self.cfg.pick_time
            self._set_state(PICKING)
        elif s == TO_DROP:
            self.timer = self.cfg.drop_time
            self._set_state(DROPPING)
        elif s == TO_HOME:
            self._set_state(IDLE)
        elif s == TO_CHARGER:
            self.send(self.charge_station, P.INFORM, {"event": "charging_started"}, "charging")
            self._set_state(CHARGING)

    # ================================================================ сообщения
    def _holds(self, tid) -> bool:
        return (self.task is not None and self.task["task_id"] == tid) or any(q["task_id"] == tid for q in self.queue)

    def _fenced(self, msg) -> bool:
        """Защита «fencing»: команды координатора с устаревшей эпохой отвергаются."""
        if not msg.sender.startswith("dispatcher"):
            return False
        e = msg.content.get("epoch", 0)
        if self.has("fencing") and e < self.max_epoch:
            self.stale_refusals += 1
            perf = P.REFUSE if msg.performative == P.CFP else P.FAILURE
            ids = [x["task_id"] for x in msg.content.get("tasks", [])] or \
                ([msg.content["task"]["task_id"]] if "task" in msg.content else [])
            self.send(msg.sender, perf, {"reason": "stale_epoch", "epoch": self.max_epoch, "task_ids": ids,
                                         "leader": self.leader_seen, "auction_id": msg.content.get("auction_id")},
                      msg.protocol, msg.conversation_id)
            return True
        if e > self.max_epoch:
            self.max_epoch, self.leader_seen = e, msg.sender
        return False

    leader_seen = "dispatcher"

    def on_agree(self, msg) -> None:
        if msg.sender.startswith("charger"):
            self.last_station_contact = self.now
        a = msg.content.get("action")
        if a == "reserve":
            self.awaiting_cell = None
            cell = tuple(msg.content["cell"])
            if self.path and self.path[0] == cell:
                self.reserved = cell
        elif a == "charge":
            if self.state != WAIT_CHARGE:          # запоздавший ответ — слот уже не нужен
                self.send(msg.sender, P.INFORM, {"event": "charge_cancel"}, "charging")
                return
            self.charge_station = msg.sender
            self._set_goal(tuple(msg.content["slot"]))
            self._set_state(TO_CHARGER)

    def on_refuse(self, msg) -> None:
        if msg.content.get("action") != "reserve":
            return
        self.awaiting_cell = None
        self.wait_ticks += 1
        self.blocked_for += 1
        cell = tuple(msg.content["cell"])
        self.stuck_for += 1
        if self.stuck_for >= self.cfg.backoff_after and self.state in WORKING | {TO_HOME}                 and self.rng.random() < 0.3:
            # затор из нескольких роботов, не разрешённый правилами приоритета, — случайная отсрочка:
            # шаг на свободную соседнюю клетку ломает симметрию взаимного ожидания
            self.backoffs += 1
            self.stuck_for = 0
            self._step_aside(avoid=cell)
            return
        # Правило разрешения встречного конфликта: объезд ищет робот с меньшим приоритетом
        # (при равенстве — с бОльшим id); второй ждёт, перепланируя лишь после долгого ожидания.
        mine, theirs = PRIORITY.get(self.state, 0), msg.content.get("blocker_priority", 0)
        i_give_way = mine < theirs or (mine == theirs and self.id > str(msg.content.get("blocked_by")))
        limit = self.cfg.replan_after if i_give_way else self.cfg.replan_after * 4
        if cell == self.goal and i_give_way and self.blocked_for >= self.cfg.replan_after * 2:
            # цель занята роботом, которому мы уступаем, — отступаем на соседнюю клетку
            self._step_aside(avoid=cell)
            return
        if self.blocked_for >= limit and self.goal is not None and cell != self.goal:
            alt = self.env.path(self.pos, self.goal, blocked={cell})
            if not alt and self.blocked_for >= self.cfg.replan_after * 4:
                # тупик (например, отказавший робот впереди в одностороннем проходе) — аварийный разворот
                alt = self.env.path(self.pos, self.goal, blocked={cell}, directed=False)
            if alt:
                self.path = alt
                self.replans += 1
                self.blocked_for = 0

    def on_request(self, msg) -> None:
        a = msg.content.get("action")
        if a in ("assign", "recharge") and self._fenced(msg):
            return
        if a == "report_position":
            self.send(msg.sender, P.INFORM, {"event": "position", "pos": self.pos}, "traffic")
            return
        if a == "assign":  # централизованное назначение
            task = msg.content["task"]
            ok, reason = self.can_take(task)
            if ok:
                self.queue.append(task)
                self.send("dispatcher", P.AGREE, {"task_id": task["task_id"]}, "central-assign", task["task_id"])
                self._report()
            else:
                self.send("dispatcher", P.REFUSE, {"task_id": task["task_id"], "reason": reason},
                          "central-assign", task["task_id"])
                self._report()
        elif a in ("delegate", "delegate_final"):
            self._on_delegate(msg)
        elif a == "release_offer":   # переговоры: станция просит уступить слот за кредит
            self._on_release_offer(msg)
        elif a == "recharge":   # указание диспетчера (полномочие координатора)
            if self.state in (IDLE, TO_HOME) and self.load() == 0:
                self.low_battery_flag = True
        elif a == "yield":
            self._maybe_yield(msg.content)

    def _on_release_offer(self, msg) -> None:
        """Собственная оценка полезности: честный робот уступает, если заряда уже хватает для работы
        (кредит компенсирует недозарядку); эгоистичный («лжец») никогда не уступает."""
        cap = self.cfg.battery_capacity
        if self.state == CHARGING and not self.liar and self.battery >= self.cfg.yield_accept_level * cap:
            self.send(msg.sender, P.AGREE, {"action": "release", "battery": self.battery}, "charging-negotiation")
            self.yields_given += 1
            self.send(self.charge_station, P.INFORM, {"event": "charge_done", "early": True}, "charging")
            self.charge_station = None
            self._set_state(IDLE)
        else:
            self.send(msg.sender, P.REFUSE, {"action": "release"}, "charging-negotiation")

    def _maybe_yield(self, c) -> None:
        """Правило уступки: уступает робот с меньшим приоритетом; при равенстве — с большим id."""
        # уступает только стоящий робот: едущий и так освободит клетку, а «маятник»
        # (шаг в сторону и сразу обратно) лишь тратит заряд
        if self.state not in YIELDING or (self.goal is not None and self.goal != self.pos):
            return
        # ЛР3: робот, ждущий зарядки, бережёт заряд — уступает дорогу только при запасе хода
        reserve = 2 if self.state != WAIT_CHARGE else self.cfg.battery_reserve
        req_pos = tuple(c["requester_pos"]) if c.get("requester_pos") else None
        if req_pos in self.env.chargers:
            reserve = 0                          # робот выезжает со слота — без этого очередь не сдвинется
        if self.battery <= self._charger_dist(self.pos) + reserve:
            return
        mine, theirs = PRIORITY.get(self.state, 0), c.get("priority", 0)
        if mine > theirs or (mine == theirs and self.id < c["requester"]):
            return
        req_pos = tuple(c["requester_pos"]) if c.get("requester_pos") else None
        self._step_aside(avoid=req_pos)

    def _step_aside(self, avoid=None) -> None:
        options = [n for n in self.env.neighbors(self.pos)
                   if self.env.kind(n) == "." and self.env.can_move(self.pos, n) and n != avoid]
        if not options:
            return
        side = self.rng.choice(options)
        if self.goal is None:
            self.goal = side
        self.path = [side]  # шаг в сторону; затем маршрут к цели будет перестроен
        self.blocked_for = 0
        self.yields += 1

    # ---------------------------------------------------------------- делегирование
    def _accept_delegated(self, task, deleg_id=None) -> None:
        if not self._holds(task["task_id"]):
            self.queue.append(task)
        self.owner[task["task_id"]] = "dispatcher"
        self.send("dispatcher", P.INFORM, {"event": "task_accepted", "task_id": task["task_id"],
                                           "deleg_id": deleg_id}, "delegation", task["task_id"])

    def _on_delegate(self, msg) -> None:
        """Берём задачу, если можем и наша оценка не выше порога; иначе передаём соседу по кольцу,
        запоминая лучшего кандидата. После полного круга задачу получает лучший кандидат."""
        c = msg.content
        task, tid = c["task"], c["task"]["task_id"]
        did = c.get("deleg_id")
        if c["action"] == "delegate" and self.has("ttl"):
            if (did, tid) in self.seen_deleg:       # задача пришла второй раз — петля
                self.send("dispatcher", P.FAILURE, {"task_ids": [tid], "reason": "loop"}, "delegation", tid)
                return
            self.seen_deleg.add((did, tid))
        ok, _ = self.can_take(task)
        if c["action"] == "delegate_final":
            if ok:
                self._accept_delegated(task, did)
            else:
                self.send("dispatcher", P.FAILURE, {"task_ids": [tid], "reason": "capacity"}, "delegation", tid)
            return
        cost = self.bid(task) if ok else None
        if ok and cost <= self.cfg.deleg_threshold:
            self._accept_delegated(task, did)
            return
        best, best_cost = c["best"], c["best_cost"]
        if ok and (best_cost is None or cost < best_cost):
            best, best_cost = self.id, cost
        hops = c["hops"] + 1
        if hops >= self.cfg.n_robots:          # круг пройден
            if best == self.id:
                self._accept_delegated(task, did)
            elif best is not None:
                self.send(best, P.REQUEST, {"action": "delegate_final", "task": task, "deleg_id": did},
                          "delegation", tid)
            else:
                self.send("dispatcher", P.FAILURE, {"task_ids": [tid], "reason": "no_taker"}, "delegation", tid)
            return
        if self.buggy_forwarder and self.now >= self.cfg.fault_start:
            hops, best, best_cost = 0, None, None   # ошибка версии протокола: счётчик хопов сбрасывается
        self.bus.hops[(did, tid)] += 1          # инструментирование эксперимента (не логика агента)
        self.send(self.ring_next, P.REQUEST, {"action": "delegate", "task": task, "hops": hops, "deleg_id": did,
                                              "best": best, "best_cost": best_cost}, "delegation", tid)

    # ---------------------------------------------------------------- тендеры и аукционы
    def on_cfp(self, msg) -> None:
        if self._fenced(msg):
            return
        if msg.protocol == "auction":
            free = self.cfg.robot_queue_cap - self.load()
            bids = {}
            if self.saboteur and self.now >= self.cfg.fault_start:
                # подменённая цель: занижает стоимость до 1 и заявляет 10 свободных мест, чтобы выиграть всё
                bids = {tk["task_id"]: 1 for tk in msg.content["tasks"]}
                free = 10
            elif free > 0:
                for task in msg.content["tasks"]:
                    ok, _ = self.can_take(task)
                    if ok:
                        bids[task["task_id"]] = self.bid(task)
            aid = msg.content["auction_id"]
            if bids:
                self.send(msg.sender, P.PROPOSE, {"auction_id": aid, "bids": bids, "free": free}, "auction", aid)
            else:
                self.send(msg.sender, P.REFUSE, {"auction_id": aid}, "auction", aid)
            return
        task = msg.content["task"]
        ok, reason = self.can_take(task)
        if ok:
            self.send(msg.sender, P.PROPOSE, {"task_id": task["task_id"], "cost": self.bid(task)}, "cnp", task["task_id"])
        else:
            self.send(msg.sender, P.REFUSE, {"task_id": task["task_id"], "reason": reason}, "cnp", task["task_id"])

    def on_accept_proposal(self, msg) -> None:
        if self._fenced(msg):
            return
        if msg.protocol == "auction":
            ok_ids, bad_ids = [], []
            sab = self.saboteur and self.now >= self.cfg.fault_start
            for task in msg.content["tasks"]:
                if self._holds(task["task_id"]):
                    ok_ids.append(task["task_id"])          # повтор награды (потеря подтверждения) — не дублируем
                elif sab or self.can_take(task)[0]:
                    self.queue.append(task)
                    self.owner[task["task_id"]] = msg.sender
                    ok_ids.append(task["task_id"])
                else:
                    bad_ids.append(task["task_id"])
            aid = msg.content["auction_id"]
            if ok_ids:
                self.send(msg.sender, P.INFORM, {"event": "tasks_accepted", "task_ids": ok_ids}, "auction", aid)
            if bad_ids:
                self.send(msg.sender, P.FAILURE, {"task_ids": bad_ids, "reason": "capacity"}, "auction", aid)
            return
        task = msg.content["task"]
        ok, reason = self.can_take(task)
        if ok:
            self.queue.append(task)
            self.owner[task["task_id"]] = msg.sender
            self.send(msg.sender, P.INFORM, {"event": "task_accepted", "task_id": task["task_id"]}, "cnp", task["task_id"])
        else:
            self.send(msg.sender, P.FAILURE, {"task_ids": [task["task_id"]], "reason": "capacity"}, "cnp", task["task_id"])

    def on_inform(self, msg) -> None:
        ev = msg.content.get("event")
        if msg.sender.startswith("charger"):
            self.last_station_contact = self.now
        if ev == "done_ack":
            self.pending_done.pop(msg.content["task_id"], None)
            return
        if ev == "task_revoked":                  # координатор отозвал задачу (просрочка / устаревшее назначение)
            tid = msg.content["task_id"]
            if self.saboteur:
                return
            self.queue = [q for q in self.queue if q["task_id"] != tid]
            if self.task is not None and self.task["task_id"] == tid:
                self.task, self.goal, self.path = None, None, []
                self._set_state(IDLE)
            self.owner.pop(tid, None)
            return
        if ev == "traffic_failover":              # монитор переключил регулировщика на резервный
            self.traffic_id = msg.content["new"]
            self.awaiting_cell, self.reserved = None, None
            self.send(self.traffic_id, P.INFORM, {"event": "position", "pos": self.pos}, "traffic")
            return
        if ev == "charge_keepalive":
            return
        if ev == "charge_revoked" and self.state == TO_CHARGER:   # не успел подключиться — заявка заново
            self.charge_station = None
            self._go_charge()
            return
        if ev == "charge_offer" and self.state == WAIT_CHARGE and self.charge_station is None:
            self.charge_offers[msg.sender] = msg.content["est_wait"]
            if len(self.charge_offers) == len(self.stations):
                self._choose_station()
        elif ev == "charge_queued" and self.state == WAIT_CHARGE and msg.content.get("wait_cell"):
            self._set_goal(tuple(msg.content["wait_cell"]))
