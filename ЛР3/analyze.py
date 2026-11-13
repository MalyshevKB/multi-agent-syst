"""Анализ результатов ЛР3: сводные таблицы (results/summary.md) и графики (figures/*.png).

  python analyze.py
"""
from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict
from statistics import mean, stdev

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RES, FIG = os.path.join(HERE, "results"), os.path.join(HERE, "figures")
VARS = ["fifo", "priority", "negotiation", "negotiation+rep"]
LABEL = {"fifo": "FIFO (до)", "priority": "Приоритет + старение", "negotiation": "Переговоры",
         "negotiation+rep": "Переговоры + репутация"}
SHORT = {"fifo": "FIFO\n(до)", "priority": "Приоритет\n+ старение", "negotiation": "Переговоры",
         "negotiation+rep": "Переговоры\n+ репутация"}
# палитра проверена валидатором (CVD / normal-vision OK); два светлых цвета — с подписями и маркерами
COLOR = {"fifo": "#2a78d6", "priority": "#eb6834", "negotiation": "#1baf7a", "negotiation+rep": "#eda100"}
MARKER = {"fifo": "o", "priority": "s", "negotiation": "^", "negotiation+rep": "D"}
INK, INK2, GRID, BG = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
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
    "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold", "axes.titlesize": 10,
    "axes.titlecolor": INK, "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "legend.frameon": False,
})


