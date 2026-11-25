"""Health probes still answer while ordinary requests are busy."""

import asyncio
import os
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import httpx
from anyio import to_thread
from anyio.from_thread import start_blocking_portal

from app import main, prerender

app = main.app


class HealthChecksTest(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        # Leave the main thread's event loop alone for the scheduler tests.
        self.portal = stack.enter_context(start_blocking_portal())

    async def check_health(self, client):
        for path in ("/healthz", "/healthz/ready"):
            response = await asyncio.wait_for(client.get(path), timeout=0.5)
            self.assertEqual(response.status_code, 200, response.text)

    def test_health_during_slow_company_render(self):
        self.portal.call(self.check_slow_company_render)

    async def check_slow_company_render(self):
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        inject = prerender.inject

        def slow_inject(*args, **kwargs):
            loop.call_soon_threadsafe(started.set)
            release.wait(2)
            return inject(*args, **kwargs)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://testserver") as client:
            with patch.object(prerender, "inject", slow_inject):
                render = asyncio.create_task(client.get("/company.html?code=7203"))
                try:
                    await asyncio.wait_for(started.wait(), timeout=3)
                    await self.check_health(client)
                    self.assertFalse(render.done())
                finally:
                    release.set()
                    response = await render
        self.assertEqual(response.status_code, 200)

    def test_health_when_request_workers_are_busy(self):
        self.portal.call(self.check_busy_request_workers)

    async def check_busy_request_workers(self):
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        limiter = to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens

        def occupy_worker():
            loop.call_soon_threadsafe(started.set)
            release.wait(2)

        limiter.total_tokens = 1
        worker = asyncio.create_task(to_thread.run_sync(occupy_worker))
        try:
            await asyncio.wait_for(started.wait(), timeout=3)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://testserver") as client:
                await self.check_health(client)
        finally:
            release.set()
            await worker
            limiter.total_tokens = previous

    def test_readiness_holds_unreleased_and_draining_copies(self):
        self.portal.call(self.check_readiness_gate)

    async def check_readiness_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            released = Path(directory) / "released"
            draining = Path(directory) / "draining"
            with patch.dict(os.environ, {"READY_GATE": "1"}), \
                    patch.object(main, "RELEASE_FLAG", released), \
                    patch.object(main, "DRAIN_FLAG", draining):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://testserver") as client:
                    held = await client.get("/healthz/ready")
                    self.assertEqual((held.status_code, held.text), (503, "not released yet\n"))
                    released.touch()
                    await self.check_health(client)
                    draining.touch()
                    drained = await client.get("/healthz/ready")
                    self.assertEqual((drained.status_code, drained.text), (503, "draining\n"))
