from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Config:
    # режим распределения задач: "central" (централизованный диспетчер) или "cnp" (контрактная сеть)
    mode: str = "central"
    seed: int = 1
    max_ticks: int = 3000

    # поток заказов
    n_orders: int = 40
    order_rate: float = 0.12          # вероятность появления заказа на такте
    max_items: int = 3                # число позиций в заказе: 1..max_items

    # роботы
    n_robots: int = 6
    robot_queue_cap: int = 2          # текущая задача + 1 в очереди
    pick_time: int = 2
    drop_time: int = 1
    replan_after: int = 3             # число отказов в резерве клетки до перепланирования
    heartbeat_every: int = 5

    # энергия
    battery_capacity: int = 120
    battery_reserve: int = 20
    charge_threshold: float = 0.35    # доля заряда, ниже которой свободный робот идёт заряжаться
    charge_rate: int = 6
    charger_slots: int = 1            # слотов на каждой из двух зарядных станций

    # диспетчер
    est_task_time: int = 25           # оценка длительности задачи для учёта загрузки (central)
    cnp_parallel: int = 3             # одновременно объявляемых тендеров (cnp)
    cnp_retry: int = 3                # пауза до повторного тендера без ставок

    # мониторинг
    heartbeat_timeout: int = 20

    # внедрение отказов: список (robot_id, tick)
    failures: list[tuple[str, int]] = field(default_factory=list)
