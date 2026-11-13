"""Демонстрация ЛР3: конфликт за слоты зарядки и механизмы его разрешения.

Примеры:
  python run.py --policy all                      # FIFO / приоритет / переговоры / переговоры+репутация
  python run.py --policy all --liars 2            # то же, двое роботов лгут о заряде и не уступают
  python run.py --policy negotiation --reputation --liars 2 --render 50
  python run.py --policy negotiation --trace trace.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys

from warehouse_mas.config import Config
from warehouse_mas.render import render
from warehouse_mas.simulation import Simulation

VARIANTS = {"fifo": ("fifo", False), "priority": ("priority", False),
            "negotiation": ("negotiation", False), "negotiation+rep": ("negotiation", True)}


def parse_args():
    p = argparse.ArgumentParser(description="МАС складских роботов — ЛР3: переговоры и разрешение конфликтов")
    p.add_argument("--policy", choices=["fifo", "priority", "negotiation", "all"], default="all",
                   help="политика распределения слотов зарядки; all — сравнение всех вариантов")
    p.add_argument("--reputation", action="store_true", help="включить проверку заявок, репутацию и санкции")
    p.add_argument("--liars", type=int, default=0, help="число роботов, занижающих заявленный заряд")
    p.add_argument("--mode", choices=["central", "cnp", "auction", "delegation"], default="auction",
                   help="механизм распределения задач (из ЛР2)")
    p.add_argument("--robots", type=int, default=8)
    p.add_argument("--orders", type=int, default=100)
    p.add_argument("--rate", type=float, default=0.1)
    p.add_argument("--charge-rate", type=int, default=3)
    p.add_argument("--stations", type=int, choices=[1, 2], default=2)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--render", type=int, default=0, metavar="N")
    p.add_argument("--trace", help="файл JSONL для трассы сообщений")
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def make_config(a, policy, rep) -> Config:
    return Config(mode=a.mode, n_robots=a.robots, n_orders=a.orders, order_rate=a.rate, seed=a.seed,
                  charge_rate=a.charge_rate, n_stations=a.stations, charge_policy=policy, reputation=rep,
                  liars=[f"robot_{i + 1}" for i in range(a.liars)])


def run_one(a, name, policy, rep) -> dict:
    sim = Simulation(make_config(a, policy, rep), keep_trace=bool(a.trace))

    def on_tick(s):
        if a.render and s.t % a.render == 0:
            print(render(s), end="\n\n")

    m = sim.run(on_tick)
    if a.trace:
        path = a.trace.replace(".jsonl", f"_{name}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for rec in sim.monitor.trace:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"трасса: {path}")
    return m


LABELS = [
    ("orders_completed", "выполнено заказов"), ("makespan", "время завершения всех заказов"),
    ("avg_order_lead_time", "среднее время заказа"), ("express_hit", "экспресс в срок, доля"),
    ("standard_hit", "обычные в срок, доля"),
    ("charge_wait_mean", "ожидание зарядки, среднее"), ("charge_wait_p95", "ожидание зарядки, p95"),
    ("charge_wait_max", "ожидание зарядки, макс."), ("charge_wait_critical", "ожидание при низком заряде"),
    ("charge_wait_honest", "ожидание честных"), ("charge_wait_liars", "ожидание лжецов"),
    ("jain_charge_wait", "индекс Джейна (ожидание)"), ("charging_sessions", "сеансов зарядки"),
    ("offers_made", "предложений уступить"), ("offers_accepted", "уступок принято"),
    ("lies_detected", "выявлено ложных заявок"), ("sanctions", "санкций"),
    ("emergency_charges", "аварийных уходов на зарядку"), ("min_battery", "мин. заряд робота"),
    ("stranded", "разряженных роботов"), ("revocations", "отзывов слота"),
]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a = parse_args()
    if a.policy == "all":
        names = list(VARIANTS)
    else:
        names = [a.policy + ("+rep" if a.reputation and a.policy == "negotiation" else "")]
    variants = {n: VARIANTS.get(n, (a.policy, a.reputation)) for n in names}
    results = {n: run_one(a, n, *v) for n, v in variants.items()}
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return
    w = 34
    print(f"{'показатель':<{w}}" + "".join(f"{n:>17}" for n in names))
    for key, label in LABELS:
        print(f"{label:<{w}}" + "".join(f"{str(results[n][key]):>17}" for n in names))


if __name__ == "__main__":
    main()
