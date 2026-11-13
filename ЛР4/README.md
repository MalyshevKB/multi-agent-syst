# МАС координации складских роботов — ЛР4

Исходный код к ЛР4 «Диверсионный анализ и оценка надёжности многоагентной системы».
Отчёт — [ЛР4_Отчёт.md](ЛР4_Отчёт.md). Система — из ЛР1–ЛР3. Добавлены: 10 неблагоприятных сценариев, 10 защитных
ограничений, анализатор трасс, физические инварианты и метрики «истинного» выполнения.

## Требования

Python 3.10+. Моделирование не требует внешних библиотек; для графиков (`analyze.py`) нужен `matplotlib`.

## Демонстрация

Каталог сценариев:

```bash
python run.py --list
```

Сценарий без защиты и с защитой, таблица сравнения:

```bash
python run.py --scenario spoofing
```

Все сценарии подряд:

```bash
python run.py --scenario all
```

Трасса сообщений и отчёт анализатора аномалий:

```bash
python run.py --scenario split_brain --trace trace.jsonl
```

Карта склада каждые 200 тактов:

```bash
python run.py --scenario traffic_crash --defenses off --render 200
```

| Параметр | Смысл |
|---|---|
| `--scenario id\|all` | `false_done`, `saboteur`, `station_outage`, `spoofing`, `delegation_loop`, `split_brain`, `injection`, `msg_loss`, `service_crash`, `traffic_crash`, `clean` |
| `--defenses off\|on\|both` | без защиты / все защиты / сравнение (по умолчанию) |
| `--seed S` | зерно |
| `--trace file.jsonl` | сохранить трассу и прогнать детекторы A1–A11 |
| `--render N`, `--json` | карта склада, вывод в JSON |

## Эксперименты

340 прогонов → `results/runs.csv`, `results/anomalies.csv`, `results/traces/`:

```bash
python experiments.py
```

Таблицы `results/summary.md` и графики `figures/*.png`:

```bash
python analyze.py
```

## Где что реализовано

| Файл | Содержание |
|---|---|
| `warehouse_mas/faults.py` | каталог сценариев и наборов защит |
| `warehouse_mas/security.py` | матрица полномочий, проверка схемы и семантики задач |
| `warehouse_mas/trace_analysis.py` | детекторы аномалий A1–A11 по трассе |
| `warehouse_mas/messages.py` | неподделываемое поле `origin`, ненадёжный канал |
| `warehouse_mas/agents/base.py` | проверка подлинности и полномочий, изоляция ошибок |
| `warehouse_mas/agents/adversary.py` | злоумышленник (подделка сообщений) |
| `warehouse_mas/agents/maintenance.py` | служба эвакуации |
| `warehouse_mas/agents/dispatcher.py` | сверка отчётов, сторожевой таймер, эпохи и передача дел, подтверждения |
| `warehouse_mas/agents/robot.py` | внедряемые неисправности, тайм-ауты и повторы, переключение регулировщика |
| `warehouse_mas/agents/traffic.py` | свидетель сверки, горячий резерв |
| `warehouse_mas/agents/charging.py` | keepalive, датчик присутствия, выселение захватчика слота |
| `warehouse_mas/simulation.py` | расписание отказов, физические инварианты, метрики устойчивости |
