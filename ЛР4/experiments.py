"""Серии экспериментов ЛР4: диверсионный анализ и оценка надёжности.

  python experiments.py

R1 — каждый сценарий без защиты (off) и со всеми защитными ограничениями (all), 10 зёрен;
     плюс эталон «штатная работа» в режиме делегирования (для сценария петли делегирования);
R2 — каждый сценарий только с ключевой защитой (key) — достаточна ли она одна, 10 зёрен;
R3 — сбор трасс (зерно 1, off и all) и прогон анализатора аномалий; выдержки трасс в results/traces/.
"""
from __future__ import annotations

import csv
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

from warehouse_mas.faults import ALL_DEFENSES, SCENARIOS, make_config
from warehouse_mas.simulation import Simulation
from warehouse_mas.trace_analysis import DETECTORS, analyze

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
FIELDS = ["series", "scenario", "defenses", "seed", "ticks", "legit_orders", "true_completed", "reported_completed",
          "false_completions", "goodput", "true_makespan", "true_lead_time", "duplicate_executions",
          "duplicate_reports", "collisions", "crashes", "contained_errors", "security_blocked", "messages_dropped",
          "messages_total", "peak_msgs_per_tick", "stranded", "injected_orders", "quarantined_orders",
          "false_done_rejected", "overdue_revoked", "bids_rejected", "bans", "stale_rejections", "step_downs",
          "loops_detected", "stale_delegations", "await_timeouts", "evacuated", "failovers", "slot_leaks_fixed",
          "squatters_reported", "deleg_max_hops", "robot_failures_detected", "wall_s"]


def jobs():
    out = []
    for sc in SCENARIOS:
        for label in ("off", "all"):
            for s in range(1, 11):
                out.append(("R1", sc, label, s, make_config(sc, label == "all", s)))
    for label in ("off", "all"):
        for s in range(1, 11):
            out.append(("R1", "clean_delegation", label, s, make_config("clean", label == "all", s, mode="delegation")))
    for sc, (_, _, _, key) in SCENARIOS.items():
        if key:
            for s in range(1, 11):
                out.append(("R2", sc, "key", s, make_config(sc, False, s, defenses=frozenset(key))))
    return out


def run_job(job):
    series, sc, label, seed, cfg = job
    t0 = time.perf_counter()
    m = Simulation(cfg).run()
    row = {k: m.get(k) for k in FIELDS if k in m}
    row.update(series=series, scenario=sc, defenses=label, seed=seed, wall_s=round(time.perf_counter() - t0, 2))
    return row


def trace_job(job):
    sc, label = job
    sim = Simulation(make_config(sc, label == "all", 1), keep_trace=True)
    m = sim.run()
    found = analyze(sim.monitor.trace, sim.env, sim.cfg.n_robots, sim.t)
    excerpt = []
    if label == "off":   # выдержка трассы: первые аномальные записи по каждому детектору
        ids = {}
        for det in ("A1_spoofed", "A2_unauthorized", "A3_unanswered", "A4_false_claim", "A10_malformed"):
            for x in found[det][:4]:          # эти детекторы возвращают номера сообщений
                ids[x] = det
        by_id = {r["id"]: r for r in sim.monitor.trace}
        for det in ("A5_multi_executor", "A6_multi_coordinator", "A11_stalled"):
            if found[det]:                    # эти — номера задач: берём сообщения по первой из них
                tid = found[det][0]
                rel = [r for r in sim.monitor.trace if r["protocol"] in ("auction", "task-report", "cnp")
                       and (r["content"].get("task_id") == tid
                            or tid in [t.get("task_id") for t in r["content"].get("tasks", [])]
                            or tid in r["content"].get("task_ids", []))
                       and r["performative"] != "cfp"][:12]
                ids.update({r["id"]: det for r in rel})
        if found["A7_loop"]:            # путь одной зациклившейся попытки делегирования
            did, tid = found["A7_loop"][0]
            loop = [r for r in sim.monitor.trace if r["content"].get("deleg_id") == did
                    and r["content"].get("action") == "delegate"][:20]
            ids.update({r["id"]: "A7_loop" for r in loop})
        excerpt = [dict(by_id[i], detector=d) for i, d in sorted(ids.items()) if i in by_id]
    return sc, label, {k: len(v) for k, v in found.items()}, excerpt, m["fault_log"], m["goodput"]


def main():
    os.makedirs(os.path.join(OUT, "traces"), exist_ok=True)
    js = jobs()
    print(f"прогонов: {len(js)}", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor() as ex:
        rows = list(ex.map(run_job, js, chunksize=2))
        traces = list(ex.map(trace_job, [(sc, lb) for sc in SCENARIOS for lb in ("off", "all")]))
    with open(os.path.join(OUT, "runs.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, "anomalies.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["scenario", "defenses", "goodput", *DETECTORS])
        w.writeheader()
        for sc, lb, counts, excerpt, flog, gp in traces:
            w.writerow({"scenario": sc, "defenses": lb, "goodput": gp, **counts})
            if excerpt or flog:
                with open(os.path.join(OUT, "traces", f"{sc}_{lb}.jsonl"), "w", encoding="utf-8") as tf:
                    for tick, ev in flog:
                        tf.write(json.dumps({"tick": tick, "fault_event": ev}, ensure_ascii=False) + "\n")
                    for rec in excerpt:
                        tf.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    print(f"готово за {time.time() - t0:.0f} c; защиты: {sorted(ALL_DEFENSES)}")


if __name__ == "__main__":
    main()
