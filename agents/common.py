from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SWITCH_URL = os.environ.get("SWITCH_URL", "http://switch:8000")
KB_REPO = Path(os.environ.get("KB_REPO", "/data/kb"))
STATE = Path(os.environ.get("KBS_STATE_DIR", "/data/state"))
ROOM_FILE = STATE / "room.json"
AGENT_NAMES = {"kb-answerer", "kb-curator"}
MENTION = re.compile(r"@[\w.\-]+")


def room_id() -> str:
    return json.loads(ROOM_FILE.read_text())["room_id"]


def strip_mentions(body: str) -> str:
    return MENTION.sub("", body or "").strip(" ,:\n")


def approvers() -> dict[str, set[str]]:
    """APPROVERS="finance:finance-lead;support:support-lead" -> {owner: {usernames}}"""
    out: dict[str, set[str]] = {}
    for part in os.environ.get("APPROVERS", "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = {x.strip().lower() for x in v.split(",") if x.strip()}
    return out


def sender_handle(payload: dict) -> str:
    """Best-effort username for a human sender (Mattermost username or display name)."""
    return (payload.get("sender_name") or "").strip().lower()


def is_agent(payload: dict) -> bool:
    s = (payload.get("sender") or "").lower()
    n = sender_handle(payload)
    return "switch-agent" in s or n in AGENT_NAMES or "kb answerer" in n or "kb curator" in n or "switch-admin" in s or "switch-bridge" in s


def default_owner(owners=None) -> str:
    """Owner for items nobody claimed yet (e.g. knowledge gaps): KB_DEFAULT_OWNER, else the first APPROVERS key."""
    owners = approvers() if owners is None else owners
    o = os.environ.get("KB_DEFAULT_OWNER", "").strip()
    return o if o in owners else next(iter(owners), "")
