from unittest.mock import Mock, call

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

import lambda_function
from config import Config
from repositories.user_repository import UserRepository
from services.notification_service import NotificationService


@pytest.fixture
def mailer():
    users, ses = Mock(), Mock()
    users.find_by_user_id.side_effect = lambda user_id: {
        "userId": user_id, "email": f"{user_id}@example.com", "emailNotification": False,
    }
    return NotificationService(users, lambda: ses, "sender@example.com"), users, ses


def send(service):
    service.send_swap_notifications("u1", "u2", "2026-09-24", "2026-10-01")


@pytest.mark.parametrize("preference", [True, False, None])
def test_both_recipients_receive_their_correct_new_dates(mailer, preference):
    service, users, ses = mailer
    users.find_by_user_id.side_effect = lambda user_id: {
        "userId": user_id, "email": f"{user_id}@example.com", "emailNotification": preference,
    }
    send(service)
    assert users.find_by_user_id.call_args_list == [call("u1"), call("u2")]
    assert ses.send_email.call_count == 2
    for invocation, user, old, new in zip(ses.send_email.call_args_list,
                                         ("u1", "u2"), ("2026年09月24日 木曜日", "2026年10月01日 木曜日"),
                                         ("2026年10月01日 木曜日", "2026年09月24日 木曜日")):
        request = invocation.kwargs
        assert request["Source"] == "sender@example.com"
        assert request["Destination"] == {"ToAddresses": [f"{user}@example.com"]}
        assert request["Message"]["Subject"]["Charset"] == "UTF-8"
        body = request["Message"]["Body"]["Text"]
        assert body["Charset"] == "UTF-8"
        assert f"変更前: {old}" in body["Data"]
        assert f"変更後: {new}" in body["Data"]
        assert body["Data"].endswith("https://d88l94lniveng.cloudfront.net/\n")


def test_ses_rejection_does_not_stop_second_recipient(mailer, caplog):
    service, _, ses = mailer
    ses.send_email.side_effect = [ClientError({"Error": {"Code": "MessageRejected", "Message": "Rejected"}}, "SendEmail"), {}]
    send(service)
    assert ses.send_email.call_count == 2
    assert "event=schedule_swap_email_failed userId=u1" in caplog.text
    assert "event=schedule_swap_email_sent userId=u2" in caplog.text


@pytest.mark.parametrize("first_user", [None, {"userId": "u1"}, {"userId": "u1", "email": " "}])
def test_missing_recipient_does_not_stop_second_recipient(mailer, first_user, caplog):
    service, users, ses = mailer
    users.find_by_user_id.side_effect = [first_user, {"userId": "u2", "email": "u2@example.com"}]
    send(service)
    ses.send_email.assert_called_once()
    assert ses.send_email.call_args.kwargs["Destination"] == {"ToAddresses": ["u2@example.com"]}
    assert "event=schedule_swap_email_failed userId=u1" in caplog.text


def test_lookup_failure_does_not_stop_other_recipient(mailer, caplog):
    service, users, ses = mailer
    users.find_by_user_id.side_effect = [RuntimeError("lookup failed"), {"email": "u2@example.com"}]
    send(service)
    ses.send_email.assert_called_once()
    assert "event=schedule_swap_email_failed userId=u1" in caplog.text


def test_missing_sender_and_client_creation_failure_are_logged(mailer, caplog):
    service, _, ses = mailer
    service.from_email = None
    send(service)
    ses.send_email.assert_not_called()
    assert "SES_FROM_EMAIL is not configured" in caplog.text
    service.from_email = "sender@example.com"
    service.ses_client_factory = Mock(side_effect=RuntimeError("SES setup failed"))
    send(service)
    assert service.ses_client_factory.call_count == 2
    assert "SES setup failed" in caplog.text


def test_user_lookup_uses_primary_key():
    table = Mock()
    table.get_item.return_value = {"Item": {"userId": "u2", "email": "u2@example.com"}}
    user = UserRepository(table).find_by_user_id("u2")
    assert user["email"] == "u2@example.com"
    table.get_item.assert_called_once_with(Key={"userId": "u2"}, ConsistentRead=True)
    table.scan.assert_not_called()


def test_ses_parameters_match_botocore_model(mailer):
    service, users, ses = mailer
    send(service)
    client = boto3.client("ses", region_name="ap-northeast-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    with Stubber(client) as stub:
        for invocation in ses.send_email.call_args_list:
            stub.add_response("send_email", {"MessageId": "test-message"}, invocation.kwargs)
        send(NotificationService(users, lambda: client, "sender@example.com"))
        stub.assert_no_pending_responses()


def test_factory_wires_lazy_reused_ses_client(monkeypatch):
    monkeypatch.setenv("USERS_TABLE_NAME", "Users")
    monkeypatch.setenv("CALENDAR_TABLE_NAME", "Calendar")
    monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
    monkeypatch.setenv("SES_FROM_EMAIL", "sender@example.com")
    monkeypatch.setenv("CALENDAR_URL", "https://calendar.example.com/")
    monkeypatch.setattr(lambda_function, "_dynamodb", Mock())
    monkeypatch.setattr(lambda_function, "_transaction_client", Mock())
    monkeypatch.setattr(lambda_function, "_ses_client", None)
    client_factory = Mock()
    monkeypatch.setattr(lambda_function.boto3, "client", client_factory)
    service = lambda_function.create_schedule_service()
    assert service.notifications.from_email == "sender@example.com"
    assert service.notifications.calendar_url == "https://calendar.example.com/"
    assert Config.from_env().ses_from_email == "sender@example.com"
    client_factory.assert_not_called()
    first = service.notifications.ses_client_factory()
    assert service.notifications.ses_client_factory() is first
    client_factory.assert_called_once_with("ses", region_name="ap-northeast-1")


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_calendar_url_defaults_when_unset_or_blank(monkeypatch, configured):
    monkeypatch.setenv("USERS_TABLE_NAME", "Users")
    monkeypatch.setenv("CALENDAR_TABLE_NAME", "Calendar")
    if configured is None:
        monkeypatch.delenv("CALENDAR_URL", raising=False)
    else:
        monkeypatch.setenv("CALENDAR_URL", configured)
    assert Config.from_env().calendar_url == "https://d88l94lniveng.cloudfront.net/"


def test_requested_dates_and_custom_link_in_email(mailer):
    service, _, ses = mailer
    service.calendar_url = "https://calendar.example.com/"
    service.send_swap_notifications("u1", "u2", "2026-09-28", "2026-09-29")
    assert ses.send_email.call_args_list[0].kwargs["Message"]["Body"]["Text"]["Data"] == (
        "朝礼の担当日が変更されました。\n\n"
        "変更前: 2026年09月28日 月曜日\n"
        "変更後: 2026年09月29日 火曜日\n\n"
        "最新の予定はカレンダーでご確認ください。\n"
        "https://calendar.example.com/\n"
    )


@pytest.mark.parametrize("value, expected", [
    ("2026-09-23", "2026年09月23日 水曜日"),
    ("2026-09-25", "2026年09月25日 金曜日"),
    ("2026-09-26", "2026年09月26日 土曜日"),
    ("2026-09-27", "2026年09月27日 日曜日"),
    ("2028-02-29", "2028年02月29日 火曜日"),
])
def test_date_format(value, expected):
    assert NotificationService.format_date(value) == expected
