"""Анализ результатов ЛР2: сводные таблицы (results/summary.md) и графики (figures/*.png).

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

HERE = os.path.dirname(os.path.abspath(__file__))
RES, FIG = os.path.join(HERE, "results"), os.path.join(HERE, "figures")
MODES = ["central", "hungarian", "cnp", "auction", "delegation"]
LABEL = {"central": "Централизованный", "hungarian": "Венгерский алгоритм", "cnp": "Контрактная сеть",
         "auction": "Пакетный аукцион", "delegation": "Делегирование"}
SHORT = {"central": "Центра-\nлизованный", "hungarian": "Венгерский\nалгоритм", "cnp": "Контракт-\nная сеть",
         "auction": "Пакетный\nаукцион", "delegation": "Делеги-\nрование"}
# Категориальная палитра (проверена валидатором: CVD/normal-vision OK; низкий контраст светлых
# цветов компенсирован прямыми подписями, маркерами и таблицами). Цвет закреплён за механизмом.
COLOR = {"central": "#2a78d6", "hungarian": "#e87ba4", "cnp": "#eb6834", "auction": "#1baf7a",
         "delegation": "#eda100"}
MARKER = {"central": "o", "hungarian": "v", "cnp": "s", "auction": "^", "delegation": "D"}
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
# двусторонний 95 % квантиль t-распределения Стьюдента по числу степеней свободы
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
       10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
       18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
       26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}


def t95(df):
    return T95.get(df, 2.042 if df <= 40 else 1.96)

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold",
    "axes.titlesize": 11, "axes.titlecolor": INK, "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb",
    "savefig.facecolor": "#fcfcfb", "legend.frameon": False,
})


def load(name):
    with open(os.path.join(RES, f"{name}.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in r.items():
            if k in ("experiment", "mode", "param"):
                continue
            try:
                r[k] = float(v) if v not in ("", "None", "-") else (None if v != "-" else v)
            except ValueError:
                pass
    return rows


def ci(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return (xs[0] if xs else float("nan")), 0.0
    t = t95(len(xs) - 1)
    return mean(xs), t * stdev(xs) / math.sqrt(len(xs))


def succ(rows):
    """Метрики усредняются только по успешным прогонам (выполнены все заказы)."""
    return [r for r in rows if r["orders_completed"] == 40]


def group(rows, *keys):
    g = defaultdict(list)
    for r in rows:
        g[tuple(r[k] for k in keys)].append(r)
    return g


def fmt(m, h, nd=1):
    return f"{m:.{nd}f} ± {h:.{nd}f}"


# ---------------------------------------------------------------- E1
E1_METRICS = [
    ("makespan", "Время завершения всех заказов, тактов", 0),
    ("avg_order_lead_time", "Среднее время выполнения заказа, тактов", 1),
    ("max_order_lead_time", "Максимальное время заказа, тактов", 0),
    ("allocation_latency", "Задержка распределения задачи, тактов", 1),
    ("coordination_msgs_per_task", "Сообщений распределения на задачу", 1),
    ("messages_total", "Всего сообщений", 0),
    ("coordination_cpu_ms", "Время ЦП на координацию, мс", 1),
    ("empty_distance_total", "Пробег без груза, клеток", 0),
    ("distance_total", "Суммарный пробег, клеток", 0),
    ("wait_ticks_total", "Тактов ожидания в пробках", 0),
    ("utilization", "Загрузка роботов", 3),
    ("jain_fairness", "Индекс Джейна (баланс задач)", 3),
    ("charging_wait_ticks", "Ожидание зарядки, тактов", 0),
]


def e1(out):
    rows = load("E1")
    g = group(rows, "mode")
    out.append("## E1. Базовый сценарий (6 роботов, 40 заказов, p = 0,12; 20 зёрен; среднее ± 95% ДИ по успешным прогонам)\n")
    out.append("| Показатель | " + " | ".join(LABEL[m] for m in MODES) + " |")
    out.append("|---|" + "---|" * len(MODES))
    for key, label, nd in E1_METRICS:
        cells = [fmt(*ci([r[key] for r in succ(g[(m,)])]), nd) for m in MODES]
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    ok = [f"{sum(r['orders_completed'] == 40 for r in g[(m,)])}/{len(g[(m,)])}" for m in MODES]
    out.append("| Прогонов со всеми 40 заказами | " + " | ".join(ok) + " |")
    out.append("| Разряженных роботов (сумма) | " + " | ".join(
        str(int(sum(r["stranded"] for r in g[(m,)]))) for m in MODES) + " |")

    # парное сравнение с централизованным по тем же зёрнам
    out.append("\n**Парное сравнение с централизованным механизмом (те же зёрна): разность makespan "
               "(отрицательная — быстрее), доля зёрен, где механизм быстрее**\n")
    out.append("| Механизм | Δ makespan, тактов | быстрее central |")
    out.append("|---|---|---|")
    base = {r["seed"]: r["makespan"] for r in succ(g[("central",)])}
    for m in MODES[1:]:
        d = [r["makespan"] - base[r["seed"]] for r in succ(g[(m,)]) if r["seed"] in base]
        out.append(f"| {LABEL[m]} | {fmt(*ci(d), 1)} | {sum(x < 0 for x in d)}/{len(d)} |")
    out.append("")

    panels = [("makespan", "Время завершения всех заказов, тактов", False),
              ("coordination_msgs_per_task", "Сообщений распределения на задачу", True),
              ("coordination_cpu_ms", "Время ЦП на координацию, мс", False),
              ("empty_distance_total", "Пробег без груза, клеток", False)]
    fig, axes = plt.subplots(1, 4, figsize=(18.5, 4.1))
    for ax, (key, title, logy) in zip(axes, panels):
        stats = [ci([r[key] for r in succ(g[(m,)])]) for m in MODES]
        xs = range(len(MODES))
        ax.bar(xs, [s[0] for s in stats], width=0.62, color=[COLOR[m] for m in MODES],
               edgecolor="#fcfcfb", linewidth=2, zorder=2)
        ax.errorbar(xs, [s[0] for s in stats], yerr=[s[1] for s in stats], fmt="none",
                    ecolor=INK2, elinewidth=1.2, capsize=3, zorder=3)
        for x, (mval, h) in zip(xs, stats):
            ax.annotate(f"{mval:.0f}" if mval >= 10 else f"{mval:.1f}", (x, mval + h),
                        xytext=(0, 3), textcoords="offset points", ha="center", va="bottom",
                        fontsize=9, color=INK)
        ax.set_xticks(list(xs), [SHORT[m] for m in MODES], fontsize=8.5)
        ax.set_title(title, loc="left", fontsize=10)
        ax.grid(axis="x", visible=False)
        if logy:
            ax.set_yscale("log")
            ax.set_ylim(top=ax.get_ylim()[1] * 2)
        else:
            ax.set_ylim(top=ax.get_ylim()[1] * 1.12)
    fig.suptitle("E1. Базовый сценарий: среднее по 20 зёрнам, планки — 95% ДИ", x=0.01, ha="left",
                 fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e1_baseline.png"), dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- линейные серии
def series_fig(rows, xkey, xlabel, panels, title, fname):
    g = group(rows, "mode", "value")
    xs = sorted({r["value"] for r in rows})
    fig, axes = plt.subplots(1, len(panels), figsize=(5.2 * len(panels), 4))
    for ax, (key, ptitle, logy) in zip(axes, panels):
        ends = []
        for m in MODES:
            stats = [ci([r[key] for r in succ(g[(m, x)])]) for x in xs]
            ys = [s[0] for s in stats]
            ax.plot(xs, ys, color=COLOR[m], lw=2, marker=MARKER[m], ms=7,
                    markeredgecolor="#fcfcfb", markeredgewidth=1.5, label=LABEL[m], zorder=3)
            ax.fill_between(xs, [y - s[1] for y, s in zip(ys, stats)], [y + s[1] for y, s in zip(ys, stats)],
                            color=COLOR[m], alpha=0.12, lw=0, zorder=2)
            ends.append((ys[-1], m))
        ax.set_title(ptitle, loc="left", fontsize=10)
        ax.set_xlabel(xlabel)
        ax.set_xticks(xs)
        if logy:
            ax.set_yscale("log")
        ax.set_xlim(xs[0] - (xs[-1] - xs[0]) * 0.04, xs[-1] + (xs[-1] - xs[0]) * 0.42)
        # прямые подписи у концов линий с раздвижкой, чтобы не перекрывались
        fig.canvas.draw()
        to_px, to_data = ax.transData, ax.transData.inverted()
        pts = sorted((to_px.transform((xs[-1], y))[1], m) for y, m in ends)
        placed = []
        for py, m in pts:
            if placed and py - placed[-1][0] < 13:
                py = placed[-1][0] + 13
            placed.append((py, m))
        x_px = to_px.transform((xs[-1], ends[0][0]))[0]
        for py, m in placed:
            ax.annotate(LABEL[m], (xs[-1], to_data.transform((x_px, py))[1]), xytext=(8, 0),
                        textcoords="offset points", va="center", fontsize=8.5, color=INK2)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.01, 0.93), ncol=5, fontsize=9)
    fig.savefig(os.path.join(FIG, fname), dpi=150)
    plt.close(fig)


def series_table(out, rows, xname, keys):
    g = group(rows, "mode", "value")
    xs = sorted({r["value"] for r in rows})
    for key, label, nd in keys:
        out.append(f"\n**{label}** (среднее ± 95% ДИ по успешным прогонам)\n")
        out.append(f"| {xname} | " + " | ".join(LABEL[m] for m in MODES) + " |")
        out.append("|---|" + "---|" * len(MODES))
        for x in xs:
            xv = f"{x:g}"
            out.append(f"| {xv} | " + " | ".join(fmt(*ci([r[key] for r in succ(g[(m, x)])]), nd) for m in MODES) + " |")
    out.append(f"\n**Прогонов со всеми заказами / разряженных роботов**\n")
    out.append(f"| {xname} | " + " | ".join(LABEL[m] for m in MODES) + " |")
    out.append("|---|" + "---|" * len(MODES))
    for x in xs:
        cells = []
        for m in MODES:
            rs = g[(m, x)]
            cells.append(f"{sum(r['orders_completed'] == 40 for r in rs)}/{len(rs)} · "
                         f"{int(sum(r['stranded'] for r in rs))}")
        out.append(f"| {x:g} | " + " | ".join(cells) + " |")
    out.append("")


SERIES_KEYS = [("makespan", "Время завершения всех заказов, тактов", 0),
               ("avg_order_lead_time", "Среднее время выполнения заказа, тактов", 0),
               ("coordination_msgs_per_task", "Сообщений распределения на задачу", 1),
               ("coordination_cpu_ms", "Время ЦП на координацию, мс", 1),
               ("empty_distance_total", "Пробег без груза, клеток", 0)]


def e2(out):
    rows = load("E2")
    out.append("## E2. Масштабирование по числу роботов (40 заказов, p = 0,12; 10 зёрен)\n")
    series_table(out, rows, "Роботов", SERIES_KEYS)
    series_fig(rows, "value", "Число роботов",
               [("makespan", "Время завершения всех заказов, тактов", False),
                ("coordination_msgs_per_task", "Сообщений распределения на задачу (лог. шкала)", True),
                ("empty_distance_total", "Пробег без груза, клеток", False)],
               "E2. Масштабирование по числу роботов (10 зёрен, полоса — 95% ДИ)", "e2_scaling.png")


def e3(out):
    rows = load("E3")
    out.append("## E3. Интенсивность потока заказов (6 роботов, 40 заказов; 10 зёрен)\n")
    series_table(out, rows, "p", SERIES_KEYS)
    series_fig(rows, "value", "Вероятность появления заказа за такт, p",
               [("avg_order_lead_time", "Среднее время выполнения заказа, тактов", False),
                ("coordination_msgs_per_task", "Сообщений распределения на задачу (лог. шкала)", True),
                ("allocation_latency", "Задержка распределения задачи, тактов", False)],
               "E3. Нагрузка: интенсивность потока заказов (10 зёрен, полоса — 95% ДИ)", "e3_load.png")


def e4(out):
    rows = load("E4")
    out.append("## E4. Чувствительность к параметрам механизмов (базовый сценарий; 10 зёрен)\n")
    params = [("auction", "auction_period", "Период аукциона, тактов"),
              ("delegation", "deleg_threshold", "Порог делегирования, тактов"),
              ("cnp", "cnp_retry", "Пауза до повторного тендера, тактов")]
    fig, axes = plt.subplots(2, 3, figsize=(14, 6.6))
    for col, (m, param, plabel) in enumerate(params):
        rs = [r for r in rows if r["param"] == param]
        g = group(rs, "value")
        xs = sorted(k[0] for k in g)
        out.append(f"\n**{LABEL[m]}: {plabel.lower()}**\n")
        out.append("| Значение | Успешных | Makespan | Ср. время заказа | Сообщ./задачу | Пробег без груза | Задержка распр. |")
        out.append("|---|---|---|---|---|---|---|")
        for x in xs:
            rr = succ(g[(x,)])
            out.append(f"| {x:g} | {len(rr)}/{len(g[(x,)])} | {fmt(*ci([r['makespan'] for r in rr]), 0)} | "
                       f"{fmt(*ci([r['avg_order_lead_time'] for r in rr]), 0)} | "
                       f"{fmt(*ci([r['coordination_msgs_per_task'] for r in rr]), 1)} | "
                       f"{fmt(*ci([r['empty_distance_total'] for r in rr]), 0)} | "
                       f"{fmt(*ci([r['allocation_latency'] for r in rr]), 1)} |")
        xv = xs
        for row, (key, ylabel) in enumerate([("makespan", "Время завершения, тактов"),
                                             ("coordination_msgs_per_task", "Сообщений на задачу")]):
            ax = axes[row][col]
            stats = [ci([r[key] for r in succ(g[(x,)])]) for x in xs]
            ys = [s[0] for s in stats]
            ax.plot(xv, ys, color=COLOR[m], lw=2, marker=MARKER[m], ms=7, markeredgecolor="#fcfcfb",
                    markeredgewidth=1.5, zorder=3)
            ax.fill_between(xv, [y - s[1] for y, s in zip(ys, stats)], [y + s[1] for y, s in zip(ys, stats)],
                            color=COLOR[m], alpha=0.15, lw=0)
            ax.set_xticks(xv)
            if row == 0:
                ax.set_title(LABEL[m], loc="left", fontsize=10)
            else:
                ax.set_xlabel(plabel)
            if col == 0:
                ax.set_ylabel(ylabel)
    fig.suptitle("E4. Чувствительность к параметрам механизмов (10 зёрен, полоса — 95% ДИ)",
                 x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e4_params.png"), dpi=150)
    plt.close(fig)
    out.append("")


def main():
    os.makedirs(FIG, exist_ok=True)
    out = ["# Сводка результатов ЛР2\n"]
    for f in (e1, e2, e3, e4):
        f(out)
    with open(os.path.join(RES, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    print("results/summary.md, figures/*.png")


if __name__ == "__main__":
    main()
