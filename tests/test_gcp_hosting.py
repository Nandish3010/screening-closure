"""Hosted judge demo on Google Cloud (deploy/gcp, docs/governance/hosting-gcp.md): config tests, no cloud and no Docker.

The load-bearing one: the env files the VM renders from Secret Manager must pass the API's own fail-closed production check.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
import yaml

import compose_lint as cl
from conftest import REPO_ROOT

GCP = REPO_ROOT / "deploy" / "gcp"
sys.path.insert(0, str(GCP / "vm"))
import render_env  # noqa: E402
import render_realm  # noqa: E402

HOST = "34-1-2-3.sslip.io"
SECRETS = {n: (n.replace("-", "") + "0123456789abcdef" * 4)[:48] for n in render_env.REQUIRED}  # distinct, 48 chars


def _settings_from(files: dict, monkeypatch):
    from app.config import Settings, settings

    env = {}
    for name in ("compose.env", "api.env"):
        env.update(files[name])
    env["FHIR_VERIFY_EVIDENCE"] = "true"  # docker-compose.yml sets it for the api service
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    fresh = Settings(_env_file=None)
    for field in Settings.model_fields:
        monkeypatch.setattr(settings, field, getattr(fresh, field))
    return fresh


def test_production_files_pass_the_fail_closed_check(monkeypatch):
    from app import auth

    files = render_env.build_files(HOST, SECRETS, "production")
    s = _settings_from(files, monkeypatch)
    assert auth.secure_config_problems() == []
    assert s.app_env == "production" and s.auth_mode == "oidc"
    assert s.clock_mode == "sim" and s.demo_simulated_clock and s.demo_deployment  # the narrow demo gate, both flags
    assert s.oidc_issuer == f"https://{HOST}/auth/realms/screening-closure"
    assert s.dev_login_enabled is False and s.outreach_demo_approval is False


def test_either_demo_flag_alone_would_not_start(monkeypatch):
    from app import auth

    files = render_env.build_files(HOST, SECRETS, "production")
    del files["api.env"]["DEMO_DEPLOYMENT"]
    _settings_from(files, monkeypatch)
    monkeypatch.setattr(__import__("app.config", fromlist=["settings"]).settings, "demo_deployment", False)
    assert any("DEMO_DEPLOYMENT" in p for p in auth.secure_config_problems())


def test_seed_mode_is_dev_and_never_production(monkeypatch):
    files = render_env.build_files(HOST, SECRETS, "seed")
    assert files["api.env"]["APP_ENV"] == "dev" and files["compose.env"]["CLOCK_MODE"] == "sim"


def test_llm_is_replay_only_with_a_zero_spend_cap_and_no_key():
    f = render_env.build_files(HOST, SECRETS)
    c = f["compose.env"]
    assert (c["LLM_CACHE_MODE"], c["LLM_NETWORK_ENABLED"], c["LLM_DAILY_TOKEN_BUDGET"]) == ("replay", "false", "0")
    assert c["TRIAGE_REMOTE_URL"] == "http://triage:8765"  # the on-premises GGUF build, never a third-party AI
    assert f["api.env"]["TRIAGE_CALIBRATION_PATH"].endswith("calibration_gguf.json")
    assert not any("ANTHROPIC" in k for d in f.values() for k in d)


def test_service_tokens_are_scoped_per_container():
    f = render_env.build_files(HOST, SECRETS)
    assert list(f["scheduler.env"]) == ["SCHEDULER_SERVICE_TOKEN"] and list(f["watchdog.env"]) == ["WATCHDOG_SERVICE_TOKEN"]


def test_missing_secret_and_newline_are_refused():
    with pytest.raises(ValueError, match="missing"):
        render_env.build_files(HOST, {k: v for k, v in SECRETS.items() if k != "pw-patient"})
    with pytest.raises(ValueError, match="newline"):
        render_env._lines({"A": "x\ny"})


def test_realm_has_only_the_public_host_and_brute_force_protection():
    src = json.loads((REPO_ROOT / "config/keycloak/screening-closure-realm.json").read_text())
    out = render_realm.render(src, HOST)
    web = next(c for c in out["clients"] if c["clientId"] == "screening-closure-web")
    assert web["redirectUris"] == [f"https://{HOST}/*"] and web["webOrigins"] == [f"https://{HOST}"]
    assert out["bruteForceProtected"] is True
    assert "localhost" not in json.dumps(out)
    assert all(u["credentials"][0]["value"].startswith("${KC_PW_") for u in out["users"])  # no password in the file


def test_only_caddy_is_published_publicly():
    data = yaml.load((GCP / "compose.gcp.yml").read_text(), Loader=cl._Loader)
    findings = cl.lint_data(data, "gcp")
    assert len(findings) == 2 and all("'caddy'" in f for f in findings), findings  # 80 and 443, nothing else
    for name in ("caddy", "web", "keycloak-prod"):
        assert data["services"][name]["profiles"] == ["public"]  # the seed phase has no public listener
    for name in ("triage", "triage-llama"):
        assert data["services"][name]["profiles"] == ["triage"] and "ports" not in data["services"][name]  # private network only
    assert "postgres" not in str(data["services"].get("postgres", {}).get("ports", ""))


def test_overlay_has_no_literal_secret_and_requires_the_rendered_ones():
    text = (GCP / "compose.gcp.yml").read_text()
    for must in ("KC_DB_PASSWORD: ${KC_DB_PASSWORD:?", "KC_PW_PATIENT: ${KC_PW_PATIENT:?", "/etc/sc/api.env"):
        assert must in text
    assert "dev-only" not in text and "closure_dev_password" not in text


def test_edge_limits_and_blocks():
    t = (GCP / "edge/nginx.conf.template").read_text()
    for must in ("limit_req_zone", "limit_req zone=api_ip", "limit_req zone=login_ip", "set_real_ip_from",
                 "scheduler|watchdog|hl7", "location = /api/clock/advance", "location ~ ^/api/clock { return 404; }",
                 "location = /api/metrics { return 404; }", r"^/auth/(admin|realms/master)",
                 r"location ~ ^/api/(docs|redoc|openapi\.json)(/|$) { return 404; }"):
        assert must in t, must
    assert "Strict-Transport-Security" in (GCP / "edge/Caddyfile").read_text()


def test_shell_scripts_parse_and_deploy_refuses_without_the_flag():
    for f in [*GCP.glob("*.sh"), *GCP.glob("vm/*.sh"), REPO_ROOT / "tools/demo/snapshot.sh"]:
        assert subprocess.run(["bash", "-n", str(f)]).returncode == 0, f
    r = subprocess.run(["bash", str(GCP / "deploy.sh")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "--yes-spend" in r.stdout


def test_scripts_never_log_in_create_accounts_or_print_secrets():
    for f in GCP.rglob("*.sh"):
        lines = [l for l in f.read_text().splitlines() if not l.lstrip().startswith("#") and "echo" not in l]
        body = "\n".join(lines)
        assert "gcloud auth login" not in body, f
        assert "echo $secrets_json" not in body and 'echo "$secrets_json"' not in body


def test_hosted_build_flag_is_set_for_the_honest_reply_text():
    assert "VITE_HOSTED_DEMO=1" in (GCP / "edge/Dockerfile").read_text()
    assert "VITE_HOSTED_DEMO" in (REPO_ROOT / "web/src/i18n/strings.ts").read_text()


def test_secret_list_in_lib_matches_the_renderer():
    import re

    names = re.search(r'SC_ITEMS="([^"]+)"', (GCP / "lib.sh").read_text()).group(1).split()
    assert sorted(names) == sorted(render_env.REQUIRED)


def test_triage_is_on_premises_and_the_hosted_label_says_so():
    strings = (REPO_ROOT / "web/src/i18n/strings.ts").read_text()
    assert "On-premises model (runs on our server, no cloud AI)" in strings
    text = (GCP / "compose.gcp.yml").read_text()
    assert "ghcr.io/ggml-org/llama.cpp:server" in text and "app.triage.gguf_server" in text
    assert "ANTHROPIC" not in text and "anthropic.com" not in text


def test_demo_clock_banner_and_gate_are_wired():
    assert "demo-clock-banner" in (REPO_ROOT / "web/src/components/DemoBanner.tsx").read_text()
    assert "DemoBanner" in (REPO_ROOT / "web/src/components/Header.tsx").read_text()
    assert "Demo, synthetic data, simulated time" in (REPO_ROOT / "web/src/i18n/strings.ts").read_text()


def test_keycloak_import_dir_is_readable_by_the_container_user():
    # sc-up.sh runs under umask 077; Keycloak (uid 1000) must still read the import dir or it crash-loops with "directory not found".
    up = (GCP / "vm" / "sc-up.sh").read_text()
    assert "chmod 0755 /etc/sc/keycloak-import" in up
    assert "chmod 0644 /etc/sc/keycloak-import/screening-closure-realm.json" in up


def test_sized_for_the_4gb_e2_medium():
    import re

    lib = (GCP / "lib.sh").read_text()
    assert ': "${SC_MACHINE_TYPE:=e2-medium}"' in lib and ': "${SC_QUANT:=q5_k_m}"' in lib
    data = yaml.load((GCP / "compose.gcp.yml").read_text(), Loader=cl._Loader)
    mb = lambda name: int(re.fullmatch(r"(\d+)m", data["services"][name]["mem_limit"]).group(1))  # noqa: E731
    assert (mb("api"), mb("fhir"), mb("keycloak-prod"), mb("triage-llama")) == (1024, 1300, 700, 1500)
    assert "-Xmx${FHIR_HEAP_MB:-768}m" in data["services"]["fhir"]["environment"]["JAVA_TOOL_OPTIONS"]
    assert "-Xmx384m" in data["services"]["keycloak-prod"]["environment"]["JAVA_OPTS_KC_HEAP"]
    # each heap leaves room for the JVM's own overhead inside its container limit, and the limits together fit 4 GB with Postgres and the OS
    assert 768 + 400 <= mb("fhir") and 384 + 250 <= mb("keycloak-prod")
    startup = (GCP / "vm" / "startup.sh").read_text()
    assert "fallocate -l 4G /swapfile" in startup  # 4 GB swap, grown from an older 2 GB file


def test_llama_healthcheck_uses_the_port_it_listens_on():
    data = yaml.load((GCP / "compose.gcp.yml").read_text(), Loader=cl._Loader)
    llama = data["services"]["triage-llama"]
    port = llama["command"][llama["command"].index("--port") + 1]
    assert port == "8081" and f"localhost:{port}/health" in " ".join(llama["healthcheck"]["test"])


# ---- BUG-77: the gated API restart (deploy/gcp/vm/sc-api-restart.sh) and its installation -----------------------------------------
# No Docker and no systemd: a fake `docker` on PATH records the calls, a fake cgroup tree and /proc/meminfo give the readings.

MIB = 1024 * 1024


def _run_restart(tmp_path, mode, *, anon_mib=None, stats="", limit=1024 * MIB, last=None, extra_env=None):
    import os
    import stat

    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    calls = tmp_path / "calls"
    calls.write_text("")
    fake = bindir / "docker"
    fake.write_text(
        f"""#!/bin/bash
