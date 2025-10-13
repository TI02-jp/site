"""Service for generating recurring meetings."""

import uuid
from datetime import date, datetime, timedelta
from typing import List

from app.models.tables import ReuniaoRecorrenciaTipo


def generate_recurrence_dates(
    start_date: date,
    end_date: date,
    recurrence_type: ReuniaoRecorrenciaTipo,
    weekdays: List[int] = None
) -> List[date]:
    """
    Generate a list of dates for recurring meetings.

    Args:
        start_date: The initial date of the meeting
        end_date: The last date to generate meetings
        recurrence_type: Type of recurrence (DIARIA, SEMANAL, etc)
        weekdays: List of weekdays (0=Monday, 6=Sunday) for weekly recurrence

    Returns:
        List of dates where meetings should be created
    """
    dates = []
    current_date = start_date

    if recurrence_type == ReuniaoRecorrenciaTipo.NENHUMA:
        return [start_date]

    elif recurrence_type == ReuniaoRecorrenciaTipo.DIARIA:
        while current_date <= end_date:
            dates.append(current_date)
            current_date += timedelta(days=1)

    elif recurrence_type == ReuniaoRecorrenciaTipo.SEMANAL:
        if weekdays:
            # Custom weekly recurrence with specific weekdays
            while current_date <= end_date:
                if current_date.weekday() in weekdays:
                    dates.append(current_date)
                current_date += timedelta(days=1)
        else:
            # Weekly on the same day
            while current_date <= end_date:
                dates.append(current_date)
                current_date += timedelta(weeks=1)

    elif recurrence_type == ReuniaoRecorrenciaTipo.QUINZENAL:
        while current_date <= end_date:
            dates.append(current_date)
            current_date += timedelta(weeks=2)

    elif recurrence_type == ReuniaoRecorrenciaTipo.MENSAL:
        while current_date <= end_date:
            dates.append(current_date)
            # Add one month
            if current_date.month == 12:
                current_date = current_date.replace(year=current_date.year + 1, month=1)
            else:
                # Handle edge case where day doesn't exist in next month
                try:
                    current_date = current_date.replace(month=current_date.month + 1)
                except ValueError:
                    # Day doesn't exist in next month (e.g., Jan 31 -> Feb 31)
                    # Go to last day of next month
                    next_month = current_date.month + 1 if current_date.month < 12 else 1
                    next_year = current_date.year if current_date.month < 12 else current_date.year + 1
                    current_date = date(next_year, next_month, 1)
                    # Go to last day of the month
                    if next_month == 12:
                        current_date = date(next_year + 1, 1, 1) - timedelta(days=1)
                    else:
                        current_date = date(next_year, next_month + 1, 1) - timedelta(days=1)

    elif recurrence_type == ReuniaoRecorrenciaTipo.ANUAL:
        while current_date <= end_date:
            dates.append(current_date)
            # Add one year
            try:
                current_date = current_date.replace(year=current_date.year + 1)
            except ValueError:
                # Handles Feb 29 on leap years
                current_date = current_date.replace(year=current_date.year + 1, day=28)

    return dates


def generate_recurrence_group_id() -> str:
    """Generate a unique ID for a group of recurring meetings."""
    return str(uuid.uuid4())
