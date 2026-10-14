"""CRM-TASK-022 tests for the isolated Billing Hub AI client."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.config import settings
from app.services import ai_api_client


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "ai_api_base_url", "https://ai.invalid")
    monkeypatch.setattr(settings, "ai_client_id", "aic_test")
    monkeypatch.setattr(settings, "ai_client_token", "test-token")
    monkeypatch.setattr(settings, "ai_model", "test-model")


def _stub(monkeypatch, response_or_error):
    post = AsyncMock(
        side_effect=response_or_error if isinstance(response_or_error, BaseException) else None,
        return_value=None if isinstance(response_or_error, BaseException) else response_or_error,
    )
    client = MagicMock()
    client.return_value.__aenter__.return_value.post = post
    monkeypatch.setattr(ai_api_client.httpx, "AsyncClient", client)
    return post, client


def _response(status, payload=None):
    response = MagicMock(status_code=status)
    response.json.return_value = payload if payload is not None else {}
    return response


def _invalid_json_response(status=200):
    response = MagicMock(status_code=status)
    response.json.side_effect = json.JSONDecodeError("invalid JSON", "x", 0)
    return response


async def _send():
    return await ai_api_client.send_message(
        feature="crm.test",
        system="system instruction",
        messages=[{"role": "user", "content": "input body"}],
        max_tokens=12,
        schema={"type": "object"},
    )


@pytest.mark.asyncio
async def test_request_shape_headers_and_success_follow_acceptance_criteria(monkeypatch):
    """Guards the request shape, headers, 50-second limit, and successful-result criterion."""
    _configure(monkeypatch)
    post, client = _stub(
        monkeypatch,
        _response(200, {"request_id": "req-1", "stop_reason": "end_turn", "text": '{"answer":1}'}),
    )
    observed_timeouts = []

    async def track_total_timeout(awaitable, timeout):
        observed_timeouts.append(timeout)
        return await awaitable

    monkeypatch.setattr(ai_api_client.asyncio, "wait_for", track_total_timeout)

    result = await _send()

    assert result.outcome == ai_api_client.OK
    assert result.output == {"answer": 1}
    assert result.request_id == "req-1"
    assert client.call_args.kwargs["timeout"] is None
    assert observed_timeouts == [50.0]
    assert post.call_args.args[0] == "https://ai.invalid/v1/messages"
    assert post.call_args.kwargs["headers"] == {
        "X-Acuven-Client-Id": "aic_test",
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
    }
    assert json.loads(post.call_args.kwargs["content"]) == {
        "feature": "crm.test",
        "model": "test-model",
        "system": "system instruction",
        "messages": [{"role": "user", "content": "input body"}],
        "max_tokens": 12,
        "output_config": {"format": "json_schema", "schema": {"type": "object"}, "effort": "low"},
    }


@pytest.mark.asyncio
async def test_unconfigured_client_does_not_make_a_request(monkeypatch):
    """Guards the criterion that any empty configuration returns unconfigured without HTTP."""
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "ai_model", "")
    post, _ = _stub(monkeypatch, _response(200))

    result = await _send()

    assert not ai_api_client.is_ai_configured()
    assert result.outcome == ai_api_client.NOT_CONFIGURED
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_over_64_kib_is_an_error_without_http(monkeypatch):
    """Guards the serialized 64 KiB request ceiling criterion."""
    _configure(monkeypatch)
    post, _ = _stub(monkeypatch, _response(200))

    result = await ai_api_client.send_message(
        feature="crm.test",
        system="x" * (ai_api_client.REQUEST_LIMIT_BYTES + 1),
        messages=[],
        max_tokens=1,
        schema={},
    )

    assert result.outcome == ai_api_client.ERROR
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_total_50_second_deadline_returns_temporary_unavailability(monkeypatch):
    """Guards the criterion that the whole HTTP call has a 50-second total deadline."""
    _configure(monkeypatch)
    _stub(monkeypatch, _response(200))
    observed_timeouts = []

    async def expire_total_deadline(awaitable, timeout):
        observed_timeouts.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(ai_api_client.asyncio, "wait_for", expire_total_deadline)

    result = await _send()

    assert observed_timeouts == [50.0]
    assert result.outcome == ai_api_client.UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_or_error", "outcome"),
    [
        (
            _response(200, {"request_id": "r", "stop_reason": "refusal", "text": "{}"}),
            ai_api_client.ERROR,
        ),
        (
            _response(200, {"request_id": "r", "stop_reason": "max_tokens", "text": "{}"}),
            ai_api_client.ERROR,
        ),
        (
            _response(200, {"request_id": "r", "stop_reason": "end_turn", "text": "not-json"}),
            ai_api_client.ERROR,
        ),
        (
            _response(200, {"request_id": "r", "stop_reason": "end_turn", "text": "[]"}),
            ai_api_client.ERROR,
        ),
        (_invalid_json_response(), ai_api_client.ERROR),
        (_response(200, []), ai_api_client.ERROR),
        (_response(401), ai_api_client.ERROR),
        (_response(403), ai_api_client.ERROR),
        (_response(413), ai_api_client.ERROR),
        (_response(422), ai_api_client.ERROR),
        (_response(500), ai_api_client.ERROR),
        (_response(429), ai_api_client.UNAVAILABLE),
        (_response(502), ai_api_client.UNAVAILABLE),
        (_response(503), ai_api_client.UNAVAILABLE),
        (_response(504), ai_api_client.UNAVAILABLE),
        (httpx.ConnectError("offline"), ai_api_client.UNAVAILABLE),
        (httpx.ReadTimeout("late"), ai_api_client.UNAVAILABLE),
        (asyncio.TimeoutError(), ai_api_client.UNAVAILABLE),
    ],
)
async def test_all_result_classification_branches_follow_acceptance_criteria(
    monkeypatch, response_or_error, outcome
):
    """Guards every specified error and temporary-unavailability result branch."""
    _configure(monkeypatch)
    _stub(monkeypatch, response_or_error)

    assert (await _send()).outcome == outcome


@pytest.mark.asyncio
async def test_logs_exclude_request_and_model_bodies(monkeypatch, caplog):
    """Guards the criterion that client logs never contain bodies, snapshots, or model output."""
    _configure(monkeypatch)
    _stub(
        monkeypatch,
        _response(
            200, {"request_id": "r", "stop_reason": "end_turn", "text": '{"secret":"model-output"}'}
        ),
    )
    caplog.set_level("INFO", logger=ai_api_client.__name__)

    await _send()

    log_text = caplog.text
    assert "input body" not in log_text
    assert "model-output" not in log_text
    assert "crm.test" in log_text and "request_id=r" in log_text
