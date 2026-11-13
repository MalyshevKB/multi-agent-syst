"""Агент-реестр репутации и кредитов взаимности (ЛР3).

Получает от зарядных станций результаты проверки заявок (claim_checked) и сведения об уступках слота
(yielded, credit_spent). Ведёт:
  * репутацию r = (h + a0) / (h + l + a0) — доля подтверждённых заявок (h — честных, l — ложных,
    a0 — априорное доверие), 1.0 у нового агента;
  * кредиты взаимности — начисляются за уступку слота, тратятся при получении слота;
  * санкции — после выявленной лжи заявки агента не учитываются sanction_ticks тактов,
    накопленные кредиты сгорают.
Каждое изменение рассылается всем станциям (INFORM reputation) — у станций локальная копия реестра.
"""
from __future__ import annotations

from ..messages import Performative as P
from .base import Agent


class ReputationAgent(Agent):
    role = "reputation"

    def __init__(self, bus, cfg, robot_ids, stations):
        super().__init__("reputation", bus)
        self.cfg = cfg
        self.stations = list(stations)
        self.honest = {r: 0 for r in robot_ids}
        self.lies = {r: 0 for r in robot_ids}
        self.credit = {r: 0 for r in robot_ids}
        self.sanctioned_until = {r: -1 for r in robot_ids}
        self.sanctions = 0

    def rep(self, r) -> float:
        a0 = self.cfg.rep_prior
        return (self.honest[r] + a0) / (self.honest[r] + self.lies[r] + a0)

    def on_inform(self, msg) -> None:
        c = msg.content
        ev, r = c.get("event"), c.get("robot")
        if r not in self.honest:
            return
        if ev == "claim_checked":
            if c["lie"]:
                self.lies[r] += 1
                if self.cfg.reputation:
                    # эскалация: срок санкции растёт с числом нарушений
                    self.sanctioned_until[r] = self.now + self.cfg.sanction_ticks * self.lies[r]
                    self.credit[r] = 0
                    self.sanctions += 1
            else:
                self.honest[r] += 1
        elif ev == "yielded":
            self.credit[r] += c.get("credit", 1)
        elif ev == "credit_spent":
            self.credit[r] = max(0, self.credit[r] - 1)
        else:
            return
        for st in self.stations:
            self.send(st, P.INFORM, {"event": "reputation", "robot": r, "rep": round(self.rep(r), 3),
                                     "credit": self.credit[r], "sanctioned_until": self.sanctioned_until[r]},
                      "reputation")
