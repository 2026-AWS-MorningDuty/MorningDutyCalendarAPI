import base64
import json
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

import lambda_function
from repositories.user_repository import UserRepository
from services.notification_service import NotificationService


def event(body):
    return {
        "routeKey": "PATCH /me/notification", "body": body,
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "verified-sub"}}}},
    }


@pytest.fixture
def users(monkeypatch):
    repository = Mock()
    repository.find_by_cognito_sub.return_value = {"userId": "authenticated-user"}
    monkeypatch.setattr(lambda_function, "create_notification_service", lambda: NotificationService(repository))
    return repository


@pytest.mark.parametrize("enabled", [True, False])
def test_explicit_setting_and_repeated_request(users, enabled):
    request = event(json.dumps({"enabled": enabled, "userId": "someone-else"}))
    for _ in range(2):
        result = lambda_function.lambda_handler(request, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"]) == {"emailNotification": enabled}
    assert users.set_email_notification.call_count == 2
    users.set_email_notification.assert_called_with("authenticated-user", enabled)
    users.find_by_cognito_sub.assert_called_with("verified-sub")


@pytest.mark.parametrize("body", [None, "", "{", "null", "[]", "true", "{}", '{"enabled": null}', '{"enabled": 0}', '{"enabled": 1}', '{"enabled": "true"}'])
def test_invalid_body_never_writes(users, body):
    result = lambda_function.lambda_handler(event(body), None)
    assert result["statusCode"] == 400
    assert json.loads(result["body"])["success"] is False
    users.find_by_cognito_sub.assert_not_called()
    users.set_email_notification.assert_not_called()


def test_base64_body(users):
    request = event(base64.b64encode(b'{"enabled": false}').decode())
    request["isBase64Encoded"] = True
    assert lambda_function.lambda_handler(request, None)["statusCode"] == 200
    users.set_email_notification.assert_called_once_with("authenticated-user", False)
    request["body"] = "%%%"
    assert lambda_function.lambda_handler(request, None)["statusCode"] == 400
    assert users.set_email_notification.call_count == 1


def test_missing_claims_and_unknown_user(users):
    request = event('{"enabled": true}')
    request.pop("requestContext")
    request["headers"] = {"Authorization": "Bearer untrusted"}
    assert lambda_function.lambda_handler(request, None)["statusCode"] == 401
    users.find_by_cognito_sub.assert_not_called()
    users.find_by_cognito_sub.return_value = None
    assert lambda_function.lambda_handler(event('{"enabled": true}'), None)["statusCode"] == 404
    users.set_email_notification.assert_not_called()


@pytest.mark.parametrize("code,status", [("ConditionalCheckFailedException", 404), ("ProvisionedThroughputExceededException", 500)])
def test_update_failures(users, code, status):
    users.set_email_notification.side_effect = ClientError(
        {"Error": {"Code": code, "Message": "private details"}}, "UpdateItem",
    )
    result = lambda_function.lambda_handler(event('{"enabled": true}'), None)
    assert result["statusCode"] == status
    assert "private details" not in result["body"]


def test_repository_updates_only_notification_and_prevents_upsert():
    table = Mock()
    UserRepository(table).set_email_notification("u1", False)
    table.update_item.assert_called_once_with(
        Key={"userId": "u1"},
        UpdateExpression="SET #notification = :enabled",
        ConditionExpression="attribute_exists(#userId)",
        ExpressionAttributeNames={"#notification": "emailNotification", "#userId": "userId"},
        ExpressionAttributeValues={":enabled": False},
    )
