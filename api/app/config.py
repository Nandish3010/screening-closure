from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# api/app/config.py -> repo root is three levels up (api/app -> api -> repo)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATHWAY = _REPO_ROOT / "config" / "pathways" / "colorectal_fit.yaml"
_DEFAULT_CALENDAR = _REPO_ROOT / "config" / "calendar.yaml"
_DEFAULT_PROVIDERS = _REPO_ROOT / "config" / "providers.yaml"
_DEFAULT_OUTREACH_POLICY = _REPO_ROOT / "config" / "outreach_policy.yaml"


# Development-only defaults. Shipping these values anywhere but a laptop is a bug, which is
# why app.auth.assert_secure_config refuses them when APP_ENV is not "dev" (SEC-1, ADR-029), and
# app.auth.verify_token never accepts a token signed with DEV_AUTH_SECRET_DEFAULT outside dev.
DEV_AUTH_SECRET_DEFAULT = "dev-only-insecure-secret-change-me"
SCHEDULER_TOKEN_DEFAULT = "dev-only-scheduler-service-token"
WATCHDOG_TOKEN_DEFAULT = "dev-only-watchdog-service-token"
AUTOMATION_TOKEN_DEFAULT = "dev-only-automation-service-token"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://closure:closure_dev_password@localhost:5432/closure"
    fhir_base_url: str = "http://fhir:8080/fhir"
    sim_clock_start: str = "2026-01-05T08:00:00Z"
    watchdog_idle_days: int = 7
    llm_provider: str = "template"
    # --- identity (ADR-012) ------------------------------------------------------------
    # "dev" allows the built-in defaults below. Any other value (compared exactly: "Dev" is not dev)
    # refuses to start unless DEV_LOGIN_ENABLED=false, DEV_AUTH_SECRET and both service tokens are
    # non-default, at least 32 bytes, and the two service tokens differ (app.auth.assert_secure_config).
    app_env: str = "dev"
    dev_login_enabled: bool = True
    dev_auth_secret: str = DEV_AUTH_SECRET_DEFAULT
    # Static bearer tokens for the services. A service token maps to a fixed identity
    # (system:scheduler) and a fixed set of event types (app.policy.SERVICE_EVENT_POLICY).
    scheduler_service_token: str = SCHEDULER_TOKEN_DEFAULT
    # The watchdog has its own token and may emit only `watchdog_flagged` (A11).
    watchdog_service_token: str = WATCHDOG_TOKEN_DEFAULT
    # The automation actor (A21, ADR-032): `system:automation` may emit only the handful of events listed in
    # app.policy.EVENT_POLICY (acknowledge, assign_ownership, handoff_requested for a reroute, complete_confirmatory,
    # closure_proposed) and call the scheduler's automation scans. It can never close a case. Same rules as the
    # other service tokens outside dev: not the default, at least 32 bytes, distinct.
    automation_service_token: str = AUTOMATION_TOKEN_DEFAULT
    # Master switch for the automation (A21): when false (the default, so a bare dev run and the older tests behave as
    # before) POST /cases appends nothing but the genesis and the scheduler's automation scans do nothing. Compose
    # sets it to true.
    automation_enabled: bool = False
    # config/providers.yaml (registration owners, reroute order) and config/outreach_policy.yaml (the pre-approved
    # templates the automation may send). Compose points them at /config/.
    providers_path: str = str(_DEFAULT_PROVIDERS)
    outreach_policy_path: str = str(_DEFAULT_OUTREACH_POLICY)
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
    # Retry of a failed projection (SEC-1): the first retry is at the next drain pass, then the wait doubles from
    # the base up to the cap; after max_attempts the event is dead-lettered and GET /alerts reports it.
    fhir_retry_base_seconds: float = 30.0
    fhir_retry_cap_seconds: float = 3600.0
    fhir_retry_max_attempts: int = 12
    # Browser origins allowed to call the API (comma separated). The web app runs on its own port
    # (vite dev 5173, vite preview 4173/4175), so the browser needs CORS including the Authorization
    # header. Local development origins only by default; never a wildcard.
    cors_allow_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173,"
        "http://localhost:4175,http://127.0.0.1:4175"
    )
    # Working-day calendar (weekend set, holidays, time zone). Compose sets /config/calendar.yaml.
    calendar_path: str = str(_DEFAULT_CALENDAR)


settings = Settings()
