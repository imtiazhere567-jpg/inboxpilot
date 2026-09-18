"""Settings that can be changed from the dashboard (Gmail, Slack, HubSpot, QuickBooks, Anthropic, rules sheet, mode).

Values are stored encrypted in `app_settings` (Fernet, key derived from SETTINGS_SECRET or RESET_TOKEN) and applied
on top of the environment at startup and after every save. An empty value removes the override (back to .env).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.config import Settings, get_settings
from agent.models import AppSetting

log = logging.getLogger("ops_agent.settings")

# Settings fields a user may override from the UI, grouped for the page. (label, secret?)
FIELDS: dict[str, list[tuple[str, str, bool]]] = {
    "mode": [("app_mode", "Mode: demo (seeded inbox, auto reset) or live (your real inbox, no reset)", False),
             ("presentation_mode", "Presentation mode: hide demo / simulated / model badges (true or false)", False)],
    "company": [("company_name", "Company name", False), ("company_email", "Operations inbox address", False), ("company_initials", "Initials for the sidebar mark", False)],
    "anthropic": [("anthropic_api_key", "Anthropic API key", True), ("claude_model_main", "Main model", False)],
    "gmail": [("gmail_address", "Gmail address", False), ("gmail_app_password", "Gmail app password", True),
              ("gmail_label_inbox", "Folder to watch", False)],
    "slack": [("slack_bot_token", "Slack bot token (xoxb-…)", True), ("slack_channel", "Channel", False)],
    "hubspot": [("hubspot_access_token", "HubSpot private app token", True)],
    "qbo": [("qbo_client_id", "Client id", True), ("qbo_client_secret", "Client secret", True),
            ("qbo_refresh_token", "Refresh token", True), ("qbo_realm_id", "Realm (company) id", False)],
    "rules_sheet": [("rules_sheet_id", "Google Sheet id", False), ("google_service_account_json", "Service-account JSON (paste the file contents)", True)],
}
ALLOWED = {f for group in FIELDS.values() for f, _, _ in group}
SECRET = {f for group in FIELDS.values() for f, _, is_secret in group if is_secret}


def _fernet() -> Fernet:
    s = get_settings()
    seed = (s.settings_secret or s.reset_token or "ops-agent-dev").encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed).digest()))


def _enc(v: str) -> str:
    return _fernet().encrypt(v.encode()).decode()


def _dec(v: str) -> str:
    try:
        return _fernet().decrypt(v.encode()).decode()
    except InvalidToken:
        log.warning("settings: cannot decrypt a stored value (secret changed?) — ignoring it")
        return ""


def load_overrides(session: Session) -> dict[str, str]:
    out = {}
    for row in session.scalars(select(AppSetting)):
        if row.key in ALLOWED:
            val = _dec(row.value_enc)
            if val:
                out[row.key] = val
    return out


def apply_overrides(session: Session) -> dict[str, str]:
    """Copy DB overrides onto the cached Settings object so every reader sees them."""
    s = get_settings()
    env_defaults = getattr(s, "_env_snapshot", None)
    if env_defaults is None:
        env_defaults = {k: getattr(s, k) for k in ALLOWED}
        object.__setattr__(s, "_env_snapshot", env_defaults)
    overrides = load_overrides(session)
    for k in ALLOWED:
        val = overrides.get(k, env_defaults[k])
        if k == "presentation_mode" and isinstance(val, str):
            val = val.lower() in ("true", "1", "yes", "on")
        setattr(s, k, val)
    return overrides


def save_settings(session: Session, values: dict[str, Any]) -> list[str]:
    """Upsert (encrypted). Empty string deletes the override. Returns the keys changed."""
    changed = []
    for k, v in values.items():
        if k not in ALLOWED:
            continue
        v = "" if v is None else str(v).strip()
        if v == "••••••••":  # masked placeholder echoed back by the form — unchanged
            continue
        row = session.get(AppSetting, k)
        if v == "":
            if row is not None:
                session.delete(row)
                changed.append(k)
            continue
        if k == "app_mode" and v not in ("demo", "live"):
            raise ValueError("app_mode must be demo or live")
        if k == "presentation_mode":
            v = "true" if v.lower() in ("true", "1", "yes", "on") else "false"
        if k == "google_service_account_json" and v.startswith("{"):
            json.loads(v)  # validate
        if row is None:
            row = AppSetting(key=k, value_enc=_enc(v))
        else:
            row.value_enc = _enc(v)
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        changed.append(k)
    session.flush()
    apply_overrides(session)
    return changed


def masked_view(session: Session) -> dict[str, Any]:
    s = get_settings()
    overrides = load_overrides(session)
    env_defaults = getattr(s, "_env_snapshot", {}) or {}
    groups = {}
    for group, fields in FIELDS.items():
        rows = []
        for key, label, is_secret in fields:
            val = getattr(s, key, "") or ""
            source = "settings" if key in overrides else ("env" if env_defaults.get(key) else "")
            shown = ("••••••••" if is_secret else str(val)) if val else ""
            rows.append({"key": key, "label": label, "secret": is_secret, "set": bool(val), "source": source, "value": shown,
                         "hint": (f"…{str(val)[-4:]}" if is_secret and val and len(str(val)) > 8 else "")})
        groups[group] = rows
    return {"groups": groups, "configured": s.integrations_configured, "mode": s.app_mode, "presentation": bool(s.presentation_mode)}


# --- connection tests ---------------------------------------------------------------------------------------------

def test_connection(system: str) -> tuple[bool, str]:
    s = get_settings()
    try:
        if system == "anthropic":
            if not s.anthropic_api_key:
                return False, "no API key"
            import anthropic

            c = anthropic.Anthropic(api_key=s.anthropic_api_key)
            r = c.messages.create(model=s.claude_model_main, max_tokens=8, messages=[{"role": "user", "content": "Reply with OK"}])
            return True, f"{s.claude_model_main} answered ({r.usage.input_tokens}+{r.usage.output_tokens} tokens)"
        if system == "gmail":
            if not (s.gmail_address and s.gmail_app_password):
                return False, "address or app password missing"
            from imap_tools import MailBox

            with MailBox(s.gmail_imap_host).login(s.gmail_address, s.gmail_app_password, initial_folder=s.gmail_label_inbox) as mb:
                n = len(list(mb.uids()))
            return True, f"IMAP login ok · {n} message(s) in {s.gmail_label_inbox}"
        if system == "slack":
            if not s.slack_bot_token:
                return False, "no bot token"
            from slack_sdk import WebClient

            r = WebClient(token=s.slack_bot_token).auth_test()
            return True, f"bot {r.get('user')} in workspace {r.get('team')} · posting to {s.slack_channel}"
        if system == "hubspot":
            if not s.hubspot_access_token:
                return False, "no token"
            from hubspot import HubSpot

            r = HubSpot(access_token=s.hubspot_access_token).crm.companies.basic_api.get_page(limit=1)
            return True, f"connected · {len(r.results)} company visible on first page"
        if system == "qbo":
            from agent.integrations.qbo import QBOClient

            c = QBOClient()
            if not c.configured:
                return False, "client id / secret / refresh token / realm id incomplete"
            from quickbooks.objects.company_info import CompanyInfo

            info = CompanyInfo.all(qb=c._qb)
            return True, f"sandbox company: {info[0].CompanyName if info else '?'}"
        if system == "rules_sheet":
            from agent.integrations.sheets import read_rules_sheet

            rules = read_rules_sheet()
            return True, f"sheet read ok · {len(rules)} rule(s)"
        return False, f"unknown system {system}"
    except Exception as exc:  # noqa: BLE001 — surface the real error text to the page
        return False, f"{type(exc).__name__}: {str(exc)[:300]}"
