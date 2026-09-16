# НАЗНАЧЕНИЕ: ETL из data/raw/*.csv в data/processed/*.json.
#
# ВХОД:
#   data/raw/east_control.csv, east_synthetic.csv,
#   data/raw/southeast_control.csv, southeast_synthetic.csv,
#   data/raw/southcenter_control.csv, southcenter_synthetic.csv
#   data/reference/skills.json, vehicles.json, priorities.json
#
# ВЫХОД:
#   data/processed/jobs.json
#   data/processed/engineers.json
#   data/processed/events.json (шаблон событий)
#   data/processed/geocode_cache.json (через geocoder)
#
# СВЯЗИ:
#   - использует backend/app/services/normalizer.py, geocoder.py
#   - результат читает backend/app/services/loader.py
#   - запускается первым в README
#
# ЛОГИКА:
#   1. Прочитать все CSV (sep=';', encoding='utf-8-sig').
#   2. Отбросить пустые строки и строку «Адрес Офиса» (запомнить как start_point).
#   3. Маппинг «Тип заявки BK» → skill:
#        Подключение      → connect
#        Локальная заявка → local
#        Глобальная проблема (Авария) → emergency
#        Дозаказ          → connect
#   4. Маппинг «Статус BK»:
#        Отменена → cancelled (не планируем)
#        Выполнена → done (не планируем)
#        остальные → planned
#   5. Геокодинг адресов (geocoder.geocode), кэш.
#   6. Синтез инженеров из колонки «Бригада»:
#        - навыки: 1–3 случайных, но покрывающие все типы
#        - транспорт: распределить все 4 типа
#        - смена: 08:00–22:00
#        - start_point: адрес офиса
#   7. Записать jobs.json, engineers.json, events.json (шаблон).
#
# ЗАПУСК:
#   python scripts/prepare_data.py
#
# ДОПУЩЕНИЯ:
#   - если геокодинг недоступен — центроид района
#   - синтетические инженеры детерминированы (seed)
