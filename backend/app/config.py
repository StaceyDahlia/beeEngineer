# НАЗНАЧЕНИЕ: централизованные настройки из .env.
#
# ВХОД:  .env (см. .env.example).
# ВЫХОД: объект Settings, импортируется во все сервисы.
# СВЯЗИ: все services/*, api/*, scripts/*.

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    data_raw_dir: str = "data/raw"
    data_processed_dir: str = "data/processed"
    data_reference_dir: str = "data/reference"
    geocoder_provider: str = "nominatim"
    geocoder_user_agent: str = "beeEngineer/0.1"
    geocoder_cache_path: str = "data/processed/geocode_cache.json"
    distance_provider: str = "haversine"   # haversine | osrm
    osrm_base_url: str = "http://router.project-osrm.org"
    optimizer_engine: str = "ortools"      # greedy | ortools
    optimizer_time_limit_sec: int = 10

    class Config:
        env_file = ".env"

settings = Settings()
