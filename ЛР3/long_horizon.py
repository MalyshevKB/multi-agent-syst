"""Проверка на длинном горизонте (отчёт ЛР3, п. 4.2): 150 заказов, 2 лжеца, 10 зёрен.

Сравнивает среднее ожидание слота у лжецов и у честных роботов
для переговоров без репутации и с репутацией.

    python long_horizon.py
"""
import statistics as st
from concurrent.futures import ProcessPoolExecutor

from experiments import cfg
from warehouse_mas.simulation import Simulation

LIARS = ["robot_1", "robot_2"]
VARIANTS = ("negotiation", "negotiation+rep")
SEEDS = range(1, 11)


def one(job):
    variant, seed = job
    m = Simulation(cfg(variant, seed, liars=LIARS, n_orders=150)).run()
    return variant, m["charge_wait_liars"], m["charge_wait_honest"]


def main():
    jobs = [(v, s) for v in VARIANTS for s in SEEDS]
    with ProcessPoolExecutor() as ex:
        res = list(ex.map(one, jobs))
    print(f"{'вариант':18s} {'лжецы':>8s} {'честные':>8s}")
    for v in VARIANTS:
        rows = [r for r in res if r[0] == v]
        liars = st.mean(r[1] for r in rows if r[1] is not None)
        honest = st.mean(r[2] for r in rows if r[2] is not None)
        print(f"{v:18s} {liars:8.1f} {honest:8.1f}   (лжецы / честные = {liars / honest:.2f})")


if __name__ == "__main__":
    main()
