"""Save work has a channel of its own: it runs even when the shared thread pool is entirely taken."""

import asyncio
import threading

from anyio import to_thread

from handler import saves


def test_a_save_runs_while_the_shared_pool_is_exhausted():
    async def scenario() -> str:
        limiter = to_thread.current_default_thread_limiter()
        release = threading.Event()
        for _ in range(int(limiter.total_tokens)):
            asyncio.get_running_loop().create_task(to_thread.run_sync(release.wait))
        await asyncio.sleep(0.05)
        assert limiter.available_tokens == 0
        try:
            return await asyncio.wait_for(saves.run_on_saves_channel(lambda: "stored"), timeout=2)
        finally:
            release.set()

    assert asyncio.run(scenario()) == "stored"
