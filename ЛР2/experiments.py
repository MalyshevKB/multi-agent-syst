"""Серии экспериментов ЛР2: сравнение механизмов распределения задач.

  python experiments.py            # все серии, результаты в results/*.csv
  python experiments.py E1 E2      # только выбранные серии

E1 — базовый сценарий (6 роботов, 40 заказов, p=0.12), 20 зёрен;
E2 — масштабирование по числу роботов 4..12;
E3 — интенсивность потока заказов p = 0.06..0.30;
E4 — чувствительность к параметрам механизмов (период аукциона, порог делегирования, пауза CNP).
"""
from __future__ import annotations

import csv
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

from warehouse_mas.config import Config
from warehouse_mas.simulation import Simulation

MODES = ["central", "hungarian", "cnp", "auction", "delegation"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
FIELDS = ["experiment", "mode", "param", "value", "seed", "ticks", "orders_completed", "avg_order_lead_time",
          "max_order_lead_time", "makespan", "tasks_done", "messages_total", "messages_coordination",
          "coordination_msgs_per_task", "coordination_cpu_ms", "allocation_latency", "allocation_rounds",
          "distance_total", "empty_distance_total", "wait_ticks_total", "utilization", "tasks_std",
          "jain_fairness", "charging_wait_ticks", "reassigned_tasks", "stranded", "wall_s"]


def plan(names):
    jobs = []
    if "E1" in names:
        for m in MODES:
            for s in range(1, 21):
                jobs.append(("E1", m, "-", "-", Config(mode=m, seed=s)))
    if "E2" in names:
        for n in (4, 6, 8, 10, 12):
            for m in MODES:
                for s in range(1, 11):
                    jobs.append(("E2", m, "n_robots", n, Config(mode=m, seed=s, n_robots=n)))
    if "E3" in names:
        for rate in (0.06, 0.12, 0.2, 0.3):
            for m in MODES:
                for s in range(1, 11):
                    jobs.append(("E3", m, "order_rate", rate, Config(mode=m, seed=s, order_rate=rate)))
    if "E4" in names:
        sweeps = [("auction", "auction_period", (1, 3, 5, 10, 20)),
                  ("delegation", "deleg_threshold", (20, 40, 60, 100)),
                  ("cnp", "cnp_retry", (1, 3, 6, 10))]
        for m, param, values in sweeps:
            for v in values:
                for s in range(1, 11):
                    jobs.append(("E4", m, param, v, replace(Config(mode=m, seed=s), **{param: v})))
    return jobs


def run_job(job):
    exp, mode, param, value, cfg = job
    t0 = time.perf_counter()
    m = Simulation(cfg).run()
    row = {k: m.get(k) for k in FIELDS if k in m}
    row.update(experiment=exp, mode=mode, param=param, value=value, seed=cfg.seed,
               stranded=len(m["stranded_robots"]), wall_s=round(time.perf_counter() - t0, 2))
    return row


def main():
    names = sys.argv[1:] or ["E1", "E2", "E3", "E4"]
    os.makedirs(OUT, exist_ok=True)
    jobs = plan(names)
    print(f"прогонов: {len(jobs)}")
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
