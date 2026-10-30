"""Mint an OIDC access token the kind-deployed API accepts (RS256, signed with the Secret sc-oidc-signing).

    JWT=$(tools/k8s/mint_token.py --role coordinator --name loadgen --ttl 7200)
    curl -H "Authorization: Bearer $JWT" http://127.0.0.1:18376/cases

Roles: lab, primary_care_owner, specialist_owner, coordinator, patient (needs --patient-ref Patient/<id>), supervisor.
Reads the private key with kubectl (context kind-sc-m76); prints ONLY the token. The token is a credential for this throw-away
cluster; do not log it. Needs PyJWT[crypto] (the repository .venv has it).
"""
import argparse
import base64
import subprocess
import sys
import time
import uuid

import jwt

ISSUER = "https://idp.kind.invalid/realms/sc-m76"   # must match app.oidcIssuer in values-kind.yaml
AUDIENCE = "screening-closure"
KID = "sc-m76-1"


def private_key(context: str, namespace: str) -> str:
    out = subprocess.run(
        ["kubectl", "--context", context, "-n", namespace, "get", "secret", "sc-oidc-signing", "-o", "jsonpath={.data.private\\.pem}"],
        check=True, capture_output=True, text=True,
    ).stdout
    return base64.b64decode(out).decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="coordinator")
    ap.add_argument("--name", default="loadgen")
    ap.add_argument("--sub", default=None, help="stable subject (default: derived from the name)")
    ap.add_argument("--ttl", type=int, default=3600, help="seconds")
    ap.add_argument("--patient-ref", default=None)
    ap.add_argument("--provider", default=None)
    ap.add_argument("--context", default="kind-sc-m76")
    ap.add_argument("--namespace", default="closure")
    a = ap.parse_args()
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": a.sub or str(uuid.uuid5(uuid.NAMESPACE_URL, "sc-m76/" + a.name)),
        "iat": now, "nbf": now - 5, "exp": now + a.ttl, "name": a.name,
        "realm_access": {"roles": [a.role]},
    }
    if a.patient_ref:
        claims["patient_ref"] = a.patient_ref
    if a.provider:
        claims["provider"] = a.provider
    sys.stdout.write(jwt.encode(claims, private_key(a.context, a.namespace), algorithm="RS256", headers={"kid": KID}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
