"""Текстовая визуализация состояния склада."""
from __future__ import annotations

STATE_SHORT = {"IDLE": "idle", "TO_PICK": "->pick", "PICKING": "pick", "TO_DROP": "->drop",
               "DROPPING": "drop", "TO_HOME": "->home", "WAIT_CHARGE": "wait⚡", "TO_CHARGER": "->⚡",
               "CHARGING": "⚡", "STRANDED": "STRANDED"}


def render(sim) -> str:
    env = sim.env
    grid = [list(row) for row in env.layout]
    for i, r in enumerate(sim.robots):
        x, y = r.pos
        ch = str((i + 1) % 10)
        if not r.alive or r.state == "STRANDED":
            ch = "X"
        grid[y][x] = ch
    om = sim.order_manager
    done = sum(1 for o in om.orders.values() if o["completed"] is not None)
    lines = [f"такт {sim.t:4d} | заказы {done}/{om.generated} (из {sim.cfg.n_orders}) | "
             f"очередь задач {len(sim.dispatcher.pending)} | сообщений {sim.bus.stats['total']}"]
    lines += ["  " + " ".join(row) for row in grid]
    info = []
    for r in sim.robots:
        tid = r.task["task_id"] if r.task else "-"
        info.append(f"{r.id[6:]}:{STATE_SHORT.get(r.state, r.state)}[{r.battery}]{tid}+{len(r.queue)}")
    lines.append("  " + "  ".join(info))
    return "\n".join(lines)
