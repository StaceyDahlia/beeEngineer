# НАЗНАЧЕНИЕ: локальный прогон сценария демонстрации без UI.
#
# ВХОД:
#   data/processed/jobs.json, engineers.json, events.json
#
# ВЫХОД:
#   В консоль: план, метрики, объяснения; после события — diff.
#
# СВЯЗИ:
#   - backend/app/services/{loader,optimizer,baseline,replanner,metrics,explainer}.py
#   - examples/demo_scenario.md (тот же сценарий)
#
# СЦЕНАРИЙ:
#   1. Загрузить данные.
#   2. optimize → напечатать метрики и план.
#   3. baseline → напечатать метрики.
#   4. Применить event из events.json (по очереди).
#   5. replan → напечатать changed_job_ids и новое объяснение.
#
# ЗАПУСК:
#   python scripts/run_demo.py
