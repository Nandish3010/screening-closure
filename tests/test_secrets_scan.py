"""T-F12: secrets out of the repo. Token-shaped fixtures are assembled at runtime so that this
file itself stays clean under the scanner it tests."""

from __future__ import annotations

import secrets_scan as ss

from conftest import REPO_ROOT

# fake secrets, built from pieces so no literal token appears in this source file
AWS_KEY = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
GH_TOKEN = "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
OPENAI_KEY = "sk" + "-proj-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg"
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
AZURE_STORAGE = "Account" + "Key=" + "a" * 20 + "B" * 20 + "c" * 8 + "=="
PASSWORD_LINE = 'DB_PASSWORD = "' + "Tr0ub4dor&3xyz" + '"'


def rules(findings):
    return {f.rule for f in findings}


def scan(repo, **kw):
    findings, _ = ss.scan(repo.root, gitleaks=False, **kw)
    return findings


def test_real_repo_is_clean():
    findings, stats = ss.scan(REPO_ROOT, gitleaks=False)
    assert findings == [], "\n".join(map(str, findings))
    assert stats["files"] > 50 and stats["commits"] > 10


def test_real_repo_env_rules():
    assert ss.check_env_hygiene(REPO_ROOT) == []
    assert ss.check_env_example(REPO_ROOT) == []


def test_clean_repo_passes(repo):
    repo.write(".gitignore", ".env\n")
    repo.write(".env.example", "POSTGRES_PASSWORD=closure_dev_password\nAPI_TOKEN=\n# DEV_AUTH_SECRET=change-me-to-a-long-random-string\n")
    repo.write("app.py", "import os\npassword = os.environ['DB_PASSWORD']\nTOKEN_HEADER = 'x-token'\n")
    repo.commit()
    assert scan(repo) == []


def test_token_shapes_in_tree_are_found_and_redacted(repo):
    repo.write(".gitignore", ".env\n")
    repo.write("a.txt", f"key {AWS_KEY}\n")
    repo.write("b.txt", f"gh {GH_TOKEN}\n")
    repo.write("c.txt", f"openai {OPENAI_KEY}\n")
    repo.write("d.pem.txt", f"{PEM}\nabc\n")
    repo.write("e.txt", f"jwt {JWT}\n")
    repo.write("f.txt", f"{AZURE_STORAGE}\n")
    repo.commit()
    findings = scan(repo, history=False)
    assert rules(findings) >= {"aws-access-key-id", "github-token", "openai-key", "private-key", "jwt", "azure-storage-key"}
    text = "\n".join(map(str, findings))
    for secret in (AWS_KEY, GH_TOKEN, OPENAI_KEY, JWT):
        assert secret not in text  # never printed


def test_jwt_fragment_without_three_parts(repo):
    repo.write(".gitignore", ".env\n")
    repo.write("n.md", "token: " + "eyJ" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdefgh\n")
    repo.commit()
    assert "jwt-fragment" in rules(scan(repo, history=False))


def test_password_literal_flagged_in_code_but_not_in_exempt_places(repo):
    repo.write(".gitignore", ".env\n")
    repo.write("app/settings.py", PASSWORD_LINE + "\n")
    repo.commit()
    assert rules(scan(repo, history=False)) == {"credential-literal"}

    clean = repo
    clean.write("app/settings.py", "password = settings.db_password\n")
    clean.write("tests/test_x.py", PASSWORD_LINE + "\n")
    clean.write("api/tests/test_y.py", PASSWORD_LINE + "\n")
    clean.write("web/src/a.test.tsx", 'const password = "Tr0ub4dor&3xyz";\n')
    clean.write("docker-compose.yml", "      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-closure_dev_password}\n")
    clean.write(".env.example", "POSTGRES_PASSWORD=Tr0ub4dor&3xyz\n")  # path-exempt for the literal rule...
    clean.commit()
    assert [f for f in scan(clean, history=False) if f.rule == "credential-literal"] == []
    # ...but the example-file check still insists on placeholders
    assert "env-example-real-value" in rules(scan(clean, history=False))


def test_url_credentials(repo):
    repo.write(".gitignore", ".env\n")
    repo.write("a.py", 'URL = "postgresql://admin:Sup3rS3cretPw@db.internal:5432/x"\n')
    repo.write("b.py", 'URL = "postgresql://admin:${PW}@db:5432/x"\n')
    repo.commit()
    f = [x for x in scan(repo, history=False) if x.rule == "url-credentials"]
    assert len(f) == 1 and f[0].where.startswith("a.py")


def test_allow_marker_silences_a_line(repo):
    repo.write(".gitignore", ".env\n")
    repo.write("a.txt", f"{AWS_KEY}  secrets-scan: allow (documented example)\n")
    repo.commit()
    assert scan(repo) == []


def test_env_must_be_ignored(repo):
    repo.write("a.txt", "x\n")
    repo.commit()
    assert "env-not-ignored" in rules(scan(repo, history=False))


def test_env_tracked_and_in_history(repo):
    repo.write(".gitignore", "")
    repo.write(".env", "POSTGRES_PASSWORD=x\n")
    repo.commit()
    assert "env-tracked" in rules(scan(repo, history=False))
    repo.git("rm", "-q", ".env")
    repo.write(".gitignore", ".env\n")
    repo.commit("remove env")
    got = rules(scan(repo, history=False))
    assert "env-in-history" in got and "env-tracked" not in got and "env-not-ignored" not in got


def test_env_example_is_not_an_env_file(repo):
    repo.write(".gitignore", ".env\n")
    repo.write(".env.example", "A=1\n")
    repo.commit()
    assert scan(repo) == []


def test_secret_removed_but_still_in_history_is_found(repo):
    repo.write(".gitignore", ".env\n")
    repo.write("a.txt", f"{GH_TOKEN}\n")
    repo.commit("oops")
    repo.write("a.txt", "clean\n")
    repo.commit("fix")
    assert scan(repo, history=False) == []
    h = [f for f in scan(repo) if f.rule == "github-token"]
    assert h and h[0].where.startswith("history:")


def test_secret_in_commit_message_is_found(repo):
    repo.write(".gitignore", ".env\n")
    repo.write("a.txt", "x\n")
    repo.commit(f"token is {OPENAI_KEY}")
    assert "openai-key" in rules(scan(repo))


def test_key_files_are_flagged(repo):
    repo.write(".gitignore", ".env\n")
    repo.write("deploy/server.pem", "abc\n")
    repo.commit()
    assert "key-file" in rules(scan(repo, history=False))


def test_cli_exit_codes(repo, capsys):
    repo.write(".gitignore", ".env\n")
    repo.write("a.txt", "fine\n")
    repo.commit()
    assert ss.main(["--root", str(repo.root), "--no-gitleaks"]) == 0
    repo.write("a.txt", f"{AWS_KEY}\n")
    repo.commit()
    assert ss.main(["--root", str(repo.root), "--no-gitleaks"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and AWS_KEY not in out