echo "$*" >> {calls}
case "$1 $2" in
  "compose ps") echo abc123 ;;
  "compose restart") ;;
  "inspect -f") echo {limit} ;;
  "stats --no-stream") echo "{stats}" ;;
esac
"""
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    cg = tmp_path / "cg"
    scope = cg / "system.slice" / "docker-abc123.scope"
    scope.mkdir(parents=True, exist_ok=True)
    if anon_mib is not None:
        (scope / "memory.stat").write_text(f"file 99999999\nanon {int(anon_mib * MIB)}\n")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        8000000 kB\n")
    state = tmp_path / "last"
    if last is not None:
        state.write_text(str(last))
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "SC_REPO": str(tmp_path),
        "SC_ENV_SH": str(tmp_path / "absent.sh"),
        "SC_CGROUP_ROOT": str(cg),
        "SC_MEMINFO": str(meminfo),
        "SC_RESTART_LOG": str(tmp_path / "restart.log"),
        "SC_RESTART_STATE": str(state),
        **(extra_env or {}),
    }
    r = subprocess.run(["bash", str(GCP / "vm/sc-api-restart.sh"), mode], capture_output=True, text=True, env=env, timeout=30)
    log = (tmp_path / "restart.log").read_text() if (tmp_path / "restart.log").exists() else ""
    return r, "compose restart api" in calls.read_text(), log


def test_api_restart_below_the_gate_restarts_nothing(tmp_path):
    r, restarted, log = _run_restart(tmp_path, "gate", anon_mib=500)  # 48.8% of 1024 MiB
    assert r.returncode == 0 and not restarted and log == ""
    assert "no restart" in r.stdout


def test_api_restart_above_the_gate_restarts_only_api_and_logs_the_reason(tmp_path):
    r, restarted, log = _run_restart(tmp_path, "gate", anon_mib=760)  # 74%
    assert r.returncode == 0 and restarted
    assert "RESTARTED" in log and "memory 74% >= gate 70%" in log and "cgroup anon" in log
    calls = (tmp_path / "calls").read_text()
    assert "compose restart api" in calls and "down" not in calls and "restart postgres" not in calls


def test_api_restart_daily_fallback_uses_the_lower_threshold(tmp_path):
    r, restarted, log = _run_restart(tmp_path, "daily", anon_mib=450)  # 44%: over 40, under 70
    assert restarted and "daily fallback" in log
    (tmp_path / "g").mkdir()
    r, restarted, _ = _run_restart(tmp_path / "g", "gate", anon_mib=450)
    assert not restarted
    (tmp_path / "g2").mkdir()
    r, restarted, _ = _run_restart(tmp_path / "g2", "daily", anon_mib=300)  # 29%: under both
    assert not restarted


def test_api_restart_loop_guard_and_check_mode(tmp_path):
    import time

    r, restarted, log = _run_restart(tmp_path, "gate", anon_mib=900, last=int(time.time()) - 600)
    assert r.returncode == 0 and not restarted and "WANTED restart" in log and "not restarting" in log
    (tmp_path / "c").mkdir()
    r, restarted, _ = _run_restart(tmp_path / "c", "check", anon_mib=900)
    assert r.returncode == 0 and not restarted and "87%" in r.stdout
    (tmp_path / "u").mkdir()
    assert _run_restart(tmp_path / "u", "bogus")[0].returncode == 2


def test_api_restart_falls_back_to_docker_stats_and_to_host_memory(tmp_path):
    r, restarted, log = _run_restart(tmp_path, "gate", stats="760MiB / 1GiB", anon_mib=None)
    assert restarted and "docker stats" in log
    (tmp_path / "h").mkdir()
    # no container limit (0): the limit is the host's MemTotal, 8000000 kB; 6 GiB used is 78%
    r, restarted, log = _run_restart(tmp_path / "h", "gate", anon_mib=6 * 1024, limit=0)
    assert restarted and "host MemTotal" in log


def test_api_restart_units_and_installation():
    vm = GCP / "vm"
    timer = (vm / "sc-api-restart.timer").read_text()
    assert "OnUnitActiveSec=15min" in timer and "OnCalendar" not in timer  # no blind clock-time restart
    assert "sc-api-restart.sh gate" in (vm / "sc-api-restart.service").read_text()
    assert "sc-api-restart.sh daily" in (vm / "sc-api-restart-daily.service").read_text()
    assert "OnCalendar" in (vm / "sc-api-restart-daily.timer").read_text()
    # installed on every run of sc-up.sh (boot and deploy), all four units, enabled; startup.sh no longer carries a conditional copy
    up = (vm / "sc-up.sh").read_text()
    for unit in ("sc-api-restart.service", "sc-api-restart.timer", "sc-api-restart-daily.service", "sc-api-restart-daily.timer"):
        assert unit in up
    assert "systemctl enable --now sc-api-restart.timer sc-api-restart-daily.timer" in up
    assert "sc-api-restart" not in "\n".join(l for l in (vm / "startup.sh").read_text().splitlines() if not l.lstrip().startswith("#"))
    # a re-run of deploy.sh refreshes the startup-script metadata on an existing VM
    deploy = (GCP / "deploy.sh").read_text()
    assert deploy.count("startup-script=$here/vm/startup.sh") == 2
    # the gate needs a limit to be a percentage of
    api = yaml.safe_load((GCP / "compose.gcp.yml").read_text())["services"]["api"]
    assert api["mem_limit"] == "1024m"


def test_demo_reset_watcher_units_are_installed_by_sc_up():
    """ADR-058 wiring: both reset units are installed and enabled by sc-up.sh, /var/lib/sc-demo is owned by uid 10001, no cron."""
    vm = GCP / "vm"
    up = (vm / "sc-up.sh").read_text()
    for unit in ("sc-demo-reset-watch.service", "sc-demo-reset-watch.timer", "sc-demo-reset-scheduled.service", "sc-demo-reset-scheduled.timer"):
        assert (vm / unit).exists() and unit in up
    assert "systemctl enable --now sc-demo-reset-watch.timer sc-demo-reset-scheduled.timer" in up
    assert "chown 10001 /var/lib/sc-demo" in up
    assert "demo-reset-watch.sh --scheduled" in (vm / "sc-demo-reset-scheduled.service").read_text()
    assert "demo-reset-watch.sh" in (vm / "sc-demo-reset-watch.service").read_text()
    assert "OnUnitActiveSec=15" in (vm / "sc-demo-reset-watch.timer").read_text()
    # judging window (S3): once a day at 03:00 Asia/Dubai = 23:00 UTC, never caught up mid-day, no boot-time or interval reset
    sched = (vm / "sc-demo-reset-scheduled.timer").read_text()
    live = "\n".join(l for l in sched.splitlines() if not l.lstrip().startswith("#"))
    assert "OnCalendar=*-*-* 23:00:00 UTC" in live  # 23:00 UTC == 03:00 Asia/Dubai (UTC+4, no DST)
    assert "Persistent=false" in live and "Persistent=true" not in live
    assert "OnBootSec" not in live and "OnUnitActiveSec" not in live
    assert "6h" not in live
    assert "Asia/Dubai" in sched and "03:00" in sched
    # the request-based reset keeps its 15 s poll; sc-up.sh reloads systemd and restarts both timers so a changed timer takes effect
    assert up.index("daemon-reload", up.index("sc-demo-reset-scheduled.timer")) < up.index("try-restart sc-demo-reset-watch.timer sc-demo-reset-scheduled.timer")
    # the web's "next scheduled reset" line follows the same clock time
    env_py = (vm / "render_env.py").read_text()
    assert 'api["DEMO_AUTO_RESET_HOURS"] = "24"' in env_py and 'api["DEMO_AUTO_RESET_AT_UTC"] = "23:00"' in env_py
    # no cron, and startup.sh does not carry the units
    assert "cron" not in up.lower()
    assert "sc-demo-reset" not in "\n".join(l for l in (vm / "startup.sh").read_text().splitlines() if not l.lstrip().startswith("#"))
    # the folder is bind-mounted into the api container
    api = yaml.safe_load((GCP / "compose.gcp.yml").read_text())["services"]["api"]
    assert "/var/lib/sc-demo:/var/lib/sc-demo" in api["volumes"] and api["mem_limit"] == "1024m"


def test_compose_env_carries_app_env_so_the_base_compose_default_cannot_win():
    """docker-compose.yml has `APP_ENV: ${APP_ENV:-dev}` in the api's environment, which overrides api.env; the
    interpolation file must therefore carry the mode too (regression: the hosted API ran as dev, 6 Nov 2026)."""
    import re
    base = (REPO_ROOT / "docker-compose.yml").read_text()
    assert re.search(r"APP_ENV: \$\{APP_ENV:-dev\}", base)
    prod = render_env.build_files("demo.example", SECRETS, mode="production")
    seed = render_env.build_files("demo.example", SECRETS, mode="seed")
    assert prod["compose.env"]["APP_ENV"] == "production" == prod["api.env"]["APP_ENV"]
    assert seed["compose.env"]["APP_ENV"] == "dev"


def test_hosted_seed_stages_the_rich_opening_state_in_the_right_order():
    """The hosted demo opens staged (simulator/stage_demo_state.py): the overdue case is created BEFORE the cohort's cases (the clock
    moves once, so none of the others is overdue), the draft, sign-off and closed cases come after the chronic cases, and all of it
    is before the snapshot that 'reset demo' restores. Hosted only: the local video demo's seed does not run it."""
    script = (GCP / "vm" / "seed-on-vm.sh").read_text()
    code = "\n".join(l for l in script.splitlines() if not l.lstrip().startswith("#"))
    order = ["simulator/generate_results.py", "stage_demo_state.py --phase overdue", "simulator/create_cases.py",
             "simulator/create_chronic_cases.py", "stage_demo_state.py --phase rest", "tools/demo/snapshot.sh save"]
    at = [code.index(x) for x in order]
    assert at == sorted(at)
    assert code.index("stage_demo_state.py --phase rest") < code.index("echo production > /var/lib/sc/mode")
    assert "stage_demo_state" not in (REPO_ROOT / "Makefile").read_text().split("\ndemo-seed:\n", 1)[1].split("\n\n", 1)[0]

