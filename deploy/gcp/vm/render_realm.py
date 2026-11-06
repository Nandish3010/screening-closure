#!/usr/bin/env python3
"""Render the Keycloak realm for the hosted demo from the committed realm (config/keycloak/screening-closure-realm.json).

Changes, all in memory the web client's redirect URIs and web origins become the public host only (no localhost), brute-force
protection goes on (short, temporary lockouts), and the `sslRequired` stays `external`. ADR-060 adds a second client, `screening-closure-demo`, with direct access
grants, used only by the API's one-click demo sign-in over the private network; the committed realm and the web client never allow
direct grants. Passwords stay as the committed `${KC_PW_*}` placeholders, which
Keycloak fills from its environment at import. Usage: render_realm.py HOST SRC.json DST.json
"""

import copy
import json
import sys

DEMO_CLIENT_ID = "screening-closure-demo"
# Brute-force protection for the public Keycloak login page: ~10 bad passwords lock a user, but only briefly and never permanently, so
# a stranger guessing at a public demo username cannot take a role away from the judges for more than a minute. ensure-demo-client.sh
# applies the same values to a live realm (an existing realm is not touched by the import).
BRUTE_FORCE = {"bruteForceProtected": True, "failureFactor": 10, "permanentLockout": False, "waitIncrementSeconds": 30,
               "maxFailureWaitSeconds": 60, "minimumQuickLoginWaitSeconds": 30, "quickLoginCheckMilliSeconds": 1000,
               "maxDeltaTimeSeconds": 600}
SECRET_PLACEHOLDER = "${KC_DEMO_CLIENT_SECRET}"  # filled by Keycloak from its environment at import, like the user passwords


def demo_client(realm: dict) -> dict:
    """The web client's token shape (audience, name, provider, patient_ref claims) with the password grant instead of the browser flow.
    A CONFIDENTIAL client (client secret, held only by the API) with no redirect URIs and no web origins: the token endpoint is
    reachable through the edge, but without the secret nobody else can use this client for password grants, so the public cannot
    guess demo passwords through it and lock a role (ADR-060 addendum). The only caller that holds the secret and the demo
    passwords is the API."""
    web = next(c for c in realm["clients"] if c["clientId"] == "screening-closure-web")
    c = copy.deepcopy(web)
    c.update(clientId=DEMO_CLIENT_ID, name="Screening Closure hosted-demo one-click sign-in (API only)",
             publicClient=False, clientAuthenticatorType="client-secret", secret=SECRET_PLACEHOLDER, standardFlowEnabled=False, implicitFlowEnabled=False, directAccessGrantsEnabled=True,
             serviceAccountsEnabled=False, redirectUris=[], webOrigins=[])
    c.pop("attributes", None)
    return c


def render(realm: dict, host: str) -> dict:
    for c in realm["clients"]:
        if c["clientId"] == "screening-closure-web":
            c["redirectUris"] = [f"https://{host}/*"]
            c["webOrigins"] = [f"https://{host}"]
    realm["clients"] = [c for c in realm["clients"] if c["clientId"] != DEMO_CLIENT_ID] + [demo_client(realm)]
    realm.update(BRUTE_FORCE)
    realm["sslRequired"] = "external"
    return realm


if __name__ == "__main__":
    host, src, dst = sys.argv[1:4]
    with open(src, encoding="utf-8") as fh:
        out = render(json.load(fh), host)
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
