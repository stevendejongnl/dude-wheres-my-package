"""Retry helper for carrier HTTP calls.

Wraps an async callable with exponential-backoff retries for transient
errors (timeouts, connect failures, 429/5xx). Raises CarrierTransientError
once all attempts are exhausted.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

import httpx

from dwmp.carriers.base import CarrierTransientError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 502, 503, 504}
_RETRY_AFTER_CAP = 60.0


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.ConnectError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


def _retry_after(exc: BaseException) -> float:
    if isinstance(exc, httpx.HTTPStatusError):
        header = exc.response.headers.get("retry-after", "")
        if header.isdigit():
            return min(float(header), _RETRY_AFTER_CAP)
    return 0.0


async def with_retries[T](
    fn: Callable[[], Awaitable[T]],
    *,
    carrier: str,
    attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            return await fn()
        except BaseException as exc:
            if not _is_retryable(exc):
                raise
            last_exc = exc
            explicit = _retry_after(exc)
            if explicit:
                delay = explicit
            else:
                delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            logger.info(
                "%s: transient error on attempt %d/%d (%s), retrying in %.1fs",
                carrier, attempt + 1, attempts, exc, delay,
            )
            await asyncio.sleep(delay)

    raise CarrierTransientError(
        carrier,
        f"Failed after {attempts} attempts: {last_exc}",
    ) from last_exc
