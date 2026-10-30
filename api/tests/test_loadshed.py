"""M7-8 (ADR-048): load shedding. Over the in-flight limit a request gets 503 + Retry-After at once, requests already running finish,
the exempt paths (probe, scheduler, watchdog, metrics) are never refused, and 0 switches it off."""

import asyncio

import pytest

from app.loadshed import LoadShedMiddleware


def scope(path, method="GET"):
    return {"type": "http", "path": path, "method": method, "headers": []}


class Recorder:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)

    @property
    def status(self):
        return self.messages[0]["status"]

    @property
    def headers(self):
        return dict(self.messages[0]["headers"])


def run(coro):
    return asyncio.run(coro)


async def hold_app(gate, started):
    async def app(scope, receive, send):
        started.append(scope["path"])
        await gate.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    return app


def test_the_request_over_the_limit_is_refused_at_once_with_retry_after_and_the_ones_running_finish():
    async def go():
        gate, started = asyncio.Event(), []
        mw = LoadShedMiddleware(await hold_app(gate, started), max_inflight=2, retry_after=3)
        outs = [Recorder() for _ in range(4)]
        tasks = [asyncio.create_task(mw(scope(f"/cases/{i}"), None, outs[i])) for i in range(4)]
        await asyncio.sleep(0.05)
        assert len(started) == 2 and mw.inflight == 2  # two are running
        assert outs[2].status == 503 and outs[3].status == 503  # the other two were refused without waiting
        assert outs[2].headers[b"retry-after"] == b"3" and outs[2].headers[b"x-shed"] == b"1"
        assert b"overloaded" in outs[2].messages[1]["body"]
        gate.set()
        await asyncio.gather(*tasks)
        assert outs[0].status == 200 and outs[1].status == 200 and mw.inflight == 0 and mw.shed == 2 and mw.peak == 2
        # and a slot is free again
        out = Recorder()
        await mw(scope("/cases/9"), None, out)
        assert out.status == 200
    run(go())


@pytest.mark.parametrize("path", ["/health", "/scheduler/lease", "/scheduler/heartbeat", "/scheduler/sweep", "/watchdog/scan", "/metrics"])
def test_probes_service_calls_and_the_metrics_scrape_are_never_refused(path):
    async def go():
        gate, started = asyncio.Event(), []
        mw = LoadShedMiddleware(await hold_app(gate, started), max_inflight=1)
        first = asyncio.create_task(mw(scope("/cases"), None, Recorder()))
        await asyncio.sleep(0.02)
        out = Recorder()
        t = asyncio.create_task(mw(scope(path), None, out))
        await asyncio.sleep(0.02)
        assert path in started  # it ran although the pod is at its limit
        gate.set()
        await asyncio.gather(first, t)
        assert out.status == 200
    run(go())


def test_zero_switches_it_off_and_non_http_scopes_pass_through():
    async def go():
        gate, started = asyncio.Event(), []
        mw = LoadShedMiddleware(await hold_app(gate, started), max_inflight=0)
        outs = [Recorder() for _ in range(5)]
        tasks = [asyncio.create_task(mw(scope("/cases"), None, o)) for o in outs]
        await asyncio.sleep(0.02)
        assert len(started) == 5
        gate.set()
        await asyncio.gather(*tasks)
        assert all(o.status == 200 for o in outs)
        seen = []

        async def app(scope, receive, send):
            seen.append(scope["type"])

        await LoadShedMiddleware(app, max_inflight=1)({"type": "lifespan"}, None, None)
        assert seen == ["lifespan"]
    run(go())


def test_it_is_off_by_default_in_the_app_and_a_real_fastapi_app_answers_503_over_the_limit():
    """The code default is off (dev and the tests); the chart sets the limit. A small FastAPI app with the middleware and a slow
    sync route (the thread pool, as the API runs them) answers 503 + Retry-After to the request over the limit."""
    import threading
    import time

    import httpx
    from fastapi import FastAPI

    from app import config, main

    assert config.settings.max_inflight_requests == 0 and not any(m.cls is LoadShedMiddleware for m in main.app.user_middleware)

    release = threading.Event()
    entered = threading.Semaphore(0)
    small = FastAPI()
    small.add_middleware(LoadShedMiddleware, max_inflight=2, retry_after=2)

    @small.get("/slow")
    def slow():
        entered.release()
        release.wait(10)
        return {"ok": True}

    @small.get("/health")
    def health():
        return {"ok": True}

    async def go():
        transport = httpx.ASGITransport(app=small)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            slow_calls = [asyncio.create_task(c.get("/slow")) for _ in range(2)]
            for _ in range(2):
                assert await asyncio.to_thread(entered.acquire, True, 10)
            over = await c.get("/slow")
            assert over.status_code == 503 and over.headers["retry-after"] == "2"
            assert (await c.get("/health")).status_code == 200  # the probe still answers at the limit
            release.set()
            assert [r.status_code for r in await asyncio.gather(*slow_calls)] == [200, 200]
    run(go())
    time.sleep(0)