def load(name):
    with open(os.path.join(RES, f"{name}.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in list(r.items()):
            if k in ("experiment", "variant", "param"):
                continue
            if k in ("charge_wait_by_robot", "charge_sessions_by_robot"):
                r[k] = json.loads(v)
                continue
            if v in ("", "None"):
                r[k] = None
            elif v == "-":
                r[k] = v
            else:
                try:
                    r[k] = float(v)
                except ValueError:
                    pass
    return rows


def ci(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return float("nan"), 0.0
    if len(xs) < 2:
        return xs[0], 0.0
    return mean(xs), t95(len(xs) - 1) * stdev(xs) / math.sqrt(len(xs))


def fmt(m, h, nd=1):
    return "—" if m != m else f"{m:.{nd}f} ± {h:.{nd}f}"


def succ(rows):
    return [r for r in rows if r["orders_completed"] == 100]


def group(rows, *keys):
    g = defaultdict(list)
    for r in rows:
        g[tuple(r[k] for k in keys)].append(r)
    return g


def bars(ax, variants, stats, title, nd=0):
    xs = range(len(variants))
    ax.bar(xs, [s[0] for s in stats], width=0.62, color=[COLOR[v] for v in variants], edgecolor=BG,
           linewidth=2, zorder=2)
    ax.errorbar(xs, [s[0] for s in stats], yerr=[s[1] for s in stats], fmt="none", ecolor=INK2,
                elinewidth=1.2, capsize=3, zorder=3)
    for x, (m, h) in zip(xs, stats):
        ax.annotate(f"{m:.{nd}f}", (x, m + h), xytext=(0, 3), textcoords="offset points", ha="center",
                    va="bottom", fontsize=9, color=INK)
    ax.set_xticks(list(xs), [SHORT[v] for v in variants], fontsize=8.5)
    ax.set_title(title, loc="left")
    ax.grid(axis="x", visible=False)
    ax.set_ylim(top=ax.get_ylim()[1] * 1.14)


def end_labels(fig, ax, xs, ends):
    fig.canvas.draw()
    to_px, to_data = ax.transData, ax.transData.inverted()
    pts = sorted((to_px.transform((xs[-1], y))[1], v) for y, v in ends if y == y)
    placed = []
    for py, v in pts:
        if placed and py - placed[-1][0] < 13:
            py = placed[-1][0] + 13
        placed.append((py, v))
    x_px = to_px.transform((xs[-1], 0))[0]
    for py, v in placed:
        ax.annotate(LABEL[v], (xs[-1], to_data.transform((x_px, py))[1]), xytext=(8, 0),
                    textcoords="offset points", va="center", fontsize=8.5, color=INK2)


def lines(fig, ax, xs, series, title, xlabel, zero=False):
    ends = []
    for v, stats in series.items():
        ys = [s[0] for s in stats]
        ax.plot(xs, ys, color=COLOR[v], lw=2, marker=MARKER[v], ms=7, markeredgecolor=BG,
                markeredgewidth=1.5, label=LABEL[v], zorder=3)
        ax.fill_between(xs, [y - s[1] for y, s in zip(ys, stats)], [y + s[1] for y, s in zip(ys, stats)],
                        color=COLOR[v], alpha=0.12, lw=0, zorder=2)
        ends.append((ys[-1], v))
    if zero:
        ax.axhline(0, color=INK2, lw=1, zorder=1)
    ax.set_title(title, loc="left")
    ax.set_xlabel(xlabel)
    ax.set_xticks(xs)
    ax.set_xlim(xs[0] - (xs[-1] - xs[0]) * 0.05, xs[-1] + (xs[-1] - xs[0]) * 0.55)
    end_labels(fig, ax, xs, ends)


def finish(fig, title, fname, legend_ax=None):
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    if legend_ax is not None:
        h, lab = legend_ax.get_legend_handles_labels()
        fig.tight_layout(rect=(0, 0, 1, 0.9))
        fig.legend(h, lab, loc="upper left", bbox_to_anchor=(0.01, 0.93), ncol=4, fontsize=9)
    else:
        fig.tight_layout()
    fig.savefig(os.path.join(FIG, fname), dpi=150)
    plt.close(fig)


# ================================================================== E1
E1_METRICS = [
    ("makespan", "Время завершения всех заказов, тактов", 0),
    ("avg_order_lead_time", "Среднее время заказа, тактов", 1),
    ("max_order_lead_time", "Максимальное время заказа, тактов", 0),
    ("express_hit", "Экспресс-заказы в срок, доля", 3),
    ("standard_hit", "Обычные заказы в срок, доля", 3),
    ("charge_wait_mean", "Ожидание зарядки: среднее, тактов", 1),
    ("charge_wait_p95", "Ожидание зарядки: 95-й процентиль", 1),
    ("charge_wait_max", "Ожидание зарядки: максимум", 1),
    ("charge_wait_critical", "Ожидание при низком заряде (≤ 30 %)", 1),
    ("jain_charge_wait", "Индекс Джейна по ожиданию роботов", 3),
    ("charging_sessions", "Сеансов зарядки", 1),
    ("offers_made", "Предложений уступить слот", 1),
    ("offers_accepted", "Принятых уступок", 1),
    ("emergency_charges", "Аварийных уходов на зарядку", 2),
    ("min_battery", "Минимальный заряд робота за прогон", 1),
    ("utilization", "Загрузка роботов", 3),
]


def e1(out):
    rows = load("E1")
    vs = ["fifo", "priority", "negotiation"]
    g = group(rows, "variant")
    out.append("## E1. До и после: все агенты честные (8 роботов, 100 заказов, 2 слота; 20 зёрен; "
               "среднее ± 95% ДИ по успешным прогонам)\n")
    out.append("| Показатель | " + " | ".join(LABEL[v] for v in vs) + " |")
    out.append("|---|" + "---|" * len(vs))
    for key, label, nd in E1_METRICS:
        out.append(f"| {label} | " + " | ".join(fmt(*ci([r[key] for r in succ(g[(v,)])]), nd) for v in vs) + " |")
    out.append("| Успешных прогонов | " + " | ".join(f"{len(succ(g[(v,)]))}/{len(g[(v,)])}" for v in vs) + " |")
    out.append("| Разряженных роботов (сумма) | " + " | ".join(
        str(int(sum(r["stranded"] for r in g[(v,)]))) for v in vs) + " |")

    out.append("\n**Парные разности с FIFO (те же зёрна)**\n")
    out.append("| Механизм | Δ ожидание, среднее | Δ ожидание, p95 | Δ при низком заряде | Δ makespan |")
    out.append("|---|---|---|---|---|")
    base = {r["seed"]: r for r in succ(g[("fifo",)])}
    for v in vs[1:]:
        cells = []
        for key in ("charge_wait_mean", "charge_wait_p95", "charge_wait_critical", "makespan"):
            d = [r[key] - base[r["seed"]][key] for r in succ(g[(v,)])
                 if r["seed"] in base and r[key] is not None and base[r["seed"]][key] is not None]
            cells.append(fmt(*ci(d), 1))
        out.append(f"| {LABEL[v]} | " + " | ".join(cells) + " |")
    out.append("")

    panels = [("charge_wait_mean", "Ожидание зарядки, среднее, тактов", 1),
              ("charge_wait_p95", "Ожидание зарядки, 95-й процентиль", 0),
              ("charge_wait_critical", "Ожидание при низком заряде, тактов", 1),
              ("jain_charge_wait", "Справедливость: индекс Джейна", 2)]
    fig, axes = plt.subplots(1, 4, figsize=(15, 3.9))
    for ax, (key, title, nd) in zip(axes, panels):
        bars(ax, vs, [ci([r[key] for r in succ(g[(v,)])]) for v in vs], title, nd)
    finish(fig, "E1. До и после введения механизмов: честные агенты (20 зёрен, планки — 95% ДИ)", "e1_before_after.png")


# ================================================================== E2
def group_wait(r, ids):
    w = sum(r["charge_wait_by_robot"].get(i, 0) for i in ids)
    n = sum(r["charge_sessions_by_robot"].get(i, 0) for i in ids)
    return w / n if n else None


def e2(out):
    rows = load("E2")
    g = group(rows, "variant", "liars", "seed")
    ks = [1, 2, 3]
    out.append("## E2. Нечестные агенты: k роботов занижают заявленный заряд и не уступают слот (10 зёрен)\n")
    out.append("Выгода лжи и вред честным оцениваются **парно**: те же роботы и то же зерно в прогоне без лжи "
               "(k = 0) той же политики. Δ — изменение среднего ожидания на один сеанс зарядки, тактов.\n")
    out.append("| Политика | k | Δ ожидания лжецов | Δ ожидания честных | Makespan | Экспресс в срок | "
               "Выявлено лжи | Санкций | Успешных |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    dl, dh, mk = defaultdict(list), defaultdict(list), defaultdict(list)
    for v in VARS:
        for k in ks:
            liars = [f"robot_{i + 1}" for i in range(k)]
            honest = [f"robot_{i + 1}" for i in range(k, 8)]
            rs = []
            for s in range(1, 11):
                a = g.get((v, float(k), float(s)))
                b = g.get((v, 0.0, float(s)))
                if not a or not b or a[0]["orders_completed"] != 100 or b[0]["orders_completed"] != 100:
                    continue
                a, b = a[0], b[0]
                la, lb = group_wait(a, liars), group_wait(b, liars)
                ha, hb = group_wait(a, honest), group_wait(b, honest)
                if None not in (la, lb):
                    dl[(v, k)].append(la - lb)
                if None not in (ha, hb):
                    dh[(v, k)].append(ha - hb)
                mk[(v, k)].append(a["makespan"])
                rs.append(a)
            allr = [x for s in range(1, 11) for x in g.get((v, float(k), float(s)), [])]
            out.append(f"| {LABEL[v]} | {k} | {fmt(*ci(dl[(v, k)]))} | {fmt(*ci(dh[(v, k)]))} | "
                       f"{fmt(*ci(mk[(v, k)]), 0)} | {fmt(*ci([r['express_hit'] for r in rs]), 3)} | "
                       f"{fmt(*ci([r['lies_detected'] for r in rs]))} | {fmt(*ci([r['sanctions'] for r in rs]))} | "
                       f"{len(rs)}/{len(allr)} |")
    out.append("")

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.3))
    lines(fig, axes[0], ks, {v: [ci(dl[(v, k)]) for k in ks] for v in VARS},
          "Выгода лжи: Δ ожидания лжецов, тактов", "Число лжецов k (из 8)", zero=True)
    lines(fig, axes[1], ks, {v: [ci(dh[(v, k)]) for k in ks] for v in VARS},
          "Вред честным: Δ ожидания честных, тактов", "Число лжецов k (из 8)", zero=True)
    base_mk = {v: ci([r["makespan"] for s in range(1, 11) for r in g.get((v, 0.0, float(s)), [])
                      if r["orders_completed"] == 100]) for v in VARS}
    lines(fig, axes[2], [0] + ks, {v: [base_mk[v]] + [ci(mk[(v, k)]) for k in ks] for v in VARS},
          "Время завершения всех заказов, тактов", "Число лжецов k (из 8)")
    finish(fig, "E2. Нечестные агенты: парное сравнение с прогоном без лжи (10 зёрен, полоса — 95% ДИ)",
           "e2_liars.png", legend_ax=axes[0])


# ================================================================== E3
def e3(out):
    rows = load("E3")
    vs = ["fifo", "priority", "negotiation"]
    g = group(rows, "variant", "value")
    xs = sorted({r["value"] for r in rows})
    out.append("## E3. Острота дефицита: скорость зарядки (10 зёрен; по успешным прогонам)\n")
    for key, label, nd in [("charge_wait_mean", "Ожидание зарядки, среднее", 1),
                           ("charge_wait_p95", "Ожидание зарядки, p95", 0),
                           ("charge_wait_critical", "Ожидание при низком заряде", 1),
                           ("makespan", "Время завершения всех заказов", 0),
                           ("standard_hit", "Обычные заказы в срок, доля", 3)]:
        out.append(f"\n**{label}**\n")
        out.append("| Скорость зарядки | " + " | ".join(LABEL[v] for v in vs) + " |")
        out.append("|---|" + "---|" * len(vs))
        for x in xs:
            out.append(f"| {x:g} | " + " | ".join(fmt(*ci([r[key] for r in succ(g[(v, x)])]), nd) for v in vs) + " |")
    out.append("\n**Успешных прогонов / разряженных роботов**\n")
    out.append("| Скорость зарядки | " + " | ".join(LABEL[v] for v in vs) + " |")
    out.append("|---|" + "---|" * len(vs))
    for x in xs:
        out.append(f"| {x:g} | " + " | ".join(
            f"{len(succ(g[(v, x)]))}/{len(g[(v, x)])} · {int(sum(r['stranded'] for r in g[(v, x)]))}" for v in vs) + " |")
    out.append("")
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.3))
    for ax, (key, title) in zip(axes, [("charge_wait_mean", "Ожидание зарядки, среднее, тактов"),
                                       ("charge_wait_p95", "Ожидание зарядки, 95-й процентиль"),
                                       ("makespan", "Время завершения всех заказов, тактов")]):
        lines(fig, ax, xs, {v: [ci([r[key] for r in succ(g[(v, x)])]) for x in xs] for v in vs}, title,
              "Скорость зарядки, ед./такт (меньше — острее дефицит)")
    finish(fig, "E3. Острота дефицита ресурса (10 зёрен, полоса — 95% ДИ)", "e3_deficit.png", legend_ax=axes[0])


