import json
from unittest.mock import Mock

import pytest

import lambda_function
from repositories.user_repository import UserRepository
from services.calendar_service import CalendarService
from services.notification_service import NotificationService
from services.schedule_service import ScheduleService


@pytest.mark.parametrize("paginated", [False, True])
@pytest.mark.parametrize("route", ["GET /calendar", "PATCH /me/notification", "POST /schedule/swap"])
def test_duplicate_identity_returns_internal_error_without_side_effects(monkeypatch, caplog, paginated, route):
    table, calendar, notifications = Mock(), Mock(), Mock()
    first, second = {"userId": "u1"}, {"userId": "u2"}
    table.query.side_effect = (
        [{"Items": [first], "LastEvaluatedKey": {"userId": "u1"}}, {"Items": [second]}]
        if paginated else [{"Items": [first, second]}]
    )
    users = UserRepository(table)
    monkeypatch.setattr(lambda_function, "create_service", lambda: CalendarService(users, calendar))
    monkeypatch.setattr(lambda_function, "create_notification_service", lambda: NotificationService(users))
    monkeypatch.setattr(lambda_function, "create_schedule_service", lambda: ScheduleService(users, calendar, notifications=notifications))
    body = {"enabled": True} if route == "PATCH /me/notification" else {"myDate": "2026-09-24", "targetDate": "2026-10-01"}
    event = {
        "routeKey": route, "body": json.dumps(body),
        "queryStringParameters": {"year": "2026", "month": "9"},
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "private-sub"}}}},
    }
    result = lambda_function.lambda_handler(event, None)
    assert result["statusCode"] == 500
    payload = json.loads(result["body"])
    assert payload["success"] is False
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert "reason=duplicate_cognitoSub" in caplog.text
    assert "private-sub" not in caplog.text
    assert "Traceback" not in result["body"]
    assert not calendar.mock_calls
    assert not notifications.mock_calls
    table.update_item.assert_not_called()
