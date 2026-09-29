import json
import re
from unittest.mock import Mock

import pytest

import lambda_function
from services.notification_service import NotificationService
from utils import performance
from utils.response import APIError


def perf_messages(caplog):
    return [record.getMessage() for record in caplog.records
            if record.getMessage().startswith("[PERF]")]


@pytest.mark.parametrize("failure", [False, True])
def test_timer_uses_milliseconds_and_logs_on_failure(monkeypatch, caplog, failure):
    monkeypatch.setattr(performance.time, "perf_counter", Mock(side_effect=[10.0, 10.125]))
    def operation():
        with performance.measure_perf("GET /calendar", "query_calendar"):
            if failure:
                raise ValueError("private-data")
    if failure:
        with pytest.raises(ValueError, match="private-data"):
            operation()
    else:
        operation()
    assert perf_messages(caplog) == [
        "[PERF] route=GET /calendar step=query_calendar duration_ms=125.0",
    ]


@pytest.mark.parametrize("route,factory,method,body", [
    ("GET /calendar", "create_service", "get_calendar", None),
    ("PATCH /me/notification", "create_notification_service", "set_notification", {"enabled": True}),
    ("POST /schedule/swap", "create_schedule_service", "swap",
     {"myDate": "2026-09-28", "targetDate": "2026-09-29"}),
])
@pytest.mark.parametrize("outcome", ["success", "api_error", "unexpected", "unauthorized"])
def test_handler_total_on_success_and_errors(monkeypatch, caplog, route, factory, method, body, outcome):
    service = Mock()
    operation = getattr(service, method)
    operation.return_value = {"ok": True}
    if outcome == "api_error":
        operation.side_effect = APIError(404, "NOT_FOUND", "Not found")
    elif outcome == "unexpected":
        operation.side_effect = RuntimeError("private-data")
    monkeypatch.setattr(lambda_function, factory, lambda: service)
    request = {
        "routeKey": route, "body": json.dumps(body),
        "queryStringParameters": {"year": "2026", "month": "9"},
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "secret-sub"}}}},
        "headers": {"Authorization": "secret-token"},
    }
    if outcome == "unauthorized":
        request.pop("requestContext")
    result = lambda_function.lambda_handler(request, None)
    assert result["statusCode"] == {"success": 200, "api_error": 404, "unexpected": 500, "unauthorized": 401}[outcome]
    messages = perf_messages(caplog)
    assert sum("step=total " in message for message in messages) == 1
    assert "step=total " in messages[-1]
    for message in messages:
        assert re.fullmatch(r"\[PERF\] route=" + re.escape(route) + r" step=\w+ duration_ms=\d+\.\d", message)
        assert all(secret not in message for secret in ("secret-sub", "secret-token", "private-data"))


def test_ses_failure_timed_separately_and_second_recipient_continues(caplog):
    users, ses = Mock(), Mock()
    users.find_by_user_id.return_value = {"email": "private@example.com"}
    ses.send_email.side_effect = [RuntimeError("send failed"), {}]
    service = NotificationService(users, lambda: ses, "sender@example.com")
    service.send_swap_notifications("u1", "u2", "2026-09-28", "2026-09-29")
    messages = perf_messages(caplog)
    assert [message.split("step=")[1].split()[0] for message in messages] == [
        "get_notification_users", "send_email", "get_notification_users", "send_email",
    ]
    assert ses.send_email.call_count == 2
    assert all("@" not in message for message in messages)