def test_hosted_seed_fails_loudly_when_the_staging_fails():
    """A stager that exits non-zero (the PR #67 sign-off 403) must end seed-on-vm.sh non-zero, before the snapshot and before the
    switch to production: -e on the script, -e inside the seeding container (one command per line), an explicit check on docker run."""
    import re
    script = (GCP / "vm" / "seed-on-vm.sh").read_text()
    code = "\n".join(l for l in script.splitlines() if not l.lstrip().startswith("#"))
    assert re.search(r"^set -[A-Za-z]*e[A-Za-z]*u[A-Za-z]* pipefail$", code, re.M)
    assert "sh -ec '" in code and "sh -c " not in code  # not a bare `sh -c` chain
    block = code[code.index("docker run --rm --network host"):code.index("ref=$(docker compose")]
    assert "if ! docker run" in block and "exit 1" in block
    for step in ("load_cohort.py", "generate_results.py", "stage_demo_state.py --phase overdue", "create_cases.py",
                 "create_chronic_cases.py", "stage_demo_state.py --phase rest"):
        assert re.search(rf"^\s+python simulator/{re.escape(step)}.*$", block, re.M), step  # each on its own line, no `|| true` after it
    assert "|| true" not in block and "&&" not in block
    assert code.index("exit 1", code.index("if ! docker run")) < code.index("tools/demo/snapshot.sh save") < code.index("echo production > /var/lib/sc/mode")


