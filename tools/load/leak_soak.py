#!/usr/bin/env python3
"""BUG-77 / P1-16: a short, scripted memory soak of the API in ONE Linux container, standard library plus httpx and PyJWT only.

The 4 h Kubernetes soak (tools/k8s/soak.py) shows the API pods' anonymous memory growing about 45 MiB/h. This is the same request
mix (tools/k8s/api_load.py: create, event, get, timeline, notifications, worklist) at a constant arrival rate against a
container started from the API image, with the container's own /proc read every `--period` seconds through `docker exec`:
VmRSS, RssAnon (smaps_rollup), the number of threads, the number and size of anonymous read-write mappings, `[heap]`, and
the container's cgroup anonymous memory. Nothing is installed in the container.

    docker run -d --name leak-api -p 127.0.0.1:18077:8000 ... sc-leak-api   # see docs/results/scale/memory_repro/README
    .venv/bin/python tools/load/leak_soak.py --container leak-api --rate 50 --duration 900 --auth oidc --out run.json

`--auth dev` logs in through POST /auth/dev-login; `--auth oidc` mints RS256 tokens with a throw-away key generated here and
serves its JWKS on `--jwks-port` (start the API with AUTH_MODE=oidc, OIDC_ISSUER=<--issuer>, OIDC_JWKS_URL=http://host.docker.internal:<port>/jwks.json),
which is what the cluster soak used. The result is the series and a least-squares slope (MiB/h) of RssAnon over the run, skipping
the first `--skip` seconds (warm-up).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import json
import random
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

MIX = [("create_case", 12), ("post_event", 28), ("get_case_own", 35), ("get_case_seeded", 0), ("timeline", 10), ("notifications", 14), ("worklist", 1)]
EVENT_STEPS = [("accept_ownership", "primary_care_owner"), ("contact_patient", "primary_care_owner"), ("patient_acknowledge", "coordinator")]
ISSUER, AUDIENCE, KID = "https://idp.leak.invalid/realms/leak", "screening-closure", "leak-1"

SNAP_PY = r"""
import json,re
st={}
for ln in open('/proc/1/status'):
    k,_,v=ln.partition(':')
    if k in('VmRSS','VmHWM','Threads'): st[k]=int(v.split()[0])
roll={}
for ln in open('/proc/1/smaps_rollup'):
    k,_,v=ln.partition(':')
    if k in('Rss','Anonymous','Private_Dirty','Pss_Anon'): roll[k]=int(v.split()[0])
