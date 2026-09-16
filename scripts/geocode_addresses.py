# НАЗНАЧЕНИЕ: отдельный прогон геокодинга (если prepare_data уже создал jobs.json).
#
# ВХОД:  data/processed/jobs.json (адреса без coords).
# ВЫХОД: обновлённый jobs.json + geocode_cache.json.
# СВЯЗИ: backend/app/services/geocoder.py.
#
# ЗАПУСК:
#   python scripts/geocode_addresses.py --force
#
# ЗАЧЕМ ОТДЕЛЬНО:
#   Nominatim медленный (1 req/sec); удобно догонять кэш, не пересобирая всё.
