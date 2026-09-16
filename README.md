# beeEngineer

<!--
НАЗНАЧЕНИЕ:
  Главный вход в проект. Первое, что видит эксперт и проверяющий.
  Должен позволить запустить прототип по инструкции за 5–10 минут.

ВХОД:
  Нет. Это документация.

ВЫХОД:
  Понятная инструкция: как поставить зависимости, подготовить данные,
  поднять backend, открыть frontend, запустить сценарий демо.

СВЯЗИ:
  Ссылается на docs/*, scripts/*, backend/README.md, frontend/README.md.
  Содержит актуальные команды из scripts/run_demo.py.

ОБЯЗАТЕЛЬНЫЕ РАЗДЕЛЫ:
  1. Что это и какую задачу решает (1 абзац).
  2. Скриншот интерфейса.
  3. Схема решения (ASCII или картинка):
     CSV → loader → normalizer → constraints → optimizer → API → frontend.
  4. Быстрый старт:
       python scripts/prepare_data.py
       uvicorn backend.app.main:app --reload
       open frontend/index.html
  5. Метрики: baseline vs optimizer (таблица).
  6. Допущения (ссылка на docs/assumptions.md).
  7. Известные ограничения (ссылка на docs/limitations.md).
  8. Структура репозитория (кратко).
  9. Команда и распределение ролей.
-->
