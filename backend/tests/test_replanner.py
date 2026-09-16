# НАЗНАЧЕНИЕ: тесты перепланирования.
# ВХОД:  готовый Plan + Event.
# ВЫХОД: pytest.
# СВЯЗИ: services/replanner.py, services/explainer.py.
#
# КЕЙСЫ:
#   1. urgent_job вставляется в план, changed_job_ids непуст.
#   2. cancel_job удаляет заявку.
#   3. engineer_unavailable перераспределяет его заявки.
