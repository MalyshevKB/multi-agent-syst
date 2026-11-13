"""Сборка МАС и дискретно-событийный цикл моделирования."""
from __future__ import annotations

import random
from statistics import mean

from .agents import (ChargingStationAgent, DispatcherAgent, MonitorAgent, OrderManagerAgent,
                     RobotAgent, TrafficControllerAgent)
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
        self.env = Warehouse(build_layout(cfg.charger_slots))
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
        wait = {"charger_W": [(x, 0) for x in range(cfg.charger_slots + 1, cfg.charger_slots + 5)],
                "charger_E": [(w - 1 - x, 0) for x in range(cfg.charger_slots + 1, cfg.charger_slots + 5)]}

        # Порядок создания = порядок действий агентов на такте
        self.order_manager = OrderManagerAgent(self.bus, self.env, cfg, random.Random(cfg.seed))
        self.dispatcher = DispatcherAgent(self.bus, self.env, cfg, positions)
        self.traffic = TrafficControllerAgent(self.bus, positions)
        self.chargers = [ChargingStationAgent(sid, self.bus, slots, wait[sid]) for sid, slots in stations.items()]
        self.robots = [RobotAgent(rid, self.bus, self.env, cfg, positions[rid], positions[rid], stations,
                                  random.Random(cfg.seed * 1000 + i))
                       for i, rid in enumerate(robot_ids)]
        self.monitor = MonitorAgent(self.bus, cfg, robot_ids,
                                    notify=["dispatcher", "traffic", *stations.keys()], keep_trace=keep_trace)
        self.agents = [self.order_manager, self.dispatcher, self.traffic, *self.chargers, *self.robots, self.monitor]
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
        coord = sum(v for k, v in self.bus.by_protocol.items() if k in ("central-assign", "cnp", "status"))
        return {
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
            "distance_total": sum(r.distance for r in self.robots),
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
