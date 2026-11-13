"""ЛР4: каталог неблагоприятных сценариев и защитных ограничений."""
from __future__ import annotations

from dataclasses import replace

from .config import Config

ALL_DEFENSES = frozenset({"auth", "validate", "contain", "verify_done", "watchdog", "timeouts", "ttl",
                          "fencing", "evacuation", "standby"})

# id: (название, класс по заданию, переопределения конфигурации, ключевые защиты)
SCENARIOS: dict[str, tuple[str, str, dict, list[str]]] = {
    "clean": ("Штатная работа", "—", {}, []),
    "false_done": ("Ложные отчёты о выполнении", "недостоверное сообщение",
                   {"fault_robots": ["robot_3"]}, ["verify_done"]),
    "saboteur": ("Саботажник: заниженные ставки, задачи не выполняются", "агент с подменённой целью",
                 {"fault_robots": ["robot_3"]}, ["watchdog"]),
    "station_outage": ("Отказ зарядной станции charger_E на 800 тактов", "недоступность внешнего средства",
                       {}, ["timeouts"]),
    "spoofing": ("Подделка отправителя и команды без полномочий", "некорректная передача полномочий",
                 {}, ["auth"]),
    "delegation_loop": ("Ошибка версии: сброс счётчика хопов при делегировании", "циклическая передача задачи",
                        {"mode": "delegation", "fault_robots": ["robot_3"]}, ["ttl"]),
    "split_brain": ("Два лидера: ошибочное повышение реплики диспетчера", "противоречивые указания",
                    {}, ["fencing"]),
    "injection": ("Вредоносные данные во внешнем потоке заказов", "внедрение вредоносных инструкций",
                  {}, ["validate", "contain"]),
    "msg_loss": ("Ненадёжный канал: потеря 3 % сообщений", "потеря сообщений",
                 {}, ["timeouts"]),
    "service_crash": ("Отказ робота в точке сдачи", "отказ агента на сервисной точке",
                      {"fault_robots": ["robot_3"]}, ["evacuation"]),
    "traffic_crash": ("Отказ регулировщика движения", "единая точка отказа",
                      {}, ["standby", "timeouts"]),
}


def make_config(scenario: str, defended: bool, seed: int, **kw) -> Config:
    _, _, over, _ = SCENARIOS[scenario]
    base = Config(seed=seed, scenario=scenario, defenses=ALL_DEFENSES if defended else frozenset())
    return replace(base, **(over | kw))
