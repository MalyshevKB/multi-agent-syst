"""Запуск демонстрации МАС складских роботов.

Примеры:
  python run.py                               # централизованный режим, итоговые метрики
  python run.py --mode cnp --render 20        # контрактная сеть, карта каждые 20 тактов
  python run.py --mode both                   # сравнение режимов на одном потоке заказов
  python run.py --fail robot_2:150            # отказ робота на 150-м такте
  python run.py --trace trace.jsonl           # трасса всех сообщений
"""
from __future__ import annotations

import argparse
import json
import sys

from warehouse_mas.config import Config
from warehouse_mas.render import render
from warehouse_mas.simulation import Simulation


def parse_args():
    p = argparse.ArgumentParser(description="МАС координации складских роботов")
    p.add_argument("--mode", choices=["central", "hungarian", "cnp", "auction", "delegation", "all"], default="central",
                   help="механизм распределения задач; all — сравнение всех на одном потоке заказов")
    p.add_argument("--robots", type=int, default=6)
    p.add_argument("--orders", type=int, default=40)
    p.add_argument("--rate", type=float, default=0.12, help="интенсивность поступления заказов")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--max-ticks", type=int, default=3000)
    p.add_argument("--charger-slots", type=int, default=1)
    p.add_argument("--battery", type=int, default=120)
    p.add_argument("--fail", action="append", default=[], metavar="ROBOT:TICK")
    p.add_argument("--render", type=int, default=0, metavar="N", help="печатать карту каждые N тактов")
    p.add_argument("--trace", help="файл JSONL для трассы сообщений")
    p.add_argument("--json", action="store_true", help="вывести метрики в JSON")
    return p.parse_args()


def make_config(a, mode) -> Config:
    failures = []
    for f in a.fail:
        rid, tick = f.split(":")
        failures.append((rid, int(tick)))
    return Config(mode=mode, n_robots=a.robots, n_orders=a.orders, order_rate=a.rate, seed=a.seed,
                  max_ticks=a.max_ticks, charger_slots=a.charger_slots, battery_capacity=a.battery,
                  failures=failures)


def run_one(a, mode) -> dict:
    sim = Simulation(make_config(a, mode), keep_trace=bool(a.trace))
    printed = set()

    def on_tick(s):
        for ev in s.events:
            if ev not in printed:
                printed.add(ev)
                print(f"[такт {ev[0]}] {ev[1]}")
        if a.render and s.t % a.render == 0:
            print(render(s), end="\n\n")

    m = sim.run(on_tick)
    if a.render:
        print(render(sim), end="\n\n")
    if a.trace:
        path = a.trace if a.mode != "all" else a.trace.replace(".jsonl", f"_{mode}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for rec in sim.monitor.trace:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"трасса сообщений: {path} ({len(sim.monitor.trace)} записей)")
    return m


LABELS = [
    ("ticks", "тактов моделирования"), ("orders_completed", "выполнено заказов"),
    ("avg_order_lead_time", "среднее время выполнения заказа"), ("max_order_lead_time", "макс. время заказа"),
    ("makespan", "время завершения последнего заказа"), ("tasks_done", "выполнено задач"),
    ("messages_total", "всего сообщений"), ("messages_coordination", "сообщений на распределение"),
    ("coordination_msgs_per_task", "сообщений распределения на задачу"),
    ("coordination_cpu_ms", "время ЦП на координацию, мс"),
    ("allocation_latency", "задержка распределения, тактов"),
    ("empty_distance_total", "пробег без груза"), ("jain_fairness", "индекс Джейна (баланс)"),
    ("distance_total", "суммарный пробег"), ("wait_ticks_total", "тактов ожидания в пробках"),
    ("replans_total", "перепланирований"), ("yield_requests", "запросов уступить дорогу"),
    ("utilization", "загрузка роботов"), ("charging_sessions", "сеансов зарядки"),
    ("charging_wait_ticks", "ожидание зарядки, тактов"), ("reassigned_tasks", "переназначено задач"),
    ("failures_detected", "обнаружено отказов"), ("stranded_robots", "разряженные роботы"),
]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a = parse_args()
    modes = ["central", "hungarian", "cnp", "auction", "delegation"] if a.mode == "all" else [a.mode]
    results = {m: run_one(a, m) for m in modes}
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return
    width = 36
    print(f"{'показатель':<{width}}" + "".join(f"{m:>12}" for m in modes))
    for key, label in LABELS:
        print(f"{label:<{width}}" + "".join(f"{str(results[m][key]):>12}" for m in modes))
    for m in modes:
        print(f"\n[{m}] сообщения по протоколам: {results[m]['messages_by_protocol']}")
        print(f"[{m}] задач на робота: {results[m]['tasks_per_robot']}")


if __name__ == "__main__":
    main()
