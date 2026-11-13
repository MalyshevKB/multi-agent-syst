"""Анализ результатов ЛР4: сводные таблицы (results/summary.md) и графики (figures/*.png).

  python analyze.py
"""
from __future__ import annotations

import csv
import math
import os
from collections import defaultdict
from statistics import mean, stdev

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from warehouse_mas.faults import SCENARIOS  # noqa: E402
from warehouse_mas.trace_analysis import DETECTORS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RES, FIG = os.path.join(HERE, "results"), os.path.join(HERE, "figures")
ORDER = [s for s in SCENARIOS if s != "clean"]
NAME = {s: v[0] for s, v in SCENARIOS.items()} | {"clean_delegation": "Штатная работа (делегирование)"}
CLASS = {s: v[1] for s, v in SCENARIOS.items()}
LAB = {"off": "Без защиты", "key": "Только ключевая защита", "all": "Все защиты"}
# палитра проверена валидатором (первые три слота: CVD / normal-vision OK, all-pairs)
COLOR = {"off": "#2a78d6", "key": "#eb6834", "all": "#1baf7a"}
MARK = {"off": "o", "key": "s", "all": "^"}
INK, INK2, GRID, BG = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold", "axes.titlesize": 10,
    "axes.titlecolor": INK, "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "legend.frameon": False,
})


def load(name):
    with open(os.path.join(RES, name), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in r.items():
            if k in ("series", "scenario", "defenses"):
                continue
            r[k] = None if v in ("", "None") else float(v)
    return rows


# двусторонний 95 % квантиль t-распределения Стьюдента по числу степеней свободы
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
       10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
       18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
       26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}


def t95(df):
    return T95.get(df, 2.042 if df <= 40 else 1.96)