anon=[];heap=0;cur=None;n=0;head=re.compile(r'^[0-9a-f]+-[0-9a-f]+ ')
for ln in open('/proc/1/smaps'):
    if head.match(ln):
        p=ln.split(); a,b=(int(x,16) for x in p[0].split('-')); cur={'name':p[5] if len(p)>5 else '','size':(b-a)//1024,'rw':p[1][:2]=='rw'}
    elif ln.startswith('Rss:'):
        r=int(ln.split()[1])
        if cur['name']=='[heap]': heap=r
        elif cur['name']=='' and cur['rw']: anon.append((cur['size'],r))
cg=0
for path in('/sys/fs/cgroup/memory.stat','/sys/fs/cgroup/memory/memory.stat'):
    try:
        for ln in open(path):
            if ln.split()[0] in('anon','rss'): cg=int(ln.split()[1]); break
        break
    except OSError: pass
print(json.dumps({'status':st,'rollup':roll,'heap_kb':heap,'anon_maps':len(anon),'anon_rss_kb':sum(r for _,r in anon),
 'anon_big_maps_ge_1MiB':sum(1 for s,_ in anon if s>=1024),'arena64':sum(1 for s,_ in anon if 60000<=s<=70000),'cgroup_anon_b':cg}))
"""


def snap(container: str) -> dict | None:
    try:
        out = subprocess.run(["docker", "exec", container, "python", "-c", SNAP_PY], capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        return None


def slope_mib_per_h(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 3:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    den = sum((x - mx) ** 2 for x, _ in points)
    return None if den == 0 else sum((x - mx) * (y - my) for x, y in points) / den * 3600.0


def serve_jwks(port: int):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = key.public_key().public_numbers()
    b64 = lambda i: base64.urlsafe_b64encode(i.to_bytes((i.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()  # noqa: E731
    doc = json.dumps({"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID, "n": b64(nums.n), "e": b64(nums.e)}]}).encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(doc)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()), srv


def mint(pem: bytes, role: str, name: str, ttl: int) -> str:
    import jwt

    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, "leak/" + name)), "iat": now, "nbf": now - 5,
              "exp": now + ttl, "name": name, "realm_access": {"roles": [role]}}
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": KID})


async def get_tokens(args, jwks_pem) -> dict[str, str]:
    roles = {"lab": "leak lab", "primary_care_owner": "Dr. Layla Haddad", "coordinator": "leak coordinator"}  # the automation assigns new cases to Dr. Haddad
    if args.auth == "oidc":
        return {r: mint(jwks_pem, r, n, args.duration + 3600) for r, n in roles.items()}
    out = {}
    async with httpx.AsyncClient(base_url=args.url) as c:
        for r, n in roles.items():
            resp = await c.post("/auth/dev-login", json={"role": r, "name": n})
            resp.raise_for_status()
            out[r] = resp.json()["token"]
    return out


class Gen:
    def __init__(self, args, tokens):
        self.a, self.tokens, self.rng = args, tokens, random.Random(args.seed)
        self.run = uuid.uuid4().hex[:6]
        self.own: list[str] = []
        self.advanceable: collections.deque = collections.deque()
        self.n = 0
        self.ops = [k for k, w in MIX for _ in range(w)]
        self.status: collections.Counter = collections.Counter()
        self.clients: list[httpx.AsyncClient] = []
        self.rr = 0

    async def send(self, method, path, role, body=None):
        self.rr += 1
        try:
            r = await self.clients[self.rr % len(self.clients)].request(method, path, json=body, headers={"Authorization": f"Bearer {self.tokens[role]}"})
            self.status[r.status_code] += 1
            return r.status_code
        except Exception as exc:  # noqa: BLE001
            self.status[type(exc).__name__] += 1
            return 0

    async def fire(self):
        op = self.rng.choice(self.ops)
        if op in ("post_event", "get_case_own", "notifications", "timeline") and not self.own:
            op = "create_case"
        if op == "post_event" and not self.advanceable:
            op = "create_case"
        if op == "create_case":
            self.n += 1
            cid = str(uuid.uuid4())
            if await self.send("POST", "/cases", "lab", {"case_id": cid, "patient_ref": f"Patient/load-{self.run}-{self.n}",
                                                         "result_ref": f"DiagnosticReport/load-{self.run}-{self.n}"}) == 201:
                self.own.append(cid)
                self.advanceable.append((cid, 0))
        elif op == "post_event":
            cid, step = self.advanceable.popleft()
            etype, role = EVENT_STEPS[step]
            if await self.send("POST", f"/cases/{cid}/events", role, {"type": etype, "event_id": str(uuid.uuid4())}) == 201 and step + 1 < len(EVENT_STEPS):
                self.advanceable.append((cid, step + 1))
        elif op == "get_case_own":
            await self.send("GET", f"/cases/{self.rng.choice(self.own)}", "coordinator")
        elif op == "timeline":
            await self.send("GET", f"/cases/{self.rng.choice(self.own)}/timeline", "coordinator")
        elif op == "notifications":
            await self.send("GET", f"/cases/{self.rng.choice(self.own)}/notifications", "coordinator")
        elif op == "worklist":
            await self.send("GET", "/cases?state=lost_contact", "coordinator")

    async def run_load(self, duration: float, on_tick=None):
        rate = self.a.rate
        n_clients = max(6, min(200, int(rate * 1.5)))
        self.clients = [httpx.AsyncClient(base_url=self.a.url, timeout=httpx.Timeout(30.0, connect=10.0),
                                          limits=httpx.Limits(max_connections=16, max_keepalive_connections=16, keepalive_expiry=4.0)) for _ in range(n_clients)]
        tasks: set[asyncio.Task] = set()
        t0 = time.perf_counter() + 0.5
        i = 0
        while True:
            now = time.perf_counter()
            if now - t0 >= duration:
                break
            while t0 + i / rate <= now and i / rate < duration:
                tk = asyncio.create_task(self.fire())
                tasks.add(tk)
                tk.add_done_callback(tasks.discard)
                i += 1
            await asyncio.sleep(max(0.0, min(t0 + i / rate - time.perf_counter(), 0.004)))
        if tasks:
            await asyncio.wait(tasks, timeout=45)
        for c in self.clients:
            await c.aclose()
        self.issued = i


async def sampler(container: str, period: float, series: list, t_start: float, stop: asyncio.Event):
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        s = await loop.run_in_executor(None, snap, container)
        if s:
            s["t"] = time.time() - t_start
            series.append(s)
            print(f"t={s['t']:7.0f}s RssAnon={s['rollup'].get('Anonymous', 0) / 1024:7.1f} MiB RSS={s['status'].get('VmRSS', 0) / 1024:7.1f} "
                  f"threads={s['status'].get('Threads')} anon_maps={s['anon_maps']} heap={s['heap_kb'] / 1024:.1f} cg_anon={s['cgroup_anon_b'] / 2**20:.1f}", flush=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=period)
        except asyncio.TimeoutError:
            pass


async def amain(args) -> int:
    pem = srv = None
    if args.auth == "oidc":
        pem, srv = serve_jwks(args.jwks_port)
    tokens = await get_tokens(args, pem)
    gen = Gen(args, tokens)
    series: list[dict] = []
    stop = asyncio.Event()
    t_start = time.time()
    samp = asyncio.create_task(sampler(args.container, args.period, series, t_start, stop))
    await gen.run_load(args.duration)
    stop.set()
    await samp
    pts = [(s["t"], s["rollup"].get("Anonymous", 0) / 1024) for s in series if s["t"] >= args.skip]
    cg = [(s["t"], s["cgroup_anon_b"] / 2**20) for s in series if s["t"] >= args.skip and s["cgroup_anon_b"]]
    res = {"args": vars(args), "issued": gen.issued, "status": {str(k): v for k, v in gen.status.items()},
           "slope_rss_anon_mib_per_h": slope_mib_per_h(pts), "slope_cgroup_anon_mib_per_h": slope_mib_per_h(cg),
           "first": series[0] if series else None, "last": series[-1] if series else None, "series": series}
    print(json.dumps({k: res[k] for k in ("issued", "status", "slope_rss_anon_mib_per_h", "slope_cgroup_anon_mib_per_h")}))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(res, fh, indent=1)
    if srv:
        srv.shutdown()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:18077")
    ap.add_argument("--container", required=True, help="the API container; its PID 1 is the uvicorn process")
    ap.add_argument("--rate", type=float, default=50.0)
    ap.add_argument("--duration", type=float, default=900.0)
    ap.add_argument("--period", type=float, default=30.0)
    ap.add_argument("--skip", type=float, default=120.0, help="seconds of warm-up left out of the slope")
    ap.add_argument("--auth", choices=("dev", "oidc"), default="dev")
    ap.add_argument("--jwks-port", type=int, default=18078)
    ap.add_argument("--seed", type=int, default=7701)
    ap.add_argument("--out", default="")
    return asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
