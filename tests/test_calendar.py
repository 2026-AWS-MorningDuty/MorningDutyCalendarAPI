import json
from datetime import date
from unittest.mock import Mock

import pytest

import lambda_function
from repositories.calendar_repository import CalendarRepository
from repositories.user_repository import UserRepository
from services.calendar_service import CalendarService
from utils.date_utils import adjacent_month, parse_year_month
from utils.response import APIError


USER = {"userId": "u1", "name": "User", "emailNotification": True}


def item(day, user="u2", **extra):
    return {"date": day, "assignedUserId": user, "assignedUserName": "Name", **extra}


def service(months, today=date(2026, 9, 24)):
    users = Mock()
    users.find_by_cognito_sub.return_value = USER
    calendar = Mock()
    calendar.list_month.side_effect = lambda key: months.get(key, [])
    calendar.month_exists.side_effect = lambda key: bool(months.get(key))
    return CalendarService(users, calendar, today=lambda: today)


def test_full_leap_month_and_merge():
    result = service({"2024-02": [item("2024-02-02", "u1")]}).get_calendar("sub", 2024, 2)
    assert len(result["days"]) == 29
    assert result["days"][0] == {
        "date": "2024-02-01", "dayOfWeek": "THU", "holiday": False,
        "holidayName": None, "hasSchedule": False, "assignedUserId": None,
        "assignedUserName": None, "mine": False,
    }
    assert result["days"][1]["mine"] is True
    assert result["days"][1]["hasSchedule"] is True
    assert result["days"][10]["holiday"] is True


@pytest.mark.parametrize("assignment", [{}, {"assignedUserId": None}, {"assignedUserId": ""}, {"assignedUserId": "u2"}])
def test_has_schedule_means_assigned_user(assignment):
    entry = {"date": "2026-09-24", "holiday": False, **assignment}
    result = service({"2026-09": [entry]}).get_calendar("sub", 2026, 9)
    assigned = bool(assignment.get("assignedUserId"))
    assert result["days"][23]["hasSchedule"] is assigned
    assert bool(result["changeCandidates"]) is assigned


def test_candidates_use_actual_month_and_all_exclusions():
    months = {
        "2025-12": [item("2025-12-01")], "2026-02": [item("2026-02-01")],
        "2026-09": [
            item("2026-09-23"), item("2026-09-24"), item("2026-09-25", "u1"),
            item("2026-09-26", holiday=False), item("2026-09-28", holiday=True),
            item("2026-09-29", None), item("2026-09-30"),
        ],
        "2026-10": [item("2026-10-01"), item("2026-10-12")],
        "2026-11": [item("2026-11-02")],
    }
    result = service(months).get_calendar("sub", 2026, 1)
    assert result["navigation"] == {"previousMonthAvailable": True, "nextMonthAvailable": True}
    assert [d["date"] for d in result["changeCandidates"]] == ["2026-09-24", "2026-09-30", "2026-10-01"]
    assert set(result["changeCandidates"][0]) == {"date", "dayOfWeek", "assignedUserId", "assignedUserName"}


def test_db_holiday_override_and_computed_holidays():
    days = CalendarService.build_days(2026, 9, [
        item("2026-09-21", holiday=False, holidayName=None),
        item("2026-09-24", holiday=True, holidayName="Company holiday"),
    ], "u1")
    assert days[20]["holiday"] is False
    assert days[20]["holidayName"] is None
    assert days[22]["holiday"] is True
    assert days[22]["holidayName"] is not None
    assert days[23]["holidayName"] == "Company holiday"
    assert days[25]["holiday"] is True


def test_empty_calendar_and_year_rollover():
    result = service({}, date(2026, 12, 31)).get_calendar("sub", 2026, 12)
    assert len(result["days"]) == 31
    assert result["changeCandidates"] == []
    assert not any(result["navigation"].values())
    assert adjacent_month(2026, 12, 1) == (2027, 1)
    assert adjacent_month(2026, 1, -1) == (2025, 12)
    assert adjacent_month(1, 1, -1) is None
    assert adjacent_month(9999, 12, 1) is None


def test_candidates_roll_over_year():
    result = service({"2027-01": [item("2027-01-04")]}, date(2026, 12, 31)).get_calendar("sub", 2026, 12)
    assert [d["date"] for d in result["changeCandidates"]] == ["2027-01-04"]


def test_repository_queries_paginate_and_use_gsi():
    table = Mock()
    table.query.side_effect = [{"Items": [], "LastEvaluatedKey": {"userId": "cursor"}}, {"Items": [USER]}]
    assert UserRepository(table).find_by_cognito_sub("verified-sub") == USER
    first = table.query.call_args_list[0].kwargs
    assert first["IndexName"] == "cognitoSub-index"
    assert first["KeyConditionExpression"].get_expression()["values"][1] == "verified-sub"
    assert table.query.call_args.kwargs["ExclusiveStartKey"] == {"userId": "cursor"}
    table.query.side_effect = [{"Items": [{"date": "a"}], "LastEvaluatedKey": {"date": "a"}}, {"Items": [{"date": "b"}]}]
    assert CalendarRepository(table).list_month("2026-09") == [{"date": "a"}, {"date": "b"}]
    table.query.side_effect = None
    table.query.return_value = {"Count": 1}
    assert CalendarRepository(table).month_exists("2026-10") is True
    assert table.query.call_args.kwargs["Limit"] == 1
    table.scan.assert_not_called()


@pytest.mark.parametrize("params", [None, {}, {"year": "2026", "month": "13"}, {"year": "0000", "month": "1"}, {"year": "2026", "month": "1.5"}])
def test_invalid_input(params):
    with pytest.raises(APIError) as error:
        parse_year_month(params)
    assert error.value.status == 400


def event():
    return {"routeKey": "GET /calendar", "queryStringParameters": {"year": "2026", "month": "9"},
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "verified-sub"}}}}}


def test_handler_success_and_claims(monkeypatch):
    calendar = service({})
    monkeypatch.setattr(lambda_function, "create_service", lambda: calendar)
    result = lambda_function.lambda_handler(event(), None)
    assert result["statusCode"] == 200
    assert len(json.loads(result["body"])["days"]) == 30
    calendar.users.find_by_cognito_sub.assert_called_once_with("verified-sub")


def test_handler_errors_and_unimplemented_routes(monkeypatch):
    calendar = service({})
    monkeypatch.setattr(lambda_function, "create_service", lambda: calendar)
    request = event()
    request.pop("requestContext")
    request["headers"] = {"Authorization": "Bearer untrusted"}
    assert lambda_function.lambda_handler(request, None)["statusCode"] == 401
    for route in ("DELETE /calendar",):
        assert lambda_function.lambda_handler({"routeKey": route}, None)["statusCode"] == 404
    calendar.users.find_by_cognito_sub.return_value = None
    assert lambda_function.lambda_handler(event(), None)["statusCode"] == 404
    calendar.users.find_by_cognito_sub.side_effect = RuntimeError("private details")
    result = lambda_function.lambda_handler(event(), None)
    assert result["statusCode"] == 500
    assert "private details" not in result["body"]
    assert json.loads(result["body"])["success"] is False