def ci(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return float("nan"), 0.0
    if len(xs) < 2:
        return xs[0], 0.0
    return mean(xs), t95(len(xs) - 1) * stdev(xs) / math.sqrt(len(xs))


def f(m, h, nd=2):
    return "—" if m != m else f"{m:.{nd}f} ± {h:.{nd}f}"


def tot(rows, k):
    return int(sum(r[k] or 0 for r in rows))


def main():
    os.makedirs(FIG, exist_ok=True)
    rows = load("runs.csv")
    g = defaultdict(list)
    for r in rows:
        g[(r["scenario"], r["defenses"])].append(r)
    out = ["# Сводка результатов ЛР4\n"]

    # ------------------------------------------------------------ T1: устойчивость
    out.append("## T1. Полезная работа (доля легитимных заказов, физически выполненных за 2000 тактов), "
               "среднее ± 95% ДИ, 10 зёрен\n")
    out.append("| Сценарий | Класс | Без защиты | Только ключевая | Все защиты |")
    out.append("|---|---|---|---|---|")
    for sc in ["clean"] + ORDER + ["clean_delegation"]:
        cells = [f(*ci([r["goodput"] for r in g[(sc, lb)]])) if g[(sc, lb)] else "—" for lb in ("off", "key", "all")]
        out.append(f"| {NAME[sc]} | {CLASS.get(sc, '—')} | " + " | ".join(cells) + " |")

    out.append("\n## T2. Нарушения целостности и безопасности (сумма за 10 прогонов: без защиты → все защиты)\n")
    keys = [("false_completions", "Ложно выполненные заказы"), ("duplicate_executions", "Повторные выполнения"),
            ("crashes", "Падения агентов"), ("collisions", "Столкновения"), ("stranded", "Разряженные роботы")]
    out.append("| Сценарий | " + " | ".join(k[1] for k in keys) + " | Сообщений (ср.) | Makespan (ср., успешные) |")
    out.append("|---|" + "---|" * (len(keys) + 2))
    for sc in ["clean"] + ORDER + ["clean_delegation"]:
        a, b = g[(sc, "off")], g[(sc, "all")]
        cells = [f"{tot(a, k)} → {tot(b, k)}" for k, _ in keys]
        msg = f"{mean(r['messages_total'] for r in a):.0f} → {mean(r['messages_total'] for r in b):.0f}"
        mk = [r["true_makespan"] for r in a if r["true_makespan"]], [r["true_makespan"] for r in b if r["true_makespan"]]
        mks = (f"{mean(mk[0]):.0f}" if mk[0] else "не завершено") + " → " + (f"{mean(mk[1]):.0f}" if mk[1] else "—")
        out.append(f"| {NAME[sc]} | " + " | ".join(cells) + f" | {msg} | {mks} |")

    out.append("\n## T3. Срабатывания защитных ограничений (среднее за прогон, все защиты)\n")
    acts = [("security_blocked", "отброшено подделок/без полномочий"), ("quarantined_orders", "заказов в карантине"),
            ("contained_errors", "изолированных ошибок"), ("false_done_rejected", "отклонено ложных отчётов"),
            ("bids_rejected", "отклонено ставок"), ("overdue_revoked", "отозвано просрочек"), ("bans", "карантинов роботов"),
            ("stale_rejections", "отказов по эпохе"), ("step_downs", "сложений полномочий"),
            ("loops_detected", "обнаружено петель"), ("stale_delegations", "отозвано устаревших делегирований"),
            ("await_timeouts", "тайм-аутов подтверждения"), ("slot_leaks_fixed", "исправлено утечек слота"),
            ("squatters_reported", "захватов слота"), ("evacuated", "эвакуаций"), ("failovers", "переключений на резерв"),
            ("deleg_max_hops", "макс. длина круга делегирования")]
    out.append("| Сценарий | Сработавшие защиты (среднее за прогон) |")
    out.append("|---|---|")
    for sc in ["clean"] + ORDER:
        b = g[(sc, "all")]
        items = [f"{lab} {mean(r[k] or 0 for r in b):.1f}" for k, lab in acts if mean(r[k] or 0 for r in b) >= 0.1]
        out.append(f"| {NAME[sc]} | " + "; ".join(items) + " |")
    a = g[("delegation_loop", "off")]
    out.append(f"\nБез защиты в сценарии петли делегирования: макс. длина круга "
               f"{mean(r['deleg_max_hops'] for r in a):.1f} (в эталоне делегирования "
               f"{mean(r['deleg_max_hops'] for r in g[('clean_delegation', 'off')]):.1f}).\n")

    # ------------------------------------------------------------ T4: аномалии по трассам
    an = list(csv.DictReader(open(os.path.join(RES, "anomalies.csv"), encoding="utf-8")))
    out.append("## T4. Выявление отказов по трассам (зерно 1): число срабатываний детекторов\n")
    out.append("| Сценарий | Защита | " + " | ".join(k.split("_")[0] for k in DETECTORS) + " |")
    out.append("|---|---|" + "---|" * len(DETECTORS))
    for r in an:
        out.append(f"| {NAME[r['scenario']]} | {r['defenses']} | " + " | ".join(r[k] for k in DETECTORS) + " |")
    out.append("\n" + "\n".join(f"- **{k.split('_')[0]}** — {v}" for k, v in DETECTORS.items()) + "\n")

    # ------------------------------------------------------------ T5: накладные расходы
    out.append("## T5. Стоимость защиты в штатной работе (10 зёрен)\n")
    out.append("| Режим | Makespan без защиты | Makespan с защитой | Сообщений без | Сообщений с | Пик сообщений/такт без → с |")
    out.append("|---|---|---|---|---|---|")
    for sc in ("clean", "clean_delegation"):
        a, b = g[(sc, "off")], g[(sc, "all")]
        out.append(f"| {NAME[sc]} | {f(*ci([r['true_makespan'] for r in a]), 0)} | {f(*ci([r['true_makespan'] for r in b]), 0)} | "
                   f"{mean(r['messages_total'] for r in a):.0f} | {mean(r['messages_total'] for r in b):.0f} | "
                   f"{mean(r['peak_msgs_per_tick'] for r in a):.0f} → {mean(r['peak_msgs_per_tick'] for r in b):.0f} |")
    with open(os.path.join(RES, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")

    # ------------------------------------------------------------ Рис. 1: полезная работа
    fig, ax = plt.subplots(figsize=(11, 6.2))
    ys = list(range(len(ORDER)))[::-1]
    for y, sc in zip(ys, ORDER):
        vals = {lb: ci([r["goodput"] for r in g[(sc, lb)]])[0] for lb in ("off", "key", "all")}
        ax.plot([vals["off"], vals["all"]], [y, y], color=GRID, lw=3, zorder=1)
        for lb in ("off", "key", "all"):
            m, h = ci([r["goodput"] for r in g[(sc, lb)]])
            ax.errorbar(m, y + {"off": 0, "key": -0.18, "all": 0}[lb], xerr=h, fmt=MARK[lb], ms=8, color=COLOR[lb],
                        mec=BG, mew=1.5, ecolor=COLOR[lb], elinewidth=1, capsize=2, zorder=3,
                        label=LAB[lb] if y == ys[0] else None)
        ax.annotate(f"{vals['off']:.0%}", (vals["off"], y), xytext=(-16, 0), textcoords="offset points",
                    ha="right", va="center", fontsize=8.5, color=INK2)
        ax.annotate(f"{vals['all']:.0%}", (vals["all"], y), xytext=(14, 0), textcoords="offset points",
                    ha="left", va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(ys, [f"{NAME[s]}\n({CLASS[s]})" for s in ORDER], fontsize=8.5)
    ax.set_xlim(-0.08, 1.12)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0], ["0 %", "25 %", "50 %", "75 %", "100 %"])
    ax.set_xlabel("Полезная работа: доля легитимных заказов, физически выполненных за 2000 тактов")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, fontsize=9)
    fig.suptitle("Рис. 1. Устойчивость до и после введения защитных ограничений (10 зёрен, планки — 95% ДИ)",
                 x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(os.path.join(FIG, "fig1_goodput.png"), dpi=150)
    plt.close(fig)

    # ------------------------------------------------------------ Рис. 2: карта детекторов
    rows_an = [r for r in an if r["defenses"] == "off"]
    dets = list(DETECTORS)
    mat = [[int(r[d]) for d in dets] for r in rows_an]
    fig, ax = plt.subplots(figsize=(12, 6))
    ramp = ["#f1f0ec", "#cfe0f6", "#9cc2ee", "#5f9ee3", "#2a78d6", "#1a56a3"]

    def shade(v):
        if v == 0:
            return ramp[0]
        return ramp[min(5, 1 + int(math.log10(v) * 1.4))]
    for i, row in enumerate(mat):
        for j, v in enumerate(row):
            ax.add_patch(plt.Rectangle((j, i), 0.96, 0.92, color=shade(v), lw=0))
            ax.text(j + 0.48, i + 0.46, str(v) if v else "·", ha="center", va="center", fontsize=8.5,
                    color="#ffffff" if v >= 300 else INK)
    ax.set_xlim(0, len(dets))
    ax.set_ylim(len(rows_an), 0)
    ax.set_xticks([j + 0.48 for j in range(len(dets))], [d.split("_")[0] for d in dets])
    ax.set_yticks([i + 0.46 for i in range(len(rows_an))], [NAME[r["scenario"]] for r in rows_an], fontsize=8.5)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    fig.suptitle("Рис. 2. Выявление отказов по трассам без защиты: срабатывания детекторов A1–A11 (зерно 1)",
                 x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.text(0.01, 0.01, "  ".join(f"{k.split('_')[0]} — {v.split(' (')[0].lower()}" for k, v in DETECTORS.items()),
             fontsize=7.2, color=INK2, wrap=True)
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    fig.savefig(os.path.join(FIG, "fig2_detectors.png"), dpi=150)
    plt.close(fig)
    print("results/summary.md, figures/*.png")


if __name__ == "__main__":
    main()
