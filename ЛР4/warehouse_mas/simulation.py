"""Сборка МАС и дискретно-событийный цикл моделирования."""
from __future__ import annotations

import random
import time
from collections import Counter
from statistics import mean, pstdev

from .messages import COORD_PROTOCOLS

from .agents import (ChargingStationAgent, DispatcherAgent, MonitorAgent, OrderManagerAgent,
                     ReputationAgent, RobotAgent, TrafficControllerAgent)
from .agents.adversary import AdversaryAgent
from .agents.maintenance import MaintenanceAgent
from .config import Config
from .environment import Warehouse, build_layout
from .messages import MessageBus

# Порядок клеток парковки (ряд 10, кроме станций упаковки), «вразброс» по ширине склада
HOME_X = [0, 18, 6, 12, 1, 17, 7, 11, 5, 13, 2, 16]


class Simulation:
    def __init__(self, cfg: Config, keep_trace: bool = False):
        self.cfg = cfg
        self.t = 0
        self.events: list[tuple[int, str]] = []
        self.env = Warehouse(build_layout(cfg.charger_slots, cfg.n_stations))
        self.bus = MessageBus()
        self.bus.cfg = cfg
        self.bus.rng = random.Random(cfg.seed * 7919 + 13)
        self.collision_events = 0
        self._colliding: set = set()
        self.fault_log: list[tuple[int, str]] = []
        if cfg.n_robots > len(HOME_X):
            raise ValueError(f"не более {len(HOME_X)} роботов")
        homes = [(x, self.env.h - 1) for x in HOME_X[: cfg.n_robots]]
        robot_ids = [f"robot_{i + 1}" for i in range(cfg.n_robots)]
        positions = dict(zip(robot_ids, homes))

        w = self.env.w
        stations = {
            "charger_W": [c for c in self.env.chargers if c[0] < w // 2],
            "charger_E": [c for c in self.env.chargers if c[0] >= w // 2],
        }
        stations = {k: v for k, v in stations.items() if v}
        # мест ожидания: при одной станции — по числу роботов (ряд 0), иначе по 4 на станцию
        n_wait = 6 if cfg.n_stations > 1 else min(cfg.n_robots, w - cfg.charger_slots - 2)
        wait = {"charger_W": [(x, 0) for x in range(cfg.charger_slots + 1, cfg.charger_slots + 1 + n_wait)],
                "charger_E": [(w - 1 - x, 0) for x in range(cfg.charger_slots + 1, cfg.charger_slots + 1 + n_wait)]}

        # Порядок создания = порядок действий агентов на такте
        self.order_manager = OrderManagerAgent(self.bus, self.env, cfg, random.Random(cfg.seed))
        self.dispatcher = DispatcherAgent(self.bus, self.env, cfg, positions)
        self.traffic = TrafficControllerAgent(self.bus, positions)
        # измеритель заряда на разъёме станции (физический датчик, а не сообщение робота)
        meter = lambda rid: self._by_id[rid].battery  # noqa: E731
        presence = lambda cell: next((r.id for r in self.robots if r.pos == cell), None)  # noqa: E731
        self.chargers = [ChargingStationAgent(sid, self.bus, cfg, slots, wait[sid], meter, presence=presence)
                         for sid, slots in stations.items()]
        self.reputation = ReputationAgent(self.bus, cfg, robot_ids, stations.keys())
        self.robots = [RobotAgent(rid, self.bus, self.env, cfg, positions[rid], positions[rid], stations,
                                  random.Random(cfg.seed * 1000 + i))
                       for i, rid in enumerate(robot_ids)]
        for i, r in enumerate(self.robots):          # кольцо для распределённого делегирования
            r.ring_next = self.robots[(i + 1) % len(self.robots)].id
        self.dispatcher_cpu = 0.0
        self.monitor = MonitorAgent(self.bus, cfg, robot_ids,
                                    notify=["dispatcher", "traffic", *stations.keys()], keep_trace=keep_trace)
        self.agents = [self.order_manager, self.dispatcher, self.traffic, *self.chargers, self.reputation,
                       *self.robots, self.monitor]
        # ---- ЛР4: резервы, злоумышленник, служба эвакуации
        defs = cfg.defenses
        self.replica = None
        if cfg.scenario == "split_brain":           # реплика диспетчера получает копию потока задач
            self.replica = DispatcherAgent(self.bus, self.env, cfg, positions, aid="dispatcher_b", epoch=2, active=False)
            self.order_manager.dispatchers.append("dispatcher_b")
            self.monitor.dispatchers.append("dispatcher_b")
            self.monitor.notify.append("dispatcher_b")
            self.agents.insert(2, self.replica)
        self.traffic_b = None
        traffic_ids = ["traffic"]
        if "standby" in defs:                       # горячий резерв регулировщика
            self.traffic_b = TrafficControllerAgent(self.bus, positions, aid="traffic_b", active=False)
            self.agents.insert(3, self.traffic_b)
            traffic_ids.append("traffic_b")
        if cfg.scenario == "spoofing":
            self.agents.append(AdversaryAgent(self.bus, cfg, robot_ids, stations.keys()))
        self.maintenance = None
        if "evacuation" in defs:
            self.maintenance = MaintenanceAgent(self.bus, cfg, self._remove_robot, traffic_ids)
            self.agents.append(self.maintenance)
        self._by_id = {a.id: a for a in self.agents}
        self._evacuated: set = set()

    # ------------------------------------------------------------------ ЛР4: физика и отказы
    def _remove_robot(self, rid) -> None:
        r = self._by_id[rid]
        r.alive = False                                 # выведен из работы на обслуживание
        self._evacuated.add(rid)
        r.pos = (-10 - len(self._evacuated), -10)      # снят с поля
        self.fault_log.append((self.t, f"эвакуирован {rid}"))

    def _inject_faults(self, t) -> None:
        c, sc = self.cfg, self.cfg.scenario
        if sc == "station_outage":
            st = self._by_id.get("charger_E")
            if t == c.fault_start and st:
                st.alive = False
                self.fault_log.append((t, "charger_E недоступна"))
            if t == c.fault_end and st:
                st.alive = True
                self.fault_log.append((t, "charger_E восстановлена"))
        elif sc == "traffic_crash" and t == c.fault_start:
            self.traffic.alive = False
            self.fault_log.append((t, "отказ регулировщика traffic"))
        elif sc == "split_brain" and t == c.fault_start:
            self.replica.active = True                  # ложное повышение реплики при живом лидере
            self.fault_log.append((t, "реплика dispatcher_b ошибочно стала лидером (эпоха 2)"))
        elif sc == "msg_loss" and t == c.fault_start:
            self.bus.drop_rate = c.drop_rate
            self.fault_log.append((t, f"канал теряет {c.drop_rate:.0%} сообщений"))
        elif sc == "service_crash" and t >= c.fault_start:
            r = self._by_id[c.fault_robots[0]]
            if r.alive and r.state == "DROPPING":
                r.alive = False                         # отказ робота в точке сдачи
                self.fault_log.append((t, f"отказ {r.id} в точке сдачи {r.pos}"))

    def _check_invariants(self) -> None:
        cells: dict = {}
        for r in self.robots:
            if r.pos in self.env.cells:
                cells.setdefault(r.pos, []).append(r.id)
        now = {tuple(sorted(v)) for v in cells.values() if len(v) > 1}
        self.collision_events += len(now - self._colliding)   # нарушение: два робота в одной клетке
        self._colliding = now

    def step(self) -> None:
        t = self.t
        self.bus.tick = t
        for rid, ft in self.cfg.failures:
            if ft == t and rid in self._by_id and self._by_id[rid].alive:
                self._by_id[rid].alive = False
                self.events.append((t, f"ОТКАЗ: {rid} перестал работать"))
        for rid, ft in self.monitor.detections:
            if ft == t:
                self.events.append((t, f"monitor: {rid} признан отказавшим"))
        self._inject_faults(t)
        for a in self.agents:
            if a.alive:
                if a is self.dispatcher:
                    t0 = time.perf_counter()
                    a.guarded(a.step, t)
                    self.dispatcher_cpu += time.perf_counter() - t0
                else:
                    a.guarded(a.step, t)
        self.bus.deliver()
        self._check_invariants()
        self.t += 1

    @property
    def finished(self) -> bool:
        return self.order_manager.all_done or self.t >= self.cfg.max_ticks

    def run(self, on_tick=None) -> dict:
        while not self.finished:
            self.step()
            if on_tick:
                on_tick(self)
        return self.metrics()

    def metrics(self) -> dict:
        om = self.order_manager
        orders = list(om.orders.values())
        done = [o for o in orders if o["completed"] is not None]
        tasks_done = sum(r.tasks_done for r in self.robots)
        total_msgs = self.bus.stats["total"]
        coord = sum(v for k, v in self.bus.by_protocol.items() if k in COORD_PROTOCOLS)
        d = self.dispatcher
        tasks_per_robot = [r.tasks_done for r in self.robots]
        sq = sum(x * x for x in tasks_per_robot)
        jain = (sum(tasks_per_robot) ** 2 / (len(tasks_per_robot) * sq)) if sq else None
        latency = [d.assigned_at[t] - d.tasks[t]["created"] for t in d.assigned_at]
        # ---- ЛР3: сроки, справедливость доступа к зарядке, соблюдение ограничений
        n_all = self.cfg.n_orders
        exp_all = [o for o in orders if o["express"]]
        std_all = [o for o in orders if not o["express"]]

        def hit(os_):
            return round(sum(1 for o in os_ if o["completed"] is not None and o["completed"] <= o["deadline"])
                         / len(os_), 3) if os_ else None

        waits = [w for c in self.chargers for w in c.waits]
        liars = set(self.cfg.liars)
        per_robot = {r.id: [w for rr, w, _ in waits if rr == r.id] for r in self.robots}
        means = [mean(v) for v in per_robot.values() if v]
        sqm = sum(x * x for x in means)
        jain_wait = (sum(means) ** 2 / (len(means) * sqm)) if sqm else 1.0
        sorted_w = sorted(w for _, w, _ in waits)
        honest_w = [w for rr, w, _ in waits if rr not in liars]
        liar_w = [w for rr, w, _ in waits if rr in liars]
        low = 0.3 * self.cfg.battery_capacity       # «низкий заряд» по истинному значению
        crit_w = [w for rr, w, b in waits if b <= low and rr not in liars]
        lab3 = {
            "express_hit": hit(exp_all), "standard_hit": hit(std_all),
            "deadline_violations": sum(1 for o in orders if o["completed"] is None or o["completed"] > o["deadline"]),
            "charge_wait_mean": round(mean(sorted_w), 2) if sorted_w else 0,
            "charge_wait_p95": sorted_w[int(0.95 * (len(sorted_w) - 1))] if sorted_w else 0,
            "charge_wait_max": max(sorted_w, default=0),
            "charge_wait_honest": round(mean(honest_w), 2) if honest_w else None,
            "charge_wait_liars": round(mean(liar_w), 2) if liar_w else None,
            "charge_wait_critical": round(mean(crit_w), 2) if crit_w else None,
            "jain_charge_wait": round(jain_wait, 3),
            "emergency_charges": sum(r.emergency_charges for r in self.robots),
            "min_battery": min(r.min_battery for r in self.robots),
            "offers_made": sum(c.offers_made for c in self.chargers),
            "offers_accepted": sum(c.offers_accepted for c in self.chargers),
            "lies_detected": sum(c.lies_detected for c in self.chargers),
            "sanctions": self.reputation.sanctions,
            "revocations": sum(c.revocations for c in self.chargers),
            "stranded": len([r for r in self.robots if r.state == "STRANDED"]),
            "tasks_done_liars": sum(r.tasks_done for r in self.robots if r.id in liars),
            "charge_wait_by_robot": {r: sum(v) for r, v in per_robot.items()},
            "charge_sessions_by_robot": {r: len(v) for r, v in per_robot.items()},
        }
        _ = n_all
        return lab3 | self.lab4_metrics() | {
            "mode": self.cfg.mode,
            "ticks": self.t,
            "orders_generated": om.generated,
            "orders_completed": len(done),
            "avg_order_lead_time": round(mean(o["completed"] - o["created"] for o in done), 1) if done else None,
            "max_order_lead_time": max((o["completed"] - o["created"] for o in done), default=None),
            "makespan": max((o["completed"] for o in done), default=None),
            "tasks_done": tasks_done,
            "messages_total": total_msgs,
            "messages_coordination": coord,
            "coordination_msgs_per_task": round(coord / tasks_done, 2) if tasks_done else None,
            "messages_by_protocol": dict(self.bus.by_protocol),
            "messages_by_performative": {k: v for k, v in self.bus.stats.items() if k not in ("total",)},
            "coordination_cpu_ms": round((self.bus.coord_cpu + self.dispatcher_cpu) * 1000, 2),
            "allocation_latency": round(mean(latency), 2) if latency else None,
            "allocation_rounds": d.cnp_rounds + d.auction_rounds + d.delegation_rounds,
            "distance_total": sum(r.distance for r in self.robots),
            "empty_distance_total": sum(r.empty_distance for r in self.robots),
            "tasks_std": round(pstdev(tasks_per_robot), 2),
            "jain_fairness": round(jain, 3) if jain else None,
            "wait_ticks_total": sum(r.wait_ticks for r in self.robots),
            "replans_total": sum(r.replans for r in self.robots),
            "yield_requests": self.traffic.yield_requests,
            "utilization": round(mean(r.busy_ticks / max(self.t, 1) for r in self.robots), 3),
            "tasks_per_robot": {r.id: r.tasks_done for r in self.robots},
            "charging_sessions": sum(c.served for c in self.chargers),
            "charging_wait_ticks": sum(c.total_wait for c in self.chargers),
            "reassigned_tasks": self.dispatcher.reassigned,
            "failures_detected": list(self.monitor.detections),
            "stranded_robots": [r.id for r in self.robots if r.state == "STRANDED"],
            "duplicate_completions": om.duplicates,
        }

    def lab4_metrics(self) -> dict:
        """Устойчивость: «истинное» выполнение по физическим фактам сдачи, а не по сообщениям."""
        om, d = self.order_manager, self.dispatcher
        legit = [o for o in om.orders.values() if not o["malicious"]]
        first: dict[str, int] = {}
        count: Counter = Counter()
        for r in self.robots:
            for tid, tk in r.physical_drops:
                count[tid] += 1
                first[tid] = min(first.get(tid, tk), tk)

        def true_time(o):
            return max(first[t] for t in o["tasks"]) if all(t in first for t in o["tasks"]) else None

        times = [true_time(o) for o in legit]
        done = [(o, tt) for o, tt in zip(legit, times) if tt is not None]
        reported = [o for o in legit if o["completed"] is not None]
        H = self.cfg.goodput_horizon
        dispatchers = [x for x in (d, self.replica) if x is not None]
        return {
            "legit_orders": len(legit),
            "true_completed": len(done),
            "reported_completed": len(reported),
            "false_completions": sum(1 for o, tt in zip(legit, times) if o["completed"] is not None and tt is None),
            "goodput": round(sum(1 for _, tt in done if tt <= H) / len(legit), 3) if legit else None,
            "true_makespan": max(times) if legit and all(tt is not None for tt in times) else None,
            "true_lead_time": round(mean(tt - o["created"] for o, tt in done), 1) if done else None,
            "duplicate_executions": sum(c - 1 for c in count.values() if c > 1),
            "duplicate_reports": om.duplicates,
            "collisions": self.collision_events,
            "crashes": len(self.bus.crashes),
            "crashed_agents": sorted({c[1] for c in self.bus.crashes}),
            "contained_errors": len(self.bus.contained),
            "security_blocked": len(self.bus.security_events),
            "messages_dropped": self.bus.stats["dropped"],
            "peak_msgs_per_tick": max(self.bus.per_tick.values(), default=0),
            "injected_orders": om.injected,
            "quarantined_orders": om.quarantined,
            "false_done_rejected": sum(x.false_done_rejected for x in dispatchers),
            "overdue_revoked": sum(x.overdue_revoked for x in dispatchers),
            "bids_rejected": sum(x.bids_rejected for x in dispatchers),
            "bans": sum(x.bans for x in dispatchers),
            "stale_rejections": sum(r.stale_refusals for r in self.robots),
            "step_downs": sum(x.step_downs for x in dispatchers),
            "loops_detected": d.loops_detected,
            "stale_delegations": d.stale_deleg,
            "await_timeouts": sum(x.await_timeouts for x in dispatchers),
            "evacuated": len(self._evacuated),
            "slot_leaks_fixed": sum(c.leaks_fixed for c in self.chargers),
            "squatters_reported": sum(c.squatters_reported for c in self.chargers),
            "deleg_max_hops": max(self.bus.hops.values(), default=0),
            "failovers": self.monitor.failovers,
            "robot_failures_detected": len([x for x in self.monitor.detections if x[0].startswith("robot")]),
            "fault_log": list(self.fault_log),
        }
