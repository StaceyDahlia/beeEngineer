# НАЗНАЧЕНИЕ: собрать таблицу baseline vs optimizer для README.
#
# ВХОД:  data/processed/jobs.json, engineers.json
# ВЫХОД: examples/baseline_vs_optimizer.md (или печать в консоль)
# СВЯЗИ: backend/app/services/{baseline,optimizer,metrics}.py
#
# ЗАПУСК:
#   python scripts/compare_baseline.py
#
# ЧТО СЧИТАЕТ:
#   - engineers_used (baseline vs optimizer)
#   - total_distance_km
#   - unassigned_count
#   - per_engineer_distance_km
