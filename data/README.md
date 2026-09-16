<!--
НАЗНАЧЕНИЕ: объяснить происхождение и структуру данных.
ВХОД: CSV от организаторов, справочники.
ВЫХОД: описание папок raw/processed/reference.
СВЯЗИ: docs/data-format.md, scripts/prepare_data.py.

СОДЕРЖАНИЕ:
  1. raw/ — исходные CSV как есть (не редактируем).
  2. processed/ — нормализованные JSON, полученные из prepare_data.py.
  3. reference/ — справочники навыков, транспорта, приоритетов.
  4. Как обновлять: запустить scripts/prepare_data.py.
  5. Что не коммитим: geocode_cache.json (в .gitignore).
-->
