# НАЗНАЧЕНИЕ: юнит-тесты для трёх групп ограничений.
#
# ВХОД:  fixtures (job, engineer) из tests/fixtures/.
# ВЫХОД: pytest-assertions.
# СВЯЗИ: app/services/constraints.py.
#
# КЕЙСЫ:
#   1. Навык не подходит → NO_SKILL.
#   2. Транспорт не подходит → NO_VEHICLE.
#   3. Прибытие вне окна → OUT_OF_WINDOW.
#   4. Выход за смену → OUT_OF_SHIFT.
#   5. Всё ок → True.
