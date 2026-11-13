"""Серии экспериментов ЛР3: поведение МАС до и после введения механизмов разрешения конфликта
за слоты зарядки.

  python experiments.py            # все серии -> results/E*.csv
  python experiments.py E1 E2

E1 — «до/после», все агенты честные: fifo, priority, negotiation (20 зёрен);
E2 — нечестные агенты: k ∈ {0,1,2,3} роботов занижают заряд и не уступают слот;
     fifo, priority, negotiation, negotiation+rep (10 зёрен);
E3 — острота дефицита: скорость зарядки 2..6 ед./такт (10 зёрен);
E4 — параметры механизмов: порог досрочного освобождения (negotiation), скорость старения (priority).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

from warehouse_mas.config import Config
from warehouse_mas.simulation import Simulation

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
VARIANTS = {"fifo": ("fifo", False), "priority": ("priority", False),
            "negotiation": ("negotiation", False), "negotiation+rep": ("negotiation", True)}
FIELDS = ["experiment", "variant", "param", "value", "seed", "liars", "ticks", "orders_completed", "makespan",
          "avg_order_lead_time", "max_order_lead_time", "express_hit", "standard_hit", "deadline_violations",
          "charge_wait_mean", "charge_wait_p95", "charge_wait_max", "charge_wait_critical", "charge_wait_honest",
          "charge_wait_liars", "jain_charge_wait", "charging_sessions", "offers_made", "offers_accepted",
          "lies_detected", "sanctions", "emergency_charges", "min_battery", "stranded", "revocations",
          "tasks_done", "tasks_done_liars", "utilization", "messages_total", "charge_wait_by_robot",
          "charge_sessions_by_robot", "wall_s"]


def cfg(variant, seed, **kw):
    policy, rep = VARIANTS[variant]
    return Config(seed=seed, charge_policy=policy, reputation=rep, **kw)


def plan(names):
    jobs = []
    if "E1" in names:
        for v in ("fifo", "priority", "negotiation"):
            for s in range(1, 21):
                jobs.append(("E1", v, "-", "-", 0, cfg(v, s)))
    if "E2" in names:
        for k in (0, 1, 2, 3):
            for v in VARIANTS:
                for s in range(1, 11):
                    jobs.append(("E2", v, "liars", k, k, cfg(v, s, liars=[f"robot_{i + 1}" for i in range(k)])))
    if "E3" in names:
        for cr in (2, 3, 4, 6):
            for v in ("fifo", "priority", "negotiation"):
                for s in range(1, 11):
                    jobs.append(("E3", v, "charge_rate", cr, 0, cfg(v, s, charge_rate=cr)))
    if "E4" in names:
        for x in (0.5, 0.6, 0.75, 0.9):
            for s in range(1, 11):
                c = cfg("negotiation", s)
                jobs.append(("E4", "negotiation", "release_min", x, 0,
                             replace(c, release_min=x, yield_accept_level=max(x, c.yield_accept_level))))
        for x in (0.0, 0.5, 1.0, 3.0):
            for s in range(1, 11):
                jobs.append(("E4", "priority", "aging_rate", x, 0, replace(cfg("priority", s), aging_rate=x)))
    return jobs


def run_job(job):
    exp, variant, param, value, k, c = job
    t0 = time.perf_counter()
    m = Simulation(c).run()
    row = {f: m.get(f) for f in FIELDS if f in m}
    row.update(experiment=exp, variant=variant, param=param, value=value, seed=c.seed, liars=k,
               charge_wait_by_robot=json.dumps(m["charge_wait_by_robot"]),
               charge_sessions_by_robot=json.dumps(m["charge_sessions_by_robot"]),
               wall_s=round(time.perf_counter() - t0, 2))
    return row


def main():
    names = sys.argv[1:] or ["E1", "E2", "E3", "E4"]
    os.makedirs(OUT, exist_ok=True)
    jobs = plan(names)
    print(f"прогонов: {len(jobs)}", flush=True)
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor() as ex:
        for i, row in enumerate(ex.map(run_job, jobs, chunksize=2), 1):
            rows.append(row)
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)}  {time.time() - t0:.0f} c", flush=True)
    for exp in names:
        path = os.path.join(OUT, f"{exp}.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(r for r in rows if r["experiment"] == exp)
        print(f"сохранено: {path}")
    print(f"готово за {time.time() - t0:.0f} c")


if __name__ == "__main__":
    main()
