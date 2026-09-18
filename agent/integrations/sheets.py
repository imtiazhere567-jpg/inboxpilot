"""Google Sheet `rules` tab — read on a 60 s cache (rules.py), written when the page toggles shadow mode / outage."""
from __future__ import annotations

import logging

from agent.config import get_settings

log = logging.getLogger("ops_agent.sheets")


def _worksheet():
    import gspread

    import json

    s = get_settings()
    cred = s.google_service_account_json.strip()
    gc = gspread.service_account_from_dict(json.loads(cred)) if cred.startswith("{") else gspread.service_account(filename=cred)
    return gc.open_by_key(s.rules_sheet_id).worksheet(s.rules_sheet_tab)


def read_rules_sheet() -> dict[str, str]:
    """Columns: key | value | note. Returns {key: value} (values as strings)."""
    ws = _worksheet()
    out: dict[str, str] = {}
    for row in ws.get_all_records():
        key = str(row.get("key", "")).strip()
        if key:
            out[key] = str(row.get("value", "")).strip()
    return out


def write_rule(key: str, value: str) -> None:
    ws = _worksheet()
    cell = ws.find(key, in_column=1)
    if cell:
        ws.update_cell(cell.row, 2, value)
    else:
        ws.append_row([key, value, ""])


def sheet_url() -> str | None:
    s = get_settings()
    return f"https://docs.google.com/spreadsheets/d/{s.rules_sheet_id}" if s.rules_sheet_id else None
