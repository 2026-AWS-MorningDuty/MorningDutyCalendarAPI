import calendar
import re
from datetime import date

from utils.response import APIError

DAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def parse_year_month(params):
    params = params or {}
    year, month = params.get("year"), params.get("month")
    if not (isinstance(year, str) and re.fullmatch(r"[0-9]{4}", year)
            and isinstance(month, str) and re.fullmatch(r"[0-9]{1,2}", month)):
        raise APIError(400, "INVALID_YEAR_MONTH", "year and month are required numeric values.")
    year, month = int(year), int(month)
    if not (1 <= year <= 9999 and 1 <= month <= 12):
        raise APIError(400, "INVALID_YEAR_MONTH", "year or month is out of range.")
    return year, month


def month_key(year, month):
    return f"{year:04d}-{month:02d}"


def adjacent_month(year, month, offset):
    index = (year - 1) * 12 + month - 1 + offset
    if not 0 <= index < 9999 * 12:
        return None
    return index // 12 + 1, index % 12 + 1


def month_dates(year, month):
    return [date(year, month, day) for day in range(1, calendar.monthrange(year, month)[1] + 1)]
