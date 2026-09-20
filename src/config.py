import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    users_table_name: str
    calendar_table_name: str
    aws_region: str | None = None
    ses_from_email: str | None = None

    @classmethod
    def from_env(cls):
        return cls(
            users_table_name=os.environ["USERS_TABLE_NAME"],
            calendar_table_name=os.environ["CALENDAR_TABLE_NAME"],
            aws_region=os.environ.get("AWS_REGION"),
            ses_from_email=os.environ.get("SES_FROM_EMAIL"),
        )
