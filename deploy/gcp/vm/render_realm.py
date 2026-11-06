#!/usr/bin/env python3
"""Render the Keycloak realm for the hosted demo from the committed realm (config/keycloak/screening-closure-realm.json).

Changes, all in memory the web client's redirect URIs and web origins become the public host only (no localhost), brute-force
protection goes on, and the `sslRequired` stays `external`. ADR-060 adds a second client, `screening-closure-demo`, with direct access
grants, used only by the API's one-click demo sign-in over the private network; the committed realm and the web client never allow
direct grants. Passwords stay as the committed `${KC_PW_*}` placeholders, which
Keycloak fills from its environment at import. Usage: render_realm.py HOST SRC.json DST.json
"""

import copy
import json
import sys

DEMO_CLIENT_ID = "screening-closure-demo"


def demo_client(realm: dict) -> dict:
    """The web client's token shape (audience, name, provider, patient_ref claims) with the password grant instead of the browser flow.
    A public client with no redirect URIs and no web origins, so the browser flows cannot use it. Its token endpoint is Keycloak's
    ordinary one (also reachable through the edge, as the web client's is), where brute-force protection (render() turns it on)
    covers guessing; the only caller that holds the demo passwords is the API (ADR-060)."""
    web = next(c for c in realm["clients"] if c["clientId"] == "screening-closure-web")
    c = copy.deepcopy(web)
    c.update(clientId=DEMO_CLIENT_ID, name="Screening Closure hosted-demo one-click sign-in (API only)",
             publicClient=True, standardFlowEnabled=False, implicitFlowEnabled=False, directAccessGrantsEnabled=True,
             serviceAccountsEnabled=False, redirectUris=[], webOrigins=[])
    c.pop("attributes", None)
    return c


def render(realm: dict, host: str) -> dict:
    for c in realm["clients"]:
        if c["clientId"] == "screening-closure-web":
            c["redirectUris"] = [f"https://{host}/*"]
            c["webOrigins"] = [f"https://{host}"]
    realm["clients"] = [c for c in realm["clients"] if c["clientId"] != DEMO_CLIENT_ID] + [demo_client(realm)]
    realm["bruteForceProtected"] = True
    realm["failureFactor"] = 10
    realm["sslRequired"] = "external"
    return realm


if __name__ == "__main__":
    host, src, dst = sys.argv[1:4]
    with open(src, encoding="utf-8") as fh:
        out = render(json.load(fh), host)
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
