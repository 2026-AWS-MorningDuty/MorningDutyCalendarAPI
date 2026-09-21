import base64
import binascii
import json
import re
from datetime import date

import boto3

from config import Config
from logger import logger
from repositories.calendar_repository import CalendarRepository
from repositories.user_repository import UserRepository
from services.calendar_service import CalendarService
from services.notification_service import NotificationService
from services.schedule_service import ScheduleService
from utils.auth import get_cognito_sub
from utils.date_utils import parse_year_month
from utils.response import APIError, error_response, response

# Reuse the AWS resource across invocations; never cache users or calendar data.
_dynamodb = None
_transaction_client = None
_ses_client = None


def create_service():
    global _dynamodb
    config = Config.from_env()
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=config.aws_region)
    return CalendarService(
        UserRepository(_dynamodb.Table(config.users_table_name)),
        CalendarRepository(_dynamodb.Table(config.calendar_table_name)),
    )


def create_notification_service():
    global _dynamodb
    config = Config.from_env()
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=config.aws_region)
    return NotificationService(UserRepository(_dynamodb.Table(config.users_table_name)))


def create_schedule_service():
    global _dynamodb, _transaction_client
    config = Config.from_env()
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=config.aws_region)
    if _transaction_client is None:
        _transaction_client = boto3.client("dynamodb", region_name=config.aws_region)
    users = UserRepository(_dynamodb.Table(config.users_table_name))
    return ScheduleService(
        users,
        CalendarRepository(_dynamodb.Table(config.calendar_table_name), _transaction_client),
        notifications=NotificationService(users, get_ses_client, config.ses_from_email, config.calendar_url),
    )


def get_ses_client():
    global _ses_client
    # Initialize only after a committed swap so SES setup failures cannot undo success.
    if _ses_client is None:
        _ses_client = boto3.client("ses", region_name=Config.from_env().aws_region)
    return _ses_client


def parse_json_body(event):
    try:
        body = event.get("body")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body, validate=True).decode("utf-8")
        payload = json.loads(body)
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise APIError(400, "INVALID_BODY", "A valid JSON body is required.") from None
    return payload


def parse_notification_body(event):
    payload = parse_json_body(event)
    if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
        raise APIError(400, "INVALID_ENABLED", "enabled must be a boolean.")
    return payload["enabled"]


def parse_swap_body(event):
    payload = parse_json_body(event)
    if not isinstance(payload, dict) or set(payload) != {"myDate", "targetDate"}:
        raise APIError(400, "INVALID_BODY", "Only myDate and targetDate are accepted.")
    for value in payload.values():
        try:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            raise APIError(400, "INVALID_DATE", "Dates must be valid YYYY-MM-DD values.") from None
    return payload["myDate"], payload["targetDate"]


def lambda_handler(event, context):
    try:
        route = event.get("routeKey")
        if route not in ("GET /calendar", "PATCH /me/notification", "POST /schedule/swap"):
            raise APIError(404, "NOT_FOUND", "Route was not found.")
        sub = get_cognito_sub(event)
        if route == "POST /schedule/swap":
            my_date, target_date = parse_swap_body(event)
            return response(200, create_schedule_service().swap(sub, my_date, target_date))
        if route == "PATCH /me/notification":
            enabled = parse_notification_body(event)
            return response(200, create_notification_service().set_notification(sub, enabled))
        year, month = parse_year_month(event.get("queryStringParameters"))
        return response(200, create_service().get_calendar(sub, year, month))
    except APIError as error:
        return error_response(error)
    except Exception:
        failure_event = {"PATCH /me/notification": "notification_update_failed", "POST /schedule/swap": "schedule_swap_failed"}.get(event.get("routeKey"), "calendar_failed")
        logger.exception("event=%s result=failure", failure_event)
        return error_response(APIError(500, "INTERNAL_SERVER_ERROR", "An internal error occurred."))
