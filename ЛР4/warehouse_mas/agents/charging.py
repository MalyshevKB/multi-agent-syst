"""Агент зарядной станции — арбитр дефицитного ресурса (слотов зарядки).

Политики распределения слотов (cfg.charge_policy), ЛР3:
  fifo        — очередь «первым пришёл — первым обслужен», робот заряжается до 100 % (базовая, ЛР1–2);
  priority    — арбитраж по приоритету: срочность по заявленному заряду + старение заявки (aging);
  negotiation — priority + переговоры о досрочном освобождении слота: станция предлагает заряжающемуся
                роботу уступить слот срочной заявке за кредит (взаимность); кредиты повышают приоритет;
  при cfg.reputation = True заявленный заряд проверяется измерением при подключении; ложь снижает
  репутацию (вес заявок) и влечёт санкцию — заявки нарушителя временно не учитываются.
"""
from __future__ import annotations

from ..messages import Performative as P
from .base import Agent


class ChargingStationAgent(Agent):
    role = "charging_station"

    def __init__(self, aid, bus, cfg, slots, wait_cells, meter, est_charge_time: int = 20, presence=None):
        super().__init__(aid, bus)
        self.cfg = cfg
        self.presence = presence                # ЛР4: датчик присутствия на слоте (кто физически стоит)
        self.leaks_fixed = 0
        self.squat_since: dict = {}
        self.squatters_reported = 0
        self.meter = meter                      # физическое измерение заряда при подключении к слоту
        self.slots: dict[tuple, str | None] = {c: None for c in slots}
        self.started: dict[str, int] = {}       # робот -> такт начала зарядки (стоит на слоте)
        self.granted_at: dict[str, int] = {}    # робот -> такт выдачи слота
        self.wait_cells: dict[tuple, str | None] = {c: None for c in wait_cells}
        self.queue: list[str] = []
        self.claims: dict[str, dict] = {}       # робот -> заявка {battery, critical, at}
        self.est_charge_time = est_charge_time
        # локальная копия реестра репутации/кредитов (рассылается агентом reputation)
        self.rep: dict[str, float] = {}
        self.credits: dict[str, int] = {}
        self.sanctioned_until: dict[str, int] = {}
        # переговоры
        self.offer: dict | None = None          # открытое предложение уступить слот
        self.refused_at: dict[str, int] = {}
        # статистика
        self.served = 0
        self.total_wait = 0
        self.max_queue = 0
        self.waits: list[tuple[str, int, bool]] = []   # (робот, ожидание, заявка честно-критическая)
        self.offers_made = self.offers_accepted = self.offers_refused = 0
        self.lies_detected = 0
        self.revocations = 0

    # ================================================================ приоритет
    def urgency(self, r) -> float:
        """Срочность по заявленному заряду (0..150) с учётом репутации и санкций."""
        cap = self.cfg.battery_capacity
        b = self.claims[r]["battery"]
        if self.cfg.reputation:
            if self.sanctioned_until.get(r, -1) > self.now:
                return 0.0                       # санкция: заявка не учитывается
            # недоверие: заявленный заряд смещается к «полному» пропорционально (1 - репутация),
            # так что занижение заряда перестаёт окупаться уже после пары разоблачений
            rho = self.rep.get(r, 1.0)
            b = rho * b + (1 - rho) * cap
        u = 100.0 * (1 - b / cap)
        if b <= self.cfg.critical_battery:
            u += 50.0
        return u

    def priority(self, r) -> float:
        waited = self.now - self.claims[r]["at"]
        p = self.urgency(r) + self.cfg.aging_rate * waited          # старение — защита от голодания
        if self.cfg.charge_policy == "negotiation":
            sanctioned = self.cfg.reputation and self.sanctioned_until.get(r, -1) > self.now
            if not sanctioned:
                p += self.cfg.credit_weight * self.credits.get(r, 0)  # взаимность: кредит за уступку
        return p

    def _next_in_queue(self) -> str | None:
        if not self.queue:
            return None
        if self.cfg.charge_policy == "fifo":
            return self.queue[0]
        return max(self.queue, key=lambda r: (self.priority(r), -self.claims[r]["at"], r))

    # ================================================================ протокол
    def on_request(self, msg) -> None:
        a, r = msg.content.get("action"), msg.sender
        if a == "charge_query":   # оценка ожидания для выбора станции роботом
            free = sum(1 for o in self.slots.values() if o is None)
            est = 0 if free > len(self.queue) else \
                (len(self.queue) - free + 1) * self.est_charge_time // len(self.slots)
            self.send(r, P.INFORM, {"event": "charge_offer", "est_wait": est}, "charging")
            return
        if a != "charge" or r in self.slots.values() or r in self.queue:
            return
        c = msg.content
        self.claims[r] = {"battery": c.get("claimed_battery", 0),
                          "critical": c.get("claimed_battery", 999) <= self.cfg.critical_battery,
                          # истинный заряд — только для статистики эксперимента (инструментирование
                          # симулятора, как и проверка при подключении); в решениях станции не участвует
                          "true_battery": self.meter(r),
                          "at": self.now}
        self.queue.append(r)
        self.max_queue = max(self.max_queue, len(self.queue))
        self._serve()
        if r in self.queue:
            self._free_wait_cell(r)              # одно место ожидания на робота
            cell = next((cc for cc, o in self.wait_cells.items() if o is None), None)
            if cell is not None:
                self.wait_cells[cell] = r
            self.send(r, P.INFORM, {"event": "charge_queued", "position": len(self.queue),
                                    "wait_cell": cell}, "charging")

    def on_inform(self, msg) -> None:
        ev, r = msg.content.get("event"), msg.sender
        if ev == "charging_started":       # робот подключился: место ожидания свободно, проверка заявки
            self._free_wait_cell(r)
            self.started[r] = self.now
            self._verify(r)
        elif ev in ("charge_done", "charge_cancel"):
            self._release(r)
        elif ev == "robot_failed":
            failed = msg.content["robot"]
            if failed in self.queue:
                self.queue.remove(failed)
        elif ev == "reputation":           # обновление реестра от агента reputation
            rr = msg.content["robot"]
            self.rep[rr] = msg.content["rep"]
            self.credits[rr] = msg.content["credit"]
            self.sanctioned_until[rr] = msg.content["sanctioned_until"]
        self._serve()

    def on_agree(self, msg) -> None:      # робот согласился уступить слот
        if msg.protocol != "charging-negotiation" or self.offer is None or self.offer["robot"] != msg.sender:
            return
        self.offers_accepted += 1
        self.send("reputation", P.INFORM, {"event": "yielded", "robot": msg.sender,
                                           "credit": self.cfg.yield_credit}, "reputation")
        self.offer = None                 # слот освободится по INFORM charge_done от робота

    def on_refuse(self, msg) -> None:
        if msg.protocol != "charging-negotiation" or self.offer is None or self.offer["robot"] != msg.sender:
            return
        self.offers_refused += 1
        self.refused_at[msg.sender] = self.now
        self.offer = None

    # ================================================================ переговоры об уступке
    def step(self, t: int) -> None:
        if self.has("timeouts"):
            if t % 10 == 0:          # keepalive ожидающим и получившим слот: станция жива, заявка в силе
                for r in list(self.queue) + [o for o in self.slots.values() if o is not None]:
                    self.send(r, P.INFORM, {"event": "charge_keepalive"}, "charging")
            if self.offer is not None and t - self.offer["at"] > 5:
                self.offer = None    # ответ на предложение потерян
            if self.presence is not None and self.has("evacuation"):
                for slot, o in self.slots.items():   # захват слота: на нём стоит робот, которому слот не выдан
                    who = self.presence(slot)
                    if who is not None and who != o:
                        since = self.squat_since.setdefault(slot, t)
                        if t - since == 80:
                            self.squatters_reported += 1
                            self.send("maintenance", P.REQUEST, {"action": "evacuate", "robot": who}, "maintenance")
                    else:
                        self.squat_since.pop(slot, None)
            if self.presence is not None:   # сверка с датчиком: подключённого робота на слоте нет — слот свободен
                for slot, o in list(self.slots.items()):
                    if o is not None and o in self.started and t - self.started[o] > 3 and self.presence(slot) != o:
                        self.leaks_fixed += 1
                        self._release(o)
        # правило арбитра (все политики): слот, к которому робот не подключился за grant_timeout, отзывается
        for slot, o in self.slots.items():
            if o is not None and o not in self.started and t - self.granted_at.get(o, t) > self.cfg.grant_timeout:
                self.slots[slot] = None
                self.granted_at.pop(o, None)
                self.revocations += 1
                self.send(o, P.INFORM, {"event": "charge_revoked"}, "charging")
        self._serve()
        if self.cfg.charge_policy != "negotiation" or self.offer is not None or not self.queue:
            return
        head = self._next_in_queue()
        if head is None or self.urgency(head) < self.cfg.preempt_urgency:
            return                         # уступку просим только ради срочной заявки
        best = None
        for slot, o in self.slots.items():
            if o is None or o not in self.started:
                continue
            if t - self.refused_at.get(o, -10 ** 6) < self.cfg.offer_cooldown:
                continue
            b = self.meter(o)
            if b >= self.cfg.release_min * self.cfg.battery_capacity and (best is None or b > best[0]):
                best = (b, o)
        if best is None:
            return
        o = best[1]
        self.offer = {"robot": o, "for": head, "at": t}
        self.offers_made += 1
        self.send(o, P.REQUEST, {"action": "release_offer", "credit": self.cfg.yield_credit,
                                 "requester_urgency": round(self.urgency(head), 1)}, "charging-negotiation")

    # ================================================================ проверка заявки
    def _verify(self, r) -> None:
        claim = self.claims.get(r)
        if claim is None:
            return
        actual = self.meter(r)            # заряд, измеренный станцией при подключении
        lie = actual > claim["battery"] + self.cfg.claim_tolerance   # в пути заряд мог только убыть
        if lie:
            self.lies_detected += 1
        self.send("reputation", P.INFORM, {"event": "claim_checked", "robot": r, "claimed": claim["battery"],
                                           "measured": actual, "lie": lie}, "reputation")

    # ================================================================ служебное
    def _free_wait_cell(self, r) -> None:
        for c, o in self.wait_cells.items():
            if o == r:
                self.wait_cells[c] = None

    def _release(self, r) -> None:
        for c, o in self.slots.items():
            if o == r:
                self.slots[c] = None
        self.started.pop(r, None)
        self.granted_at.pop(r, None)
        if self.offer and self.offer["robot"] == r:
            self.offer = None
        self._free_wait_cell(r)
        if r in self.queue:
            self.queue.remove(r)

    def _serve(self) -> None:
        for c, o in self.slots.items():
            if o is not None:
                continue
            if self.has("timeouts") and self.presence is not None and self.presence(c) is not None:
                continue                         # слот физически занят (робот ещё не уехал) — не выдаём
            r = self._next_in_queue()
            if r is None:
                return
            self.queue.remove(r)
            self.slots[c] = r
            self.granted_at[r] = self.now
            self.served += 1
            claim = self.claims[r]
            wait = self.now - claim["at"]
            self.total_wait += wait
            self.waits.append((r, wait, claim["true_battery"]))
            if self.cfg.charge_policy == "negotiation" and self.credits.get(r, 0) > 0:
                self.send("reputation", P.INFORM, {"event": "credit_spent", "robot": r}, "reputation")
            self.send(r, P.AGREE, {"action": "charge", "slot": c}, "charging")
