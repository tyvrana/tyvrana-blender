import asyncio
import gc
import threading
from collections.abc import AsyncIterator

import pytest


@pytest.fixture(autouse=True)
async def no_leaks() -> AsyncIterator[None]:
    loop = asyncio.get_running_loop()
    baseline = asyncio.all_tasks()
    threads = set(threading.enumerate())
    errors: list[dict[str, object]] = []
    handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    try:
        yield
        gc.collect()
        await asyncio.sleep(0)
        leaked = asyncio.all_tasks() - baseline - {asyncio.current_task()}
        for task in leaked:
            task.cancel()
        if leaked:
            await asyncio.gather(*leaked, return_exceptions=True)
        assert not leaked
        assert not errors
        assert set(threading.enumerate()) <= threads
    finally:
        loop.set_exception_handler(handler)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--headless-only",
        action="store_true",
        help="Skip tests requiring an interactive Blender host",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "interactive: requires an interactive Blender event loop"
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not config.getoption("--headless-only"):
        return
    for item in items:
        parameters = getattr(getattr(item, "callspec", None), "params", {})
        if parameters.get("ui") is True or item.get_closest_marker("interactive"):
            item.add_marker(
                pytest.mark.skip(
                    reason="Interactive Blender excluded by --headless-only"
                )
            )
