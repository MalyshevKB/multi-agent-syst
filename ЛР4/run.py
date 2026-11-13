"""Демонстрация ЛР4: неблагоприятные сценарии, защитные ограничения, анализ трасс.

Примеры:
  python run.py --list                                  # каталог сценариев
  python run.py --scenario spoofing                     # без защиты и с защитой, сравнение
  python run.py --scenario all                          # все сценарии
  python run.py --scenario msg_loss --defenses off --render 200
  python run.py --scenario split_brain --trace trace.jsonl   # трасса + отчёт анализатора аномалий
"""
from __future__ import annotations

import argparse
import json
import sys

from warehouse_mas.faults import SCENARIOS, make_config
from warehouse_mas.render import render
from warehouse_mas.simulation import Simulation
from warehouse_mas.trace_analysis import DETECTORS, analyze

LABELS = [
    ("legit_orders", "легитимных заказов"), ("true_completed", "физически выполнено"),
    ("reported_completed", "отчитано как выполненные"), ("false_completions", "ложно выполненных"),
    ("goodput", "полезная работа (за 2000 тактов)"), ("true_makespan", "время выполнения всех"),
    ("duplicate_executions", "повторных выполнений"), ("collisions", "столкновений"),
    ("crashes", "падений агентов"), ("stranded", "разряженных роботов"), ("messages_total", "сообщений"),
    ("peak_msgs_per_tick", "пик сообщений за такт"), ("security_blocked", "отброшено (auth)"),
    ("quarantined_orders", "заказов в карантине"), ("false_done_rejected", "отклонено ложных отчётов"),
    ("bids_rejected", "отклонено ставок"), ("bans", "карантинов роботов"), ("step_downs", "сложений полномочий"),
    ("loops_detected", "обнаружено петель"), ("evacuated", "эвакуаций"), ("failovers", "переключений на резерв"),
]


def parse_args():
    p = argparse.ArgumentParser(description="МАС складских роботов — ЛР4: диверсионный анализ")
    p.add_argument("--scenario", default="spoofing", help="id сценария или all")
    p.add_argument("--defenses", choices=["off", "on", "both"], default="both")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--render", type=int, default=0, metavar="N")
    p.add_argument("--trace", help="файл JSONL для трассы; печатается отчёт анализатора аномалий")
    p.add_argument("--list", action="store_true")
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def run(a, sc, defended):
    sim = Simulation(make_config(sc, defended, a.seed), keep_trace=bool(a.trace))

    def on_tick(s):
        if a.render and s.t % a.render == 0:
            print(render(s), end="\n\n")

    m = sim.run(on_tick)
    for tick, ev in m["fault_log"]:
        print(f"  [такт {tick}] {ev}")
    if a.trace:
        path = a.trace.replace(".jsonl", f"_{sc}_{'on' if defended else 'off'}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for rec in sim.monitor.trace:
                fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        found = analyze(sim.monitor.trace, sim.env, sim.cfg.n_robots, sim.t)
        print(f"  трасса: {path} ({len(sim.monitor.trace)} записей); детекторы аномалий:")
        for k, v in found.items():
            if v:
                print(f"    {k}: {len(v)} — {DETECTORS[k]}")
    return m


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a = parse_args()
    if a.list:
        for sc, (name, cls, _, key) in SCENARIOS.items():
            print(f"{sc:16s} {name} [{cls}]; ключевая защита: {', '.join(key) or '—'}")
        return
    scenarios = list(SCENARIOS) if a.scenario == "all" else [a.scenario]
    modes = {"off": [False], "on": [True], "both": [False, True]}[a.defenses]
    for sc in scenarios:
        print(f"\n=== {sc}: {SCENARIOS[sc][0]} ({SCENARIOS[sc][1]})")
        res = {}
        for d in modes:
            print(f" защита {'включена' if d else 'выключена'}:")
            res["с защитой" if d else "без защиты"] = run(a, sc, d)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
            continue
        names = list(res)
        print(f"  {'показатель':<34}" + "".join(f"{n:>14}" for n in names))
        for key, label in LABELS:
            print(f"  {label:<34}" + "".join(f"{str(res[n][key]):>14}" for n in names))


if __name__ == "__main__":
    main()
