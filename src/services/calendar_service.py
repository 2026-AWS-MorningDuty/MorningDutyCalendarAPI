from datetime import datetime
from zoneinfo import ZoneInfo

import jpholiday

from logger import logger
from utils.date_utils import DAY_NAMES, adjacent_month, month_dates, month_key
from utils.response import APIError


def tokyo_today():
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


class CalendarService:
    def __init__(self, users, calendar, today=tokyo_today):
        self.users = users
        self.calendar = calendar
        self.today = today

    def get_calendar(self, sub, year, month):
        user = self.users.find_by_cognito_sub(sub)
        if user is None:
            raise APIError(404, "USER_NOT_FOUND", "User was not found.")
        user_id = user["userId"]
        logger.info("event=calendar_requested userId=%s year=%s month=%s", user_id, year, month)
        previous = adjacent_month(year, month, -1)
        following = adjacent_month(year, month, 1)
        previous_available = bool(previous and self.calendar.month_exists(month_key(*previous)))
        next_available = bool(following and self.calendar.month_exists(month_key(*following)))
        days = self.build_days(year, month, self.calendar.list_month(month_key(year, month)), user_id)
        today = self.today()
        candidate_month = (today.year, today.month)
        if candidate_month == (year, month):
            candidate_days = list(days)
            candidate_next, candidate_next_available = following, next_available
        else:
            candidate_days = self.build_days(*candidate_month, self.calendar.list_month(month_key(*candidate_month)), user_id)
            candidate_next = adjacent_month(*candidate_month, 1)
            candidate_next_available = bool(candidate_next and self.calendar.month_exists(month_key(*candidate_next)))
        if candidate_next_available:
            candidate_days.extend(self.build_days(
                *candidate_next, self.calendar.list_month(month_key(*candidate_next)), user_id,
            ))
        candidates = [
            {key: day[key] for key in ("date", "dayOfWeek", "assignedUserId", "assignedUserName")}
            for day in candidate_days
            if day["assignedUserId"] and not day["mine"]
            and day["dayOfWeek"] not in ("SAT", "SUN")
            and not day["holiday"] and day["date"] >= today.isoformat()
        ]
        return {
            "year": year, "month": month,
            "currentUser": {key: user[key] for key in ("userId", "name", "emailNotification")},
            "navigation": {"previousMonthAvailable": previous_available, "nextMonthAvailable": next_available},
            "days": days,
            "changeCandidates": candidates,
        }

    @staticmethod
    def build_days(year, month, items, user_id):
        by_date = {item["date"]: item for item in items}
        days = []
        for day in month_dates(year, month):
            item = by_date.get(day.isoformat(), {})
            computed_name = jpholiday.is_holiday_name(day)
            holiday = item.get("holiday")
            if holiday is None:
                holiday = day.weekday() >= 5 or computed_name is not None
            holiday_name = item.get("holidayName", computed_name if holiday else None)
            assigned_id = item.get("assignedUserId")
            days.append({
                "date": day.isoformat(), "dayOfWeek": DAY_NAMES[day.weekday()],
                "holiday": holiday, "holidayName": holiday_name,
                "hasSchedule": bool(assigned_id),
                "assignedUserId": assigned_id, "assignedUserName": item.get("assignedUserName"),
                "mine": assigned_id == user_id,
            })
        return days
