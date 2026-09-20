from datetime import date

import jpholiday
from botocore.exceptions import ClientError

from logger import logger
from services.calendar_service import tokyo_today
from utils.response import APIError


class ScheduleService:
    def __init__(self, users, calendar, today=tokyo_today, *, notifications):
        self.users = users
        self.calendar = calendar
        self.today = today
        self.notifications = notifications

    def swap(self, sub, my_date, target_date):
        user = self.users.find_by_cognito_sub(sub)
        if user is None:
            raise APIError(404, "USER_NOT_FOUND", "User was not found.")
        user_id = user["userId"]
        logger.info("event=schedule_swap_requested userId=%s myDate=%s targetDate=%s", user_id, my_date, target_date)
        try:
            if my_date == target_date:
                raise APIError(400, "SAME_DATE", "Dates must be different.")
            my_item = self.calendar.get_date(my_date)
            target_item = self.calendar.get_date(target_date)
            self.validate_day(my_date, my_item)
            self.validate_day(target_date, target_item)
            if my_item["assignedUserId"] != user_id:
                raise APIError(403, "NOT_YOUR_SCHEDULE", "myDate must be assigned to the current user.")
            if target_item["assignedUserId"] == user_id:
                raise APIError(400, "OWN_TARGET_SCHEDULE", "targetDate must be assigned to another user.")
            if date.fromisoformat(target_date) < self.today():
                raise APIError(400, "PAST_TARGET_DATE", "targetDate must not be in the past.")
            self.calendar.swap_assignments(my_item, target_item)
        except ClientError as error:
            code = error.response["Error"]["Code"]
            reasons = error.response.get("CancellationReasons", [])
            conflict = code in ("TransactionConflictException", "ConditionalCheckFailedException") or (
                code == "TransactionCanceledException" and any(
                    reason.get("Code") in ("ConditionalCheckFailed", "TransactionConflict") for reason in reasons
                )
            )
            logger.warning("event=schedule_swap_failed userId=%s myDate=%s targetDate=%s result=failure reason=%s", user_id, my_date, target_date, code)
            if conflict:
                raise APIError(409, "SCHEDULE_CONFLICT", "Schedule changed; reload the calendar and try again.") from error
            raise
        except APIError as error:
            logger.info("event=schedule_swap_failed userId=%s myDate=%s targetDate=%s result=failure reason=%s", user_id, my_date, target_date, error.code)
            raise
        logger.info("event=schedule_swap_success userId=%s myDate=%s targetDate=%s result=success", user_id, my_date, target_date)
        try:
            self.notifications.send_swap_notifications(
                user_id, target_item["assignedUserId"], my_date, target_date,
            )
        except Exception:
            # The calendar is already committed; never turn an email failure into a failed swap.
            logger.exception("event=schedule_swap_email_failed userId=%s myDate=%s targetDate=%s result=failure", user_id, my_date, target_date)
        return {"success": True}

    @staticmethod
    def validate_day(day_string, item):
        if item is None:
            raise APIError(404, "SCHEDULE_NOT_FOUND", "Schedule date was not found.")
        if not item.get("assignedUserId"):
            raise APIError(400, "UNASSIGNED_SCHEDULE", "Schedule must have an assigned user.")
        day = date.fromisoformat(day_string)
        holiday = item.get("holiday")
        if holiday is None:
            holiday = jpholiday.is_holiday(day)
        if day.weekday() >= 5 or holiday:
            raise APIError(400, "NON_WORKING_DAY", "Schedules on weekends or holidays cannot be swapped.")
