"""One-shot setup: seed the KB git repo, create demo chat users, register both
agents with Switch, and create the #kb-ops room with its instructions.
Idempotent: safe to run again."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
from pathlib import Path

import httpx

from common import KB_REPO, ROOM_FILE, STATE, SWITCH_URL
from kb_core import git
from switch_proto import SwitchAgent

log = logging.getLogger("bootstrap")
SEED = Path(os.environ.get("KB_SEED", "/app/kb-seed"))
MM = os.environ.get("MATTERMOST_URL", "http://mattermost:8065")
TEAM = os.environ.get("MATTERMOST_TEAM_NAME", "switch")
DEMO_USERS = [  # fictional personas, example data
    ("newhire", "New Hire", "(example employee)"),
    ("finance-lead", "Finance Lead", "(example owner of finance docs)"),
    ("support-lead", "Support Lead", "(example owner of support docs)"),
]
ROOM_INSTRUCTIONS = """This room is the team's knowledge-base desk (example data).
- Ask @kb-answerer anything about company policy. It answers only from the KB, with citations, and flags stale or conflicting docs.
- @kb-curator listens to the whole room. When a person corrects an answer, it drafts a fix as a git branch covering every affected doc, previews the new answer, and asks the doc owner to approve.
- Only a document's owner may `approve KB-n` / `reject KB-n`. Approved changes are merged, the index is rebuilt, and the decision is logged in the room document "KB decision log".
"""


def seed_kb() -> None:
    if (KB_REPO / ".git").exists():
        return
    KB_REPO.mkdir(parents=True, exist_ok=True)
    for item in SEED.iterdir():
        dst = KB_REPO / item.name
        shutil.copytree(item, dst) if item.is_dir() else shutil.copy(item, dst)
    git(KB_REPO, "init", "-q", "-b", "main")
    git(KB_REPO, "add", "-A")
    git(KB_REPO, "-c", "user.name=kb-seed", "-c", "user.email=kb-seed@example.invalid",
        "commit", "-q", "-m", "Seed example knowledge base")
    log.info("KB repo seeded at %s", KB_REPO)


def seed_mattermost() -> dict:
    """Create the demo personas; passwords are written to the state dir, never printed."""
    creds_file = STATE / "demo-users.json"
    creds = json.loads(creds_file.read_text()) if creds_file.exists() else {}
    c = httpx.Client(base_url=MM + "/api/v4", timeout=30)
    r = c.post("/users/login", json={"login_id": os.environ["MATTERMOST_ADMIN_USER"],
                                     "password": os.environ["MATTERMOST_ADMIN_PASSWORD"]})
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.headers["Token"]
    team = c.get(f"/teams/name/{TEAM}").json()
    for username, first, last in DEMO_USERS:
        u = c.get(f"/users/username/{username}")
        if u.status_code != 200:
            pw = creds.get(username) or ("Kb-" + secrets.token_urlsafe(12))
            u = c.post("/users", json={"username": username, "email": f"{username}@example.invalid",
                                       "password": pw, "first_name": first, "last_name": last})
            u.raise_for_status()
            creds[username] = pw
        uid = u.json()["id"]
        c.post(f"/teams/{team['id']}/members", json={"team_id": team["id"], "user_id": uid})
    creds_file.write_text(json.dumps(creds))
    os.chmod(creds_file, 0o600)
    return creds


def open_addressing(agent_ids: list[str]) -> None:
    """A new agent answers only its owner. As the Switch admin, open both agents to
    everyone in their rooms (PUT /gateway/agents/{id}/addressing-policy, policy=null).
    Who may *approve* KB changes is enforced separately, by the curator."""
    g = httpx.Client(base_url=SWITCH_URL + "/gateway", timeout=30)
    r = g.post("/auth/login", json={"email": os.environ["GATEWAY_ADMIN_EMAIL"],
                                    "password": os.environ["GATEWAY_ADMIN_PASSWORD"]})
    r.raise_for_status()
    for aid in agent_ids:
        r = g.put(f"/agents/{aid}/addressing-policy", json={"policy": None})
        log.info("addressing policy for %s -> %s", aid, r.status_code)
        r.raise_for_status()


async def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    seed_kb()
    seed_mattermost()
    tok = os.environ["AGENT_REGISTRATION_TOKEN"]
    answerer = SwitchAgent("kb-answerer", "KB Answerer", "Answers questions from the team knowledge base, with citations.", SWITCH_URL)
    curator = SwitchAgent("kb-curator", "KB Curator", "Drafts knowledge-base fixes as git branches and merges them after an owner approves.", SWITCH_URL)
    await answerer.ensure_registered(tok)
    await curator.ensure_registered(tok)
    open_addressing([answerer.agent_id, curator.agent_id])
    if ROOM_FILE.exists():
        log.info("room already exists: %s", ROOM_FILE.read_text())
        return
    users = [os.environ.get("MATTERMOST_USER", "user")] + [u for u, _, _ in DEMO_USERS]
    res = await answerer.op("create_room", _room=False, name="kb-ops",
                            description="Knowledge-base desk: ask, correct, approve. (example data)",
                            agent_names=["kb-answerer", "kb-curator"], user_names=users,
                            channel_type="channel_public", instructions=ROOM_INSTRUCTIONS)
    log.info("create_room -> %s", json.dumps(res)[:400])
    rid = res.get("room_id") or res.get("id") or res.get("room", {}).get("id")
    ROOM_FILE.write_text(json.dumps({"room_id": rid}))


if __name__ == "__main__":
    asyncio.run(main())
