"""Венгерский алгоритм (метод Куна–Манкреса) для задачи о назначениях — реализация с нуля.

Вход: квадратная или прямоугольная матрица стоимостей cost[i][j] (строк не больше, чем столбцов).
Выход: assignment[i] = j — столбец, назначенный строке i, при минимальной суммарной стоимости.

Используется вариант с потенциалами u (строки) и v (столбцы), сложность O(n²·m) (O(n³) для
квадратной матрицы). На каждом шаге в паросочетание добавляется одна строка: ищется кратчайший
увеличивающий путь по «приведённым» стоимостям cost[i][j] − u[i] − v[j] ≥ 0. Это та же идея,
что «вычесть константу из строки/столбца и искать нули» из лекции, но без явного покрытия нулей
линиями.
"""
from __future__ import annotations

INF = float("inf")


def hungarian(cost: list[list[float]]) -> list[int]:
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    assert n <= m, "строк должно быть не больше, чем столбцов"
    u = [0.0] * (n + 1)          # потенциалы строк (индексация с 1)
    v = [0.0] * (m + 1)          # потенциалы столбцов
    p = [0] * (m + 1)            # p[j] — строка, назначенная столбцу j (0 — свободен)
    way = [0] * (m + 1)          # предыдущий столбец на увеличивающем пути
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):       # сдвиг потенциалов: появляется новый «нуль»
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:                       # разворот увеличивающего пути
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    return assignment
