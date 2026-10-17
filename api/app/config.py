from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# api/app/config.py -> repo root is three levels up (api/app -> api -> repo)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATHWAY = _REPO_ROOT / "config" / "pathways" / "colorectal_fit.yaml"
_DEFAULT_CALENDAR = _REPO_ROOT / "config" / "calendar.yaml"


# Development-only defaults. Shipping these values anywhere but a laptop is a bug, which is
# why app.auth.assert_secure_config refuses them when APP_ENV is not "dev".
DEV_AUTH_SECRET_DEFAULT = "dev-only-insecure-secret-change-me"
SCHEDULER_TOKEN_DEFAULT = "dev-only-scheduler-service-token"
WATCHDOG_TOKEN_DEFAULT = "dev-only-watchdog-service-token"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://closure:closure_dev_password@localhost:5432/closure"
    fhir_base_url: str = "http://fhir:8080/fhir"
    sim_clock_start: str = "2026-01-05T08:00:00Z"
    watchdog_idle_days: int = 7
    llm_provider: str = "template"
    # --- identity (ADR-012) ------------------------------------------------------------
    # "dev" allows the built-in defaults below. Any other value refuses to start with a
    # default secret (app.auth.assert_secure_config).
    app_env: str = "dev"
    dev_login_enabled: bool = True
    dev_auth_secret: str = DEV_AUTH_SECRET_DEFAULT
    # Static bearer tokens for the services. A service token maps to a fixed identity
    # (system:scheduler) and a fixed set of event types (app.policy.SERVICE_EVENT_POLICY).
    scheduler_service_token: str = SCHEDULER_TOKEN_DEFAULT
    # The watchdog has its own token and may emit only `watchdog_flagged` (A11).
    watchdog_service_token: str = WATCHDOG_TOKEN_DEFAULT
    # In compose this is overridden to /config/pathways/colorectal_fit.yaml
    pathway_path: str = str(_DEFAULT_PATHWAY)
    # Closure evidence (A15, ADR-021): when true, `close` requires evidence_ref to resolve in HAPI.
    # Compose sets it to true; unit tests use a fake verifier instead.
    fhir_verify_evidence: bool = False
    fhir_timeout_seconds: float = 5.0
    # FHIR projection of the event stream to HAPI (A12, ADR-024). Compose turns it on; unit tests and
    # no-docker runs leave it off.
    fhir_projection: bool = False
    fhir_projection_drain_seconds: float = 20.0
    # Working-day calendar (weekend set, holidays, time zone). Compose sets /config/calendar.yaml.
    calendar_path: str = str(_DEFAULT_CALENDAR)


settings = Settings()
