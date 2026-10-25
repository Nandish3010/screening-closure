from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# api/app/config.py -> repo root is three levels up (api/app -> api -> repo)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATHWAY = _REPO_ROOT / "config" / "pathways" / "colorectal_fit.yaml"
_DEFAULT_CALENDAR = _REPO_ROOT / "config" / "calendar.yaml"
_DEFAULT_PROVIDERS = _REPO_ROOT / "config" / "providers.yaml"
_DEFAULT_OUTREACH_POLICY = _REPO_ROOT / "config" / "outreach_policy.yaml"
_DEFAULT_AGENCY = _REPO_ROOT / "config" / "agency.yaml"


# Development-only defaults. Shipping these values anywhere but a laptop is a bug, which is
# why app.auth.assert_secure_config refuses them when APP_ENV is not "dev" (SEC-1, ADR-029), and
# app.auth.verify_token never accepts a token signed with DEV_AUTH_SECRET_DEFAULT outside dev.
DEV_AUTH_SECRET_DEFAULT = "dev-only-insecure-secret-change-me"
SCHEDULER_TOKEN_DEFAULT = "dev-only-scheduler-service-token"
WATCHDOG_TOKEN_DEFAULT = "dev-only-watchdog-service-token"
AUTOMATION_TOKEN_DEFAULT = "dev-only-automation-service-token"
AGENCY_TOKEN_DEFAULT = "dev-only-agency-service-token"
HL7_TOKEN_DEFAULT = "dev-only-hl7-service-token"
METRICS_TOKEN_DEFAULT = "dev-only-metrics-scrape-token"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://closure:closure_dev_password@localhost:5432/closure"
    fhir_base_url: str = "http://fhir:8080/fhir"
    sim_clock_start: str = "2026-01-05T08:00:00Z"
    # A30 (ADR-041): "sim" (default, demo and development) reads the persisted simulation clock and registers /clock/*; "wall"
    # reads real UTC time and registers no clock route. Chosen at start-up; outside APP_ENV=dev it must be "wall"
    # (app.auth.secure_config_problems). Anything else refuses to start.
    clock_mode: Literal["sim", "wall"] = "sim"
    # A31 (ADR-041): when true the scheduler routes refuse a call that does not carry a current leader fencing token
    # (X-Leader-Holder / X-Leader-Token). Compose's `scale` profile turns it on. Off, a call without the headers is
    # allowed (manual runs, older tests); a call WITH stale headers is refused either way.
    require_leader_fence: bool = False
    leader_lease_ttl_seconds: float = Field(default=30.0, gt=1, le=3600)
    watchdog_idle_days: int = 7
    llm_provider: str = "template"
    # --- identity (ADR-012) ------------------------------------------------------------
    # "dev" allows the built-in defaults below. Any other value (compared exactly: "Dev" is not dev)
    # refuses to start unless DEV_LOGIN_ENABLED=false, DEV_AUTH_SECRET and both service tokens are
    # non-default, at least 32 bytes, and the two service tokens differ (app.auth.assert_secure_config).
    app_env: str = "dev"
    # How a person proves who they are (A29, ADR-040): "dev" = the HMAC dev-login tokens, "oidc" = JWTs from an OIDC provider
    # (local Keycloak now, UAE PASS later). Outside APP_ENV=dev only "oidc" starts. Service tokens work in both modes.
    auth_mode: str = "dev"
    oidc_issuer: str = ""  # exactly the token's `iss`, e.g. http://localhost:8180/realms/screening-closure
    oidc_audience: str = "screening-closure"  # the token's `aud`
    oidc_jwks_url: str = ""  # default: <issuer>/protocol/openid-connect/certs (compose points it at the keycloak service)
    oidc_role_claim: str = "realm_access.roles"  # dotted path to the list of role names in the token
    oidc_role_map: str = ""  # "idp_role=our_role,..."; empty = the IdP uses our six role names
    oidc_leeway_seconds: int = 30  # clock skew allowed on exp and nbf
    oidc_jwks_cache_seconds: float = 600.0
    oidc_jwks_min_refetch_seconds: float = 30.0  # at most one refetch per this long when a kid is unknown
    oidc_jwks_stale_seconds: float = 3600.0  # keep using the last good keys this long if the provider is down
    oidc_jwks_timeout_seconds: float = 3.0
    # Dev-login token lifetime on the WALL clock (identity, not case logic; ADR-040). Refresh = log in again.
    dev_token_lifetime_seconds: int = 8 * 3600
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
    # A26 (ADR-037): the shipped outreach policy is UNSIGNED (`approved_by: null`) and the automation sends no message
    # while it is. This switch treats an unsigned policy as signed by "DEMO PLACEHOLDER (clinical sign-off pending, H1)"
    # so the demo can show the automatic path; /health reports it and the web shows an amber banner. Compose sets it;
    # outside APP_ENV=dev the API refuses to start with it on, and a real signature in the file wins over it.
    outreach_demo_approval: bool = False
    # The agency's supervisor agent (A22, ADR-033) may start an agency run (`POST /agency/run`) and nothing else.
    # Empty disables the token (a supervisor can still start a run; the scheduler hook runs in process).
    agency_service_token: str = AGENCY_TOKEN_DEFAULT
    # The HL7 v2 adapter (A28, ADR-039): `system:hl7` may POST /hl7 and nothing else; in process it may create a case through
    # the POST /cases path, book the confirmatory appointment and record confirmatory evidence (app.policy.HL7_ACTIONS).
    # Empty switches the token off (always safe); set outside dev it is checked like the other tokens.
    hl7_service_token: str = HL7_TOKEN_DEFAULT
    # F14: a bearer secret a Prometheus scraper presents to GET /metrics (a supervisor's token works too). Its development
    # default is accepted only while APP_ENV=dev; outside dev only a value someone set (32 bytes or more) opens the endpoint
    # to a scraper. Empty switches the scrape token off (a supervisor token still works).
    metrics_scrape_token: str = METRICS_TOKEN_DEFAULT
    # --- the agency (A22, ADR-033): agents are labour, not authority -----------------------------
    # The scheduler hook runs supervisor -> department heads -> executioners after every N-th sweep (0 = never).
    agency_every_sweeps: int = 6
    # Per-run limits: wall-clock seconds and executioner tasks; past either, the rest of the run is not started.
    agency_run_budget_seconds: float = 20.0
    agency_max_tasks: int = 300
    agency_config_path: str = str(_DEFAULT_AGENCY)  # providers and the call script (config/agency.yaml)
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
