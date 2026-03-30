from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict


class ResilientClientError(RuntimeError):
    pass


@dataclass
class CircuitState:
    failures: int = 0
    opened_until: float = 0.0


class ResilientCaller:
    def __init__(self, name: str, *, max_retries: int = 2, open_after_failures: int = 3, reset_after_seconds: float = 30.0) -> None:
        self.name = name
        self.max_retries = max_retries
        self.open_after_failures = open_after_failures
        self.reset_after_seconds = reset_after_seconds
        self._state = CircuitState()

    def call(self, func: Callable[[], Any]) -> Any:
        now = time.time()
        if self._state.opened_until > now:
            raise ResilientClientError(f"{self.name} circuit is open")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                result = func()
                self._state.failures = 0
                self._state.opened_until = 0.0
                return result
            except Exception as exc:
                last_error = exc
                self._state.failures += 1
                if self._state.failures >= self.open_after_failures:
                    self._state.opened_until = time.time() + self.reset_after_seconds
                if attempt >= self.max_retries:
                    break
                time.sleep(0.25 * (2**attempt))
        raise ResilientClientError(str(last_error or f"{self.name} request failed"))

    async def call_async(self, func: Callable[[], Any]) -> Any:
        return await asyncio.to_thread(self.call, func)


_CALLERS: Dict[str, ResilientCaller] = {}


def get_resilient_caller(name: str, *, max_retries: int = 2, open_after_failures: int = 3, reset_after_seconds: float = 30.0) -> ResilientCaller:
    caller = _CALLERS.get(name)
    if caller is None:
        caller = ResilientCaller(
            name,
            max_retries=max_retries,
            open_after_failures=open_after_failures,
            reset_after_seconds=reset_after_seconds,
        )
        _CALLERS[name] = caller
    return caller
