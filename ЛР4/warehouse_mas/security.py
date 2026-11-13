"""ЛР4: защитные ограничения — матрица полномочий и проверка входных данных.

Матрица полномочий реализует таблицу «Полномочия» из спецификации ЛР1: кто вправе отправлять
какие команды какому агенту. Подлинность отправителя (msg.origin == msg.sender) проверяется
в Agent.receive; здесь — право роли отправителя на данное действие.
"""
from __future__ import annotations


def role_of(aid: str) -> str:
    for prefix, role in (("robot_", "robot"), ("dispatcher", "dispatcher"), ("charger_", "charger"),
                         ("traffic", "traffic"), ("monitor", "monitor"), ("order_manager", "order_manager"),
                         ("reputation", "reputation"), ("maintenance", "maintenance")):
        if aid.startswith(prefix):
            return role
    return "unknown"


# (роль получателя, ключ сообщения) -> роли, которым это разрешено.
# Ключ: action для REQUEST/AGREE/REFUSE, event для INFORM, иначе перформатив.
RULES: dict[tuple[str, str], set[str]] = {
    # роботу
    ("robot", "assign"): {"dispatcher"},
    ("robot", "recharge"): {"dispatcher"},
    ("robot", "delegate"): {"dispatcher", "robot"},
    ("robot", "delegate_final"): {"robot"},
    ("robot", "release_offer"): {"charger"},
    ("robot", "yield"): {"traffic"},
    ("robot", "report_position"): {"traffic"},
    ("robot", "reserve"): {"traffic"},
    ("robot", "charge"): {"charger"},
    ("robot", "cfp"): {"dispatcher"},
    ("robot", "accept-proposal"): {"dispatcher"},
    ("robot", "reject-proposal"): {"dispatcher"},
    ("robot", "charge_offer"): {"charger"},
    ("robot", "charge_queued"): {"charger"},
    ("robot", "charge_revoked"): {"charger"},
    ("robot", "task_revoked"): {"dispatcher"},
    ("robot", "done_ack"): {"dispatcher"},
    ("order_manager", "submit_ack"): {"dispatcher"},
    ("dispatcher", "report_ack"): {"order_manager"},
    ("robot", "traffic_failover"): {"monitor"},
    # зарядной станции
    ("charger", "charge"): {"robot"},
    ("charger", "charge_query"): {"robot"},
    ("charger", "charge_done"): {"robot"},
    ("charger", "charging_started"): {"robot"},
    ("charger", "charge_cancel"): {"robot"},
    ("charger", "release"): {"robot"},
    ("charger", "reputation"): {"reputation"},
    ("charger", "robot_failed"): {"monitor"},
    # диспетчеру
    ("dispatcher", "submit_task"): {"order_manager"},
    ("dispatcher", "task_done"): {"robot", "dispatcher"},
    ("dispatcher", "task_accepted"): {"robot"},
    ("dispatcher", "tasks_accepted"): {"robot"},
    ("dispatcher", "status"): {"robot"},
    ("dispatcher", "propose"): {"robot"},
    ("dispatcher", "robot_failed"): {"monitor"},
    ("dispatcher", "verify_result"): {"traffic"},
    ("dispatcher", "handover"): {"dispatcher"},
    ("dispatcher", "promote"): {"monitor"},
    # регулировщику
    ("traffic", "reserve"): {"robot"},
    ("traffic", "moved"): {"robot"},
    ("traffic", "position"): {"robot"},
    ("traffic", "verify"): {"dispatcher"},
    ("traffic", "robot_failed"): {"monitor"},
    ("traffic", "promote"): {"monitor"},
    ("traffic", "removed"): {"maintenance"},
    # реестру репутации
    ("reputation", "claim_checked"): {"charger"},
    ("reputation", "yielded"): {"charger"},
    ("reputation", "credit_spent"): {"charger"},
    # менеджеру заказов
    ("order_manager", "task_done"): {"dispatcher"},
}


def message_key(msg) -> str:
    c = msg.content or {}
    return c.get("action") or c.get("event") or msg.performative.value


def authorized(msg, receiver_id: str) -> bool:
    allowed = RULES.get((role_of(receiver_id), message_key(msg)))
    return allowed is None or role_of(msg.sender) in allowed


# ------------------------------------------------------------------ проверка входных данных
def _cell(v):
    # строгая проверка типа: координаты — кортеж из двух int (список — «подмена типа»)
    return type(v) is tuple and len(v) == 2 and all(type(x) is int for x in v)


def validate_task(task: dict, env) -> tuple[bool, str]:
    """Схема и семантика задачи из внешнего потока заказов (защита «validate»)."""
    try:
        if not isinstance(task.get("task_id"), str):
            return False, "schema:task_id"
        for key in ("pick", "drop", "shelf"):
            if not _cell(task.get(key)):
                return False, f"schema:{key}"
        pick, drop, shelf = tuple(task["pick"]), tuple(task["drop"]), tuple(task["shelf"])
        if shelf not in env.cells or env.kind(shelf) != "#":
            return False, "semantic:shelf"
        if pick != env.pick_point(shelf):
            return False, "semantic:pick"
        if drop not in {env.drop_point(s) for s in env.stations}:
            return False, "semantic:drop"
        if task.get("priority") not in (1, 2):
            return False, "semantic:priority"
        if not isinstance(task.get("deadline"), int) or task["deadline"] <= task.get("created", 0):
            return False, "semantic:deadline"
    except Exception:  # noqa: BLE001
        return False, "schema:exception"
    return True, ""
