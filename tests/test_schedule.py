import base64
import json
from datetime import date
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

import lambda_function
from repositories.calendar_repository import CalendarRepository
from services.schedule_service import ScheduleService


def item(day, user):
    return {"month": day[:7], "date": day, "assignedUserId": user,
            "assignedUserName": user + " name", "originalUserId": "original",
            "originalUserName": "Original name", "holiday": False}


def event(body=None):
    return {"routeKey": "POST /schedule/swap", "body": json.dumps(body if body is not None else {
        "myDate": "2026-09-24", "targetDate": "2026-10-01"}),
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "verified-sub"}}}}}


@pytest.fixture
def setup(monkeypatch):
    users, calendar = Mock(), Mock()
    users.find_by_cognito_sub.return_value = {"userId": "u1"}
    items = {"2026-09-24": item("2026-09-24", "u1"), "2026-10-01": item("2026-10-01", "u2")}
    calendar.get_date.side_effect = items.get
    service = ScheduleService(users, calendar, today=lambda: date(2026, 9, 24), notifications=Mock())
    monkeypatch.setattr(lambda_function, "create_schedule_service", lambda: service)
    return users, calendar, items, service


def test_success_cross_month_and_verified_identity(setup):
    users, calendar, items, _ = setup
    result = lambda_function.lambda_handler(event(), None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"success": True}
    users.find_by_cognito_sub.assert_called_once_with("verified-sub")
    calendar.swap_assignments.assert_called_once_with(items["2026-09-24"], items["2026-10-01"])


@pytest.mark.parametrize("body", [[], {}, {"myDate": "2026-09-24"},
    {"myDate": "2026-09-24", "targetDate": "2026-10-01", "userId": "other"},
    {"myDate": "2026-02-30", "targetDate": "2026-10-01"},
    {"myDate": "20260924", "targetDate": "2026-10-01"},
    {"myDate": None, "targetDate": "2026-10-01"},
    {"myDate": "2026-09-24", "targetDate": "2026-09-24"}])
def test_bad_requests_never_write(setup, body):
    assert lambda_function.lambda_handler(event(body), None)["statusCode"] == 400
    setup[1].swap_assignments.assert_not_called()
    setup[3].notifications.send_swap_notifications.assert_not_called()


@pytest.mark.parametrize("field,change,status", [
    ("2026-09-24", None, 404), ("2026-10-01", None, 404),
    ("2026-09-24", {"assignedUserId": None}, 400),
    ("2026-10-01", {"assignedUserId": None}, 400),
    ("2026-09-24", {"assignedUserId": "other"}, 403),
    ("2026-10-01", {"assignedUserId": "u1"}, 400),
    ("2026-09-24", {"holiday": True}, 400), ("2026-10-01", {"holiday": True}, 400),
])
def test_schedule_validation(setup, field, change, status):
    if change is None:
        del setup[2][field]
    else:
        setup[2][field].update(change)
    assert lambda_function.lambda_handler(event(), None)["statusCode"] == status
    setup[1].swap_assignments.assert_not_called()


@pytest.mark.parametrize("key,day", [("myDate", "2026-09-26"), ("targetDate", "2026-09-26"),
                                      ("myDate", "2026-09-23"), ("targetDate", "2026-11-03")])
def test_weekends_and_calculated_japanese_holidays(setup, key, day):
    entry = item(day, "u1" if key == "myDate" else "u2")
    if day != "2026-09-26":
        del entry["holiday"]
    setup[2][day] = entry
    body = {"myDate": "2026-09-24", "targetDate": "2026-10-01", key: day}
    assert lambda_function.lambda_handler(event(body), None)["statusCode"] == 400
    setup[1].swap_assignments.assert_not_called()


def test_past_target_rejected_today_allowed_and_past_source_allowed(setup):
    setup[3].today = lambda: date(2026, 10, 2)
    assert lambda_function.lambda_handler(event(), None)["statusCode"] == 400
    setup[1].swap_assignments.assert_not_called()
    setup[3].today = lambda: date(2026, 10, 1)
    assert lambda_function.lambda_handler(event(), None)["statusCode"] == 200


