from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Config:
    # механизм распределения задач: "central", "cnp", "auction", "delegation"
    mode: str = "auction"             # ЛР3: базовый механизм распределения — пакетный аукцион (лучший в ЛР2)
    seed: int = 1
    max_ticks: int = 4000

    # поток заказов
    n_orders: int = 100
    order_rate: float = 0.1           # вероятность появления заказа на такте
    max_items: int = 3                # число позиций в заказе: 1..max_items

    # роботы
    n_robots: int = 8
    robot_queue_cap: int = 2          # текущая задача + 1 в очереди
    pick_time: int = 2
    drop_time: int = 1
    replan_after: int = 3             # число отказов в резерве клетки до перепланирования
    backoff_after: int = 15           # ЛР3: отказов подряд до случайной отсрочки (разрыв затора)
    heartbeat_every: int = 5

    # энергия
    battery_capacity: int = 200       # ЛР3: ёмкая батарея — длинные сеансы зарядки
    battery_reserve: int = 20
    charge_threshold: float = 0.5     # ЛР3: свободный робот подзаряжается «впрок» ниже этой доли
    charge_rate: int = 3              # ЛР3: медленная зарядка — дефицит слотов
    charger_slots: int = 1            # слотов на каждой зарядной станции
    n_stations: int = 2               # число работающих зарядных станций (1 или 2)

    # диспетчер
    est_task_time: int = 25           # оценка длительности задачи для учёта загрузки (central)
    cnp_parallel: int = 3             # одновременно объявляемых тендеров (cnp)
    cnp_retry: int = 3                # пауза до повторного тендера без ставок
    auction_period: int = 5           # период пакетного аукциона, тактов
    auction_lot: int = 10             # максимум задач в лоте
    deleg_threshold: int = 40         # робот берёт задачу, если его оценка времени не больше порога
    deleg_parallel: int = 3           # задач, запускаемых в кольцо за такт
    deleg_retry: int = 3              # пауза после неудачного круга
    deleg_timeout: int = 10           # задача, не принятая за это время, возвращается в очередь

    # срочные заказы (ЛР3): доля экспресс-заказов и нормативные сроки
    express_share: float = 0.25
    express_deadline: int = 150
    standard_deadline: int = 450

    # конфликт за слоты зарядки (ЛР3)
    charge_policy: str = "negotiation"  # ЛР4: рекомендованная конфигурация из ЛР3
    reputation: bool = True           # проверка заявок, репутация и санкции
    liars: list[str] = field(default_factory=list)   # роботы, занижающие заявленный заряд и не уступающие
    critical_battery: int = 40        # заявка «критическая» при заряде не выше (20 %)
    aging_rate: float = 1.0           # прибавка приоритета за такт ожидания
    credit_weight: float = 15.0       # прибавка приоритета за кредит взаимности
    preempt_urgency: float = 60.0     # станция просит уступить слот только ради заявки не ниже этой срочности
    release_min: float = 0.6          # уступку предлагают роботу с зарядом не ниже этой доли
    yield_accept_level: float = 0.65  # честный робот соглашается уступить при заряде не ниже этой доли
    yield_credit: int = 1
    offer_cooldown: int = 10          # пауза перед повторным предложением отказавшемуся
    claim_tolerance: int = 5          # допуск при сверке заявленного и измеренного заряда
    rep_prior: float = 4.0            # априорное доверие (псевдосчёт честных заявок)
    sanction_ticks: int = 150         # срок санкции после выявленной лжи
    grant_timeout: int = 60           # слот отзывается, если робот не подключился за это время

    # ЛР4: диверсионный анализ
    scenario: str = "clean"
    defenses: frozenset = frozenset()  # auth, validate, contain, verify_done, watchdog, timeouts, ttl,
                                       # fencing, evacuation, standby
    fault_robots: list[str] = field(default_factory=list)
    fault_start: int = 300
    fault_end: int = 1100
    drop_rate: float = 0.03
    inject_rate: float = 0.15
    charge_timeout: int = 45          # молчание станции, после которого заявка подаётся заново
    ban_after: int = 2                # подозрений до карантина исполнителя
    ban_ticks: int = 800
    watchdog_min: int = 300           # минимальный срок выполнения задачи до отзыва
    watchdog_factor: int = 4          # ... или 4 × заявленная стоимость
    evac_delay: int = 60              # время прибытия службы эвакуации
    goodput_horizon: int = 2000       # горизонт для показателя «полезная работа»

    # мониторинг
    heartbeat_timeout: int = 20

    # внедрение отказов: список (robot_id, tick)
    failures: list[tuple[str, int]] = field(default_factory=list)
