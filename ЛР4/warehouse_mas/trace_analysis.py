"""ЛР4: анализатор трасс — выявление отказов и атак только по журналу сообщений.

Анализатор не имеет доступа к внутреннему состоянию агентов. Он видит то же, что видел бы
внешний аудитор: трассу сообщений (её пишет агент monitor) и известную спецификацию протоколов.
"""
from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from statistics import mean, pstdev

from .messages import Performative
from .security import authorized, validate_task

DETECTORS = {
    "A1_spoofed": "Подделка отправителя (поле from не совпадает с фактическим источником)",
    "A2_unauthorized": "Команда без полномочий (нарушение матрицы полномочий)",
    "A3_unanswered": "Запрос без ответа (недоступный агент / потерянное сообщение)",
    "A4_false_claim": "Отчёт «выполнено», когда робот не был в точке сдачи (по трассе перемещений)",
    "A5_multi_executor": "Одна задача выполнена / заявлена несколькими роботами",
    "A6_multi_coordinator": "Противоречивые указания: награды за задачу от разных координаторов",
    "A7_loop": "Циклическая передача: попытка делегирования прошла больше круга",
    "A8_storm": "Шторм сообщений: такты с трафиком выше среднего + 4σ",
    "A9_silent": "Замолчавший агент (был активен, затем ни одного сообщения)",
    "A10_malformed": "Задача с нарушением схемы или семантики во входном потоке",
    "A11_stalled": "Задача, назначенная, но не выполненная за 400 тактов",
}


class _Msg:  # минимальная обёртка записи трассы для функций безопасности
    def __init__(self, rec):
        self.sender, self.content = rec["from"], rec["content"]
        self.performative = Performative(rec["performative"])


def analyze(trace: list[dict], env, n_robots: int, end_tick: int) -> dict:
    found: dict[str, list] = defaultdict(list)
    # ---- A1, A2
    for r in trace:
        if r["origin"] and r["origin"] != r["from"]:
            found["A1_spoofed"].append(r["id"])
        elif not r["to"].startswith("group:") and not authorized(_Msg(r), r["to"]):
            found["A2_unauthorized"].append(r["id"])
    # ---- A3: запросы без ответа в течение 5 тактов
    replies = defaultdict(list)
    for r in trace:
        replies[(r["from"], r["to"])].append(r["tick"])
    for r in trace:
        a = r["content"].get("action")
        # только запросы, на которые протокол обязывает ответить (передача задачи без защиты — без ответа)
        if r["performative"] == "request" and a in ("reserve", "charge", "charge_query", "verify"):
            ticks = replies.get((r["to"], r["from"]), [])
            i = bisect.bisect_left(ticks, r["tick"])
            if not (i < len(ticks) and ticks[i] <= r["tick"] + 5) and r["tick"] < end_tick - 5:
                found["A3_unanswered"].append(r["id"])
    # ---- позиции роботов по сообщениям moved и параметры задач
    pos = defaultdict(list)
    tasks = {}
    for r in trace:
        c = r["content"]
        if c.get("event") == "moved":
            pos[r["from"]].append((r["tick"], tuple(c["to"]) if isinstance(c["to"], (list, tuple)) else c["to"]))
        elif c.get("action") == "submit_task":
            tk = c["task"]
            tasks.setdefault(tk["task_id"], tk)
            if not validate_task(tk, env)[0]:
                found["A10_malformed"].append(r["id"])

    def where(robot, tick):
        seq = pos.get(robot, [])
        i = bisect.bisect_right([t for t, _ in seq], tick)
        return seq[i - 1][1] if i else None

    # ---- A4, A5: отчёты о выполнении
    reporters = defaultdict(set)
    seen = set()
    for r in trace:
        c = r["content"]
        if c.get("event") == "task_done" and r["from"].startswith("robot") and (r["from"], c["task_id"]) not in seen:
            seen.add((r["from"], c["task_id"]))
            reporters[c["task_id"]].add(r["from"])
            tk = tasks.get(c["task_id"])
            if tk is not None and pos.get(r["from"]) and where(r["from"], c.get("at", r["tick"])) != tk["drop"]:
                found["A4_false_claim"].append(r["id"])
    found["A5_multi_executor"] = [t for t, s in reporters.items() if len(s) > 1]
    # ---- A6: награды от разных координаторов
    awarders = defaultdict(set)
    for r in trace:
        if r["performative"] == "accept-proposal":
            for tk in r["content"].get("tasks", []) or [r["content"].get("task", {})]:
                if tk:
                    awarders[tk["task_id"]].add(r["from"])
    found["A6_multi_coordinator"] = [t for t, s in awarders.items() if len(s) > 1]
    # ---- A7: длина круга делегирования
    hops = Counter()
    for r in trace:
        c = r["content"]
        if c.get("action") == "delegate":
            hops[(c.get("deleg_id"), c["task"]["task_id"])] += 1
    found["A7_loop"] = [k for k, v in hops.items() if v > n_robots + 1]
    # ---- A8: шторм
    per_tick = Counter(r["tick"] for r in trace)
    if per_tick:
        vals = [per_tick.get(t, 0) for t in range(end_tick)]
        m, sd = mean(vals), pstdev(vals)
        found["A8_storm"] = [t for t, v in enumerate(vals) if v > m + 4 * sd]
    # ---- A9: замолчавшие агенты
    last, first = {}, {}
    for r in trace:
        last[r["from"]] = r["tick"]
        first.setdefault(r["from"], r["tick"])
    # агенты с непрерывной активностью: роботы (heartbeat), регулировщики, станции
    found["A9_silent"] = [a for a, t in last.items() if t < end_tick - 100 and t - first[a] > 50
                          and a.startswith(("robot", "traffic", "charger"))]
    # ---- A11: зависшие задачи (награда без отчёта)
    awarded_at, done = {}, set()
    for r in trace:
        c = r["content"]
        if r["performative"] == "accept-proposal":
            for tk in c.get("tasks", []):
                awarded_at.setdefault(tk["task_id"], r["tick"])
        elif c.get("event") == "task_done":
            done.add(c["task_id"])
    found["A11_stalled"] = [t for t, at in awarded_at.items() if t not in done and end_tick - at > 400]
    return {k: found.get(k, []) for k in DETECTORS}
