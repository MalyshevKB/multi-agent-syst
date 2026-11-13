"""ЛР4: злоумышленник (скомпрометированный узел сети) — сценарий «некорректная передача полномочий».

Отправляет в шину сообщения от чужого имени (подделка поля sender) и команды, на которые у него
нет полномочий. Подлинный отправитель (origin) проставляет шина — его подделать нельзя.
  * от имени диспетчера: REQUEST recharge — снимает свободных роботов с работы;
  * от имени станции:   REQUEST release_offer — выгоняет заряжающихся роботов со слота;
  * от имени робота:    INFORM charge_done — станция освобождает слот, хотя робот ещё на нём.
"""
from __future__ import annotations

from ..messages import Message
from ..messages import Performative as P
from .base import Agent


class AdversaryAgent(Agent):
    role = "adversary"

    def __init__(self, bus, cfg, robot_ids, stations):
        super().__init__("adversary", bus)
        self.cfg = cfg
        self.robot_ids = list(robot_ids)
        self.stations = list(stations)
        self.sent = 0

    def _forge(self, fake_sender, receiver, perf, content, protocol):
        self.sent += 1
        self.bus.send(Message(fake_sender, receiver, perf, content, protocol), origin=self.id)

    def step(self, t: int) -> None:
        if t < self.cfg.fault_start or t % 10:
            return
        for r in self.robot_ids:
            self._forge("dispatcher", r, P.REQUEST, {"action": "recharge", "epoch": 99}, "central-assign")
            self._forge("charger_W", r, P.REQUEST, {"action": "release_offer", "credit": 0}, "charging-negotiation")
        for st in self.stations:
            for r in self.robot_ids:
                self._forge(r, st, P.INFORM, {"event": "charge_done"}, "charging")
        for r in self.robot_ids[:2]:   # и от своего имени — команда, на которую у узла нет полномочий
            self.send(r, P.REQUEST, {"action": "recharge"}, "central-assign")
