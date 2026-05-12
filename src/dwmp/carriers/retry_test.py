"""Tests for the carrier retry helper."""

import httpx
import pytest

from dwmp.carriers._retry import with_retries
from dwmp.carriers.base import CarrierTransientError


@pytest.mark.anyio
async def test_succeeds_on_first_attempt():
    call_count = 0

    async def fn():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await with_retries(fn, carrier="test")
    assert result == "ok"
    assert call_count == 1


@pytest.mark.anyio
async def test_retries_on_connect_timeout(monkeypatch):
    async def noop_sleep(_: float) -> None:
        pass
    monkeypatch.setattr("asyncio.sleep", noop_sleep)
    attempts = []

    async def fn():
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectTimeout("timed out")
        return "recovered"

    result = await with_retries(fn, carrier="test", attempts=3, base_delay=0)
    assert result == "recovered"
    assert len(attempts) == 3


@pytest.mark.anyio
async def test_raises_transient_error_after_all_attempts(monkeypatch):
    async def noop_sleep(_: float) -> None:
        pass
    monkeypatch.setattr("asyncio.sleep", noop_sleep)

    async def fn():
        raise httpx.ConnectError("connection refused")

    with pytest.raises(CarrierTransientError) as exc_info:
        await with_retries(fn, carrier="mycarrier", attempts=2, base_delay=0)

    assert exc_info.value.carrier == "mycarrier"
    assert "2 attempts" in str(exc_info.value)


@pytest.mark.anyio
async def test_retries_on_429(monkeypatch):
    async def noop_sleep(_: float) -> None:
        pass
    monkeypatch.setattr("asyncio.sleep", noop_sleep)
    attempts = []

    async def fn():
        attempts.append(1)
        if len(attempts) < 2:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("429", request=request, response=response)
        return "ok"

    result = await with_retries(fn, carrier="test", attempts=3, base_delay=0)
    assert result == "ok"
    assert len(attempts) == 2


@pytest.mark.anyio
async def test_honors_retry_after_header(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    attempts = []

    async def fn():
        attempts.append(1)
        if len(attempts) < 2:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(429, headers={"retry-after": "5"}, request=request)
            raise httpx.HTTPStatusError("429", request=request, response=response)
        return "done"

    await with_retries(fn, carrier="test", attempts=3, base_delay=0)
    assert slept[0] == 5.0


@pytest.mark.anyio
async def test_does_not_retry_non_transient_errors():
    attempts = []

    async def fn():
        attempts.append(1)
        raise ValueError("programmer error")

    with pytest.raises(ValueError):
        await with_retries(fn, carrier="test", attempts=3, base_delay=0)

    assert len(attempts) == 1


@pytest.mark.anyio
async def test_retries_on_503(monkeypatch):
    async def noop_sleep(_: float) -> None:
        pass
    monkeypatch.setattr("asyncio.sleep", noop_sleep)
    attempts = []

    async def fn():
        attempts.append(1)
        if len(attempts) == 1:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("503", request=request, response=response)
        return "ok"

    result = await with_retries(fn, carrier="test", attempts=3, base_delay=0)
    assert result == "ok"
