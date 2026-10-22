#!/usr/bin/env python3
"""`make hl7-demo`: send a positive FIT result over HL7 v2 and show the case appear. Needs the stack up (`make up`).

    PYTHONPATH=api python tools/hl7/demo.py [--fresh]

Sends tools/hl7/samples/oru_fit_positive.hl7 to POST /hl7 as the `system:hl7` service, derives the case id the way the
adapter does (uuid5 of the sender and MSH-10), signs in as a dev coordinator and prints the case. Run it twice: the second
ACK says "duplicate" and no second case appears (idempotent on MSH-10). `--fresh` gives the message a new MSH-10, which
creates a NEW case for the same patient (a second positive result, as POST /cases would): use `make demo-reset` afterwards
if the stack holds the demo snapshot. All data is synthetic.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import send  # noqa: E402

from app.hl7.handler import case_id_for  # noqa: E402
from app.hl7.parser import parse  # noqa: E402

SAMPLE = Path(__file__).resolve().parent / "samples" / "oru_fit_positive.hl7"


def api_json(api: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(api + path, method="POST" if body is not None else "GET", headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request, timeout=20) as resp:  # noqa: S310
        return json.loads(resp.read())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fresh", action="store_true", help="new MSH-10: a new case")
    args = ap.parse_args(argv)
    api = os.environ.get("API_URL", send.DEFAULT_API).rstrip("/")
    token = os.environ.get("HL7_SERVICE_TOKEN", send.DEFAULT_TOKEN)
    message = send.load(SAMPLE, args.fresh)
    case_id = case_id_for(parse(message))
    print(f"1. sending {SAMPLE.name} to {api}/hl7 as system:hl7")
    try:
        ack = send.send_http(message, api, token)
    except (OSError, RuntimeError) as exc:
        print(f"   could not send: {exc} (is the stack up? `make up`)", file=sys.stderr)
        return 2
    code, line = send.summary(ack)
    print(f"2. ACK {code}: {line}")
    if code != "AA":
        return 1
    login = api_json(api, "/auth/dev-login", body={"role": "coordinator", "name": "hl7 demo"})
    try:
        case = api_json(api, f"/cases/{case_id}", token=login["token"])
    except urllib.error.HTTPError as exc:
        print(f"   case {case_id} not found (HTTP {exc.code})", file=sys.stderr)
        return 1
    owner = (case.get("owner") or {}).get("display_name") or "unassigned"
    print(f"3. the case is there: {case_id}\n   state={case['state']}  owner={owner}  patient={case.get('patient_ref')}"
          f"  events={len(case['events'])} (first: {case['events'][0]['type']} by {case['events'][0]['actor']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