def test_auth_missing_user_and_base64(setup):
    request = event()
    del request["requestContext"]
    assert lambda_function.lambda_handler(request, None)["statusCode"] == 401
    setup[0].find_by_cognito_sub.assert_not_called()
    request = event()
    request["body"] = base64.b64encode(request["body"].encode()).decode()
    request["isBase64Encoded"] = True
    assert lambda_function.lambda_handler(request, None)["statusCode"] == 200
    setup[0].find_by_cognito_sub.return_value = None
    assert lambda_function.lambda_handler(event(), None)["statusCode"] == 404


@pytest.mark.parametrize("code,reasons,status", [
    ("TransactionCanceledException", [{"Code": "ConditionalCheckFailed"}], 409),
    ("TransactionCanceledException", [{"Code": "TransactionConflict"}], 409),
    ("TransactionConflictException", [], 409),
    ("TransactionCanceledException", [{"Code": "ProvisionedThroughputExceeded"}], 500),
    ("AccessDeniedException", [], 500),
])
def test_transaction_failures(setup, code, reasons, status):
    setup[1].swap_assignments.side_effect = ClientError({"Error": {"Code": code, "Message": "private details"},
        "CancellationReasons": reasons}, "TransactWriteItems")
    result = lambda_function.lambda_handler(event(), None)
    assert result["statusCode"] == status
    assert "private details" not in result["body"]
    setup[3].notifications.send_swap_notifications.assert_not_called()


def test_email_is_sent_after_commit_and_failure_preserves_success(setup, caplog):
    order = []
    setup[1].swap_assignments.side_effect = lambda *args: order.append("commit")

    def fail_email(*args):
        order.append("email")
        raise RuntimeError("Email delivery failed")

    setup[3].notifications.send_swap_notifications.side_effect = fail_email
    result = lambda_function.lambda_handler(event(), None)
    assert result["statusCode"] == 200
    assert order == ["commit", "email"]
    assert json.loads(result["body"]) == {"success": True}
    setup[1].swap_assignments.assert_called_once()
    setup[3].notifications.send_swap_notifications.assert_called_once_with("u1", "u2", "2026-09-24", "2026-10-01")
    assert "event=schedule_swap_email_failed" in caplog.text


def test_repository_atomic_write_shape_and_read():
    table, client = Mock(), Mock()
    table.name = "Calendar"
    repository = CalendarRepository(table, client)
    mine, target = item("2026-12-31", "u1"), item("2027-01-04", "u2")
    table.get_item.return_value = {"Item": mine}
    assert repository.get_date("2026-12-31") == mine
    table.get_item.assert_called_once_with(Key={"month": "2026-12", "date": "2026-12-31"}, ConsistentRead=True)
    repository.swap_assignments(mine, target)
    client.transact_write_items.assert_called_once()
    request = client.transact_write_items.call_args.kwargs
    assert len(request["TransactItems"]) == 2
    for operation, original, other in zip(request["TransactItems"], (mine, target), (target, mine)):
        update = operation["Update"]
        assert update["Key"] == {"month": {"S": original["month"]}, "date": {"S": original["date"]}}
        assert update["UpdateExpression"] == "SET #id = :newId, #name = :newName"
        assert update["ExpressionAttributeValues"][":newId"] == {"S": other["assignedUserId"]}
        assert update["ExpressionAttributeValues"][":newName"] == {"S": other["assignedUserName"]}
        assert update["ExpressionAttributeValues"][":oldId"] == {"S": original["assignedUserId"]}
        assert "#id = :oldId" in update["ConditionExpression"]
        assert "#holiday = :oldHoliday" in update["ConditionExpression"]
        assert "originalUserId" not in update["ExpressionAttributeNames"].values()
    # Validate serialized parameters against the real botocore operation model, without AWS calls.
    real_client = boto3.client("dynamodb", region_name="ap-northeast-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    with Stubber(real_client) as stub:
        stub.add_response("transact_write_items", {}, request)
        CalendarRepository(table, real_client).swap_assignments(mine, target)
        stub.assert_no_pending_responses()


def test_missing_optional_fields_are_guarded():
    table, client = Mock(), Mock()
    table.name = "Calendar"
    mine, target = item("2026-09-24", "u1"), item("2026-10-01", "u2")
    del mine["holiday"]
    del mine["assignedUserName"]
    CalendarRepository(table, client).swap_assignments(mine, target)
    update = client.transact_write_items.call_args.kwargs["TransactItems"][0]["Update"]
    assert "attribute_not_exists(#holiday)" in update["ConditionExpression"]
    assert "attribute_not_exists(#name)" in update["ConditionExpression"]
