"""Сборка МАС и дискретно-событийный цикл моделирования."""
from __future__ import annotations

import random
import time
from statistics import mean, pstdev

from .messages import COORD_PROTOCOLS

from .agents import (ChargingStationAgent, DispatcherAgent, MonitorAgent, OrderManagerAgent,
                     ReputationAgent, RobotAgent, TrafficControllerAgent)
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
        n_wait = 4 if cfg.n_stations > 1 else min(cfg.n_robots, w - cfg.charger_slots - 2)
        wait = {"charger_W": [(x, 0) for x in range(cfg.charger_slots + 1, cfg.charger_slots + 1 + n_wait)],
                "charger_E": [(w - 1 - x, 0) for x in range(cfg.charger_slots + 1, cfg.charger_slots + 1 + n_wait)]}

        # Порядок создания = порядок действий агентов на такте
        self.order_manager = OrderManagerAgent(self.bus, self.env, cfg, random.Random(cfg.seed))
        self.dispatcher = DispatcherAgent(self.bus, self.env, cfg, positions)
        self.traffic = TrafficControllerAgent(self.bus, positions)
        # измеритель заряда на разъёме станции (физический датчик, а не сообщение робота)
        meter = lambda rid: self._by_id[rid].battery  # noqa: E731
        self.chargers = [ChargingStationAgent(sid, self.bus, cfg, slots, wait[sid], meter)
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
        self._by_id = {a.id: a for a in self.agents}

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
        for a in self.agents:
            if a.alive:
                if a is self.dispatcher:
                    t0 = time.perf_counter()
                    a.step(t)
                    self.dispatcher_cpu += time.perf_counter() - t0
                else:
                    a.step(t)
        self.bus.deliver()
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
        return lab3 | {
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
