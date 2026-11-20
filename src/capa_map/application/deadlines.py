"""Recorded closure/deadline facts. No SLA, extensions or missing dates inferred."""
from datetime import date, datetime, timezone
import re


def current_day():
    return datetime.now(timezone.utc).date()


def calendar_date(value):
    try:
        return datetime.fromisoformat(str(value or '').strip().replace('Z', '+00:00')).date()
    except ValueError:
        return None


def timing(fields, as_of=None):
    as_of = as_of or current_day()
    due = calendar_date(fields.get('date_to_close') or fields.get('due_date') or fields.get('date_due'))
    closed = calendar_date(fields.get('date_closed'))
    opened = calendar_date(fields.get('date_opened'))
    status = str(fields.get('status') or '').strip().casefold()
    flag = str(fields.get('is_closed', '')).strip().casefold()
    is_closed = True if flag in {'1', '1.0', 'true', 'yes'} else False if flag in {'0', '0.0', 'false', 'no'} else None
    status_closed = status.startswith('closed')
    status_open = bool(re.match(r'^(?:open|reopen|in.progress|under.investigation)\b', status))
    result = {'dueDate': due.isoformat() if due else None,
              'closedDate': closed.isoformat() if closed else None, 'dueStatus': 'unavailable'}
    if re.search(r'\b(?:cancelled|canceled|voided|void)\b', status):
        result['dueStatus'] = 'cancelled'
        return result
    if (is_closed is False and status_closed) or (is_closed is True and status_open):
        return result
    if is_closed is None:
        is_closed = True if status_closed else False if status and status not in {'not provided', 'unknown', 'unavailable'} else None
    if not due or (opened and due < opened):
        return result
    if is_closed is True and closed and closed <= as_of and (not opened or closed >= opened):
        result['dueStatus'] = 'closed_on_time' if closed <= due else 'closed_late'
    elif is_closed is False and closed is None:
        result['dueStatus'] = 'open_overdue' if due < as_of else 'open_within_due'
    return result
