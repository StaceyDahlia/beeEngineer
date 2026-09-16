# НАЗНАЧЕНИЕ: расстояние и время в пути между двумя точками.
#
# ВХОД:
#   coords_from, coords_to: [lat, lon]
#   vehicle: тип транспорта (влияет на скорость)
#
# ВЫХОД:
#   (distance_km: float, travel_min: int)
#
# СВЯЗИ:
#   - вызывается из optimizer.py, baseline.py, metrics.py
#   - использует reference/vehicles.json (avg_speed_kmh)
#   - кэш матрицы: data/processed/osrm_cache/ (если OSRM)
#
# РЕЖИМЫ (config.distance_provider):
#   haversine — по формуле, время = distance / speed * 60
#   osrm      — GET /route/v1/driving/{lon1},{lat1};{lon2},{lat2}
