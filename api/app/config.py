from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# api/app/config.py -> repo root is three levels up (api/app -> api -> repo)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATHWAY = _REPO_ROOT / "config" / "pathways" / "colorectal_fit.yaml"
_DEFAULT_CALENDAR = _REPO_ROOT / "config" / "calendar.yaml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://closure:closure_dev_password@localhost:5432/closure"
    fhir_base_url: str = "http://fhir:8080/fhir"
    sim_clock_start: str = "2026-01-05T08:00:00Z"
    watchdog_idle_days: int = 7
    llm_provider: str = "template"
    # In compose this is overridden to /config/pathways/colorectal_fit.yaml
    pathway_path: str = str(_DEFAULT_PATHWAY)
    # Working-day calendar (weekend set, holidays, time zone). Compose sets /config/calendar.yaml.
    calendar_path: str = str(_DEFAULT_CALENDAR)


settings = Settings()