def test_snapshot_script_is_posix_sh_because_the_vm_runs_it_under_dash():
    """`tools/demo/snapshot.sh` has a /bin/sh shebang and is run directly on the VM (Debian: dash): no bashisms such as ${@:2}
    ("45: Bad substitution" in the hosted reset)."""
    import re
    import shutil

    snap = REPO_ROOT / "tools/demo/snapshot.sh"
    text = snap.read_text()
    assert text.startswith("#!/bin/sh")
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"\$\{[A-Za-z0-9_@*]+(:[0-9@]|/|\^|,)", code) and "[[" not in code and "<<<" not in code and "local " not in code
    for sh in ("dash", "sh"):
        if shutil.which(sh):
            assert subprocess.run([sh, "-n", str(snap)]).returncode == 0
    if shutil.which("dash"):  # psql_db keeps the db name and passes the rest through, as under dash
        out = subprocess.run(["dash", "-c", 'f() { _db=$1; shift; echo "$_db|$*"; }; f closure -c "SELECT 1"'], capture_output=True, text=True)
        assert out.stdout.strip() == "closure|-c SELECT 1"


def test_smoke_test_checks_docs_are_closed_and_the_reset_is_recorded():
    t = (GCP / "smoke-test.sh").read_text()
    for must in ('"$BASE/api/docs"', '"$BASE/api/openapi.json"', "demo.last_reset_at"):
        assert must in t, must
    assert "404" in t.split('"$BASE/api/docs"')[0].splitlines()[-1]
    assert "--record-seed" in (GCP / "vm/seed-on-vm.sh").read_text()


def test_api_restart_leaves_a_note_for_the_demo_health_block(tmp_path):
    import json as _json

    ctl = tmp_path / "ctl"
    ctl.mkdir()
    r, restarted, _ = _run_restart(tmp_path, "gate", anon_mib=760, extra_env={"DEMO_CONTROL_DIR": str(ctl)})
    assert r.returncode == 0 and restarted
    note = _json.loads((ctl / "api-restart.json").read_text())
    assert note["mode"] == "gate" and "memory 74% >= gate 70%" in note["reason"] and note["at"].endswith("+00:00")
    # no restart, no note; and a missing control folder never fails the restart
    ctl2 = tmp_path / "ctl2"
    ctl2.mkdir()
    (tmp_path / "a").mkdir()
    r, restarted, _ = _run_restart(tmp_path / "a", "gate", anon_mib=500, extra_env={"DEMO_CONTROL_DIR": str(ctl2)})
    assert not restarted and not (ctl2 / "api-restart.json").exists()
    (tmp_path / "b").mkdir()
    r, restarted, _ = _run_restart(tmp_path / "b", "gate", anon_mib=760, extra_env={"DEMO_CONTROL_DIR": str(tmp_path / "nope")})
    assert r.returncode == 0 and restarted
