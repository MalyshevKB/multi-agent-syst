"""Модель среды: сетка склада, стеллажи, станции упаковки, зарядки, поиск путей."""
from __future__ import annotations

from collections import deque

FREE, SHELF, STATION, CHARGER, BAY = ".", "#", "P", "C", "w"
NEIGH = ((1, 0), (-1, 0), (0, 1), (0, -1))
INF = 10 ** 9

Cell = tuple[int, int]


def build_layout(charger_slots: int = 1, n_stations: int = 2) -> list[str]:
    """Склад 19x11: 6 пар стеллажей, однополосные односторонние проходы x=0,3,...,18,
    поперечные проходы y=1,5,9; ряд 0 — зона зарядки, ряд 10 — станции упаковки и парковка."""
    w, h = 19, 11
    rows = [[FREE] * w for _ in range(h)]
    for y in (2, 3, 4, 6, 7, 8):
        for x in range(w):
            if x % 3 != 0:
                rows[y][x] = SHELF
    for x in (3, 9, 15):
        rows[10][x] = STATION
    for x in range(w):           # ряд 0 — зона зарядки: слоты и места ожидания, без сквозного проезда
        rows[0][x] = BAY
    for i in range(charger_slots):
        rows[0][i] = CHARGER
        if n_stations > 1:          # ЛР3: при n_stations = 1 восточная станция выведена из работы
            rows[0][w - 1 - i] = CHARGER
    return ["".join(r) for r in rows]


class Warehouse:
    def __init__(self, layout: list[str]):
        self.layout = layout
        self.h = len(layout)
        self.w = len(layout[0])
        self.cells: dict[Cell, str] = {(x, y): layout[y][x] for y in range(self.h) for x in range(self.w)}
        self.shelves = sorted(c for c, k in self.cells.items() if k == SHELF)
        self.stations = sorted(c for c, k in self.cells.items() if k == STATION)
        self.chargers = sorted(c for c, k in self.cells.items() if k == CHARGER)
        self.shelf_rows = {y for (_, y), k in self.cells.items() if k == SHELF}
        self._dist_cache: dict[Cell, dict[Cell, int]] = {}

    # Однополосные проходы между стеллажами — односторонние, направления чередуются:
    # x=0 вниз, x=3 вверх, x=6 вниз, ...  Это исключает встречные тупики в проходах.
    def aisle_dir(self, x: int) -> int:
        return 1 if (x // 3) % 2 == 0 else -1

    def can_move(self, a: Cell, b: Cell) -> bool:
        (ax, ay), (bx, by) = a, b
        if ax == bx and (ay in self.shelf_rows or by in self.shelf_rows):
            return by - ay == self.aisle_dir(ax)
        return True

    def kind(self, c: Cell) -> str | None:
        return self.cells.get(c)

    def neighbors(self, c: Cell):
        x, y = c
        for dx, dy in NEIGH:
            n = (x + dx, y + dy)
            if n in self.cells:
                yield n

    def drop_point(self, station: Cell) -> Cell:
        x, y = station
        return (x, y - 1)

    def pick_point(self, shelf: Cell) -> Cell:
        x, y = shelf
        for dx in (-1, 1):
            c = (x + dx, y)
            if self.kind(c) == FREE:
                return c
        raise ValueError(f"нет точки подбора у стеллажа {shelf}")

    # Стеллажи и станции упаковки непроезжаемы (обслуживаются из соседней клетки прохода);
    # через слоты зарядки и места ожидания (ряд 0) проезжать нельзя — только въезжать как в цель.
    def distances_from(self, src: Cell) -> dict[Cell, int]:
        if src in self._dist_cache:
            return self._dist_cache[src]
        dist = {src: 0}
        q = deque([src])
        while q:
            c = q.popleft()
            if c != src and self.kind(c) != FREE:
                continue
            for n in self.neighbors(c):
                if n not in dist and self.kind(n) not in (SHELF, STATION) and self.can_move(c, n):
                    dist[n] = dist[c] + 1
                    q.append(n)
        self._dist_cache[src] = dist
        return dist

    def dist(self, a: Cell, b: Cell) -> int:
        return self.distances_from(a).get(b, INF)

    def path(self, start: Cell, goal: Cell, blocked=frozenset(), directed: bool = True) -> list[Cell] | None:
        """BFS-путь (без стартовой клетки); blocked — временно запрещённые клетки;
        directed=False — аварийный режим, разрешающий движение против направления прохода."""
        if start == goal:
            return []
        # ЛР3: к слоту зарядки можно подъехать и вдоль зоны ожидания (ряд 0) — второй подъезд,
        # чтобы один робот перед слотом не отрезал станцию
        bay_ok = self.kind(goal) in (CHARGER, BAY)
        parent: dict[Cell, Cell | None] = {start: None}
        q = deque([start])
        while q:
            c = q.popleft()
            if c == goal:
                break
            if c != start and self.kind(c) != FREE and not (bay_ok and self.kind(c) == BAY):
                continue
            for n in self.neighbors(c):
                if n in parent or n in blocked or self.kind(n) in (SHELF, STATION) \
                        or (directed and not self.can_move(c, n)):
                    continue
                if self.kind(n) != FREE and n != goal and not (bay_ok and self.kind(n) == BAY):
                    continue
                parent[n] = c
                q.append(n)
        if goal not in parent:
            return None
        out = []
        c = goal
        while c != start:
            out.append(c)
            c = parent[c]
        return out[::-1]
