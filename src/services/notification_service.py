from botocore.exceptions import ClientError

from logger import logger
from utils.response import APIError


class NotificationService:
    def __init__(self, users, ses_client_factory=None, from_email=None):
        self.users = users
        self.ses_client_factory = ses_client_factory
        self.from_email = from_email

    def set_notification(self, sub, enabled):
        user = self.users.find_by_cognito_sub(sub)
        if user is None:
            raise APIError(404, "USER_NOT_FOUND", "User was not found.")
        user_id = user["userId"]
        try:
            self.users.set_email_notification(user_id, enabled)
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise APIError(404, "USER_NOT_FOUND", "User was not found.") from error
            raise
        logger.info("event=notification_updated userId=%s result=success", user_id)
        return {"emailNotification": enabled}

    def send_swap_notifications(self, my_user_id, target_user_id, my_date, target_date):
        # Swap notices are system notifications; emailNotification only controls reminders.
        for user_id, previous_date, assigned_date in (
            (my_user_id, my_date, target_date),
            (target_user_id, target_date, my_date),
        ):
            try:
                user = self.users.find_by_user_id(user_id)
                if user is None:
                    raise ValueError("Notification user was not found")
                email = user.get("email")
                if not isinstance(email, str) or not email.strip():
                    raise ValueError("Notification user has no email address")
                if not self.from_email:
                    raise ValueError("SES_FROM_EMAIL is not configured")
                self.ses_client_factory().send_email(
                    Source=self.from_email,
                    Destination={"ToAddresses": [email]},
                    Message={
                        "Subject": {"Data": "朝礼担当日の変更のお知らせ", "Charset": "UTF-8"},
                        "Body": {"Text": {
                            "Data": (
                                "朝礼の担当日が変更されました。\n\n"
                                f"変更前: {previous_date}\n"
                                f"変更後: {assigned_date}\n\n"
                                "最新の予定はカレンダーでご確認ください。\n"
                            ),
                            "Charset": "UTF-8",
                        }},
                    },
                )
                logger.info(
                    "event=schedule_swap_email_sent userId=%s myDate=%s targetDate=%s result=success",
                    user_id, my_date, target_date,
                )
            except Exception:
                # Each recipient is independent, and the transaction has already committed.
                logger.exception(
                    "event=schedule_swap_email_failed userId=%s myDate=%s targetDate=%s result=failure",
                    user_id, my_date, target_date,
                )
