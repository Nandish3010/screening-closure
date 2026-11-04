#!/usr/bin/env python3
"""Render the Keycloak realm for the hosted demo from the committed realm (config/keycloak/screening-closure-realm.json).

Changes, all in memory: the web client's redirect URIs and web origins become the public host only (no localhost), brute-force
protection goes on, and the `sslRequired` stays `external`. Passwords stay as the committed `${KC_PW_*}` placeholders, which
Keycloak fills from its environment at import. Usage: render_realm.py HOST SRC.json DST.json
"""

import json
import sys


def render(realm: dict, host: str) -> dict:
    for c in realm["clients"]:
        if c["clientId"] == "screening-closure-web":
            c["redirectUris"] = [f"https://{host}/*"]
            c["webOrigins"] = [f"https://{host}"]
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