# ================================================================== E4
def e4(out):
    rows = load("E4")
    out.append("## E4. Параметры механизмов (10 зёрен; по успешным прогонам)\n")
    specs = [("release_min", "negotiation", "Переговоры: минимальный заряд для предложения уступить (доля)",
              [("charge_wait_mean", "Ожидание, среднее", 1), ("charge_wait_p95", "Ожидание, p95", 0),
               ("charging_sessions", "Сеансов зарядки", 1), ("offers_accepted", "Уступок", 1),
               ("makespan", "Makespan", 0)]),
             ("aging_rate", "priority", "Приоритет: скорость старения заявки (за такт ожидания)",
              [("charge_wait_mean", "Ожидание, среднее", 1), ("charge_wait_max", "Ожидание, максимум", 0),
               ("charge_wait_critical", "При низком заряде", 1), ("jain_charge_wait", "Индекс Джейна", 3),
               ("makespan", "Makespan", 0)])]
    fig, axes = plt.subplots(1, 4, figsize=(16, 4))
    ai = 0
    for param, v, title, keys in specs:
        rs = [r for r in rows if r["param"] == param]
        g = group(rs, "value")
        xs = sorted(k[0] for k in g)
        out.append(f"\n**{title}**\n")
        out.append("| Значение | Успешных | " + " | ".join(k[1] for k in keys) + " |")
        out.append("|---|---|" + "---|" * len(keys))
        for x in xs:
            ok = succ(g[(x,)])
            out.append(f"| {x:g} | {len(ok)}/{len(g[(x,)])} | " +
                       " | ".join(fmt(*ci([r[k] for r in ok]), nd) for k, _, nd in keys) + " |")
        for key, ptitle in ([("charge_wait_p95", "Ожидание, p95"), ("charging_sessions", "Сеансов зарядки")]
                            if param == "release_min" else
                            [("charge_wait_max", "Макс. ожидание"),
                             ("jain_charge_wait", "Индекс Джейна")]):
            ax = axes[ai]
            ai += 1
            stats = [ci([r[key] for r in succ(g[(x,)])]) for x in xs]
            ys = [s[0] for s in stats]
            ax.plot(xs, ys, color=COLOR[v], lw=2, marker=MARKER[v], ms=7, markeredgecolor=BG, markeredgewidth=1.5)
            ax.fill_between(xs, [y - s[1] for y, s in zip(ys, stats)], [y + s[1] for y, s in zip(ys, stats)],
                            color=COLOR[v], alpha=0.15, lw=0)
            ax.set_xticks(xs)
            ax.set_title(f"{'Переговоры' if v == 'negotiation' else 'Приоритет'}: {ptitle[0].lower() + ptitle[1:]}",
                         loc="left", fontsize=9.5)
            ax.set_xlabel("Порог уступки, доля заряда" if param == "release_min" else "Старение, за такт ожидания")
    out.append("")
    finish(fig, "E4. Параметры механизмов (10 зёрен, полоса — 95% ДИ)", "e4_params.png")


def main():
    os.makedirs(FIG, exist_ok=True)
    out = ["# Сводка результатов ЛР3\n"]
    for f in (e1, e2, e3, e4):
        f(out)
    with open(os.path.join(RES, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    print("results/summary.md, figures/*.png")


if __name__ == "__main__":
    main()
