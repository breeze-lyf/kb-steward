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
KB_LANG = os.environ.get("KB_LANG", "both").lower()  # en | zh | both
ROOM_INSTRUCTIONS_EN = """This room is the team's knowledge-base desk (example data).
- Ask @kb-answerer anything about company policy. It answers only from the KB, with citations, and flags stale or conflicting docs.
- @kb-curator listens to the whole room. When a person corrects an answer, it drafts a fix as a git branch covering every affected doc, previews the new answer, and asks the doc owner to approve.
- Only a document's owner may `approve KB-n` / `reject KB-n`. Approved changes are merged, the index is rebuilt, and the decision is logged in the room document "KB decision log".
- Knowledge gaps (questions the KB can't answer) are collected in the room document "KB knowledge gaps". Say `@kb-curator stale report` for docs overdue for review.
"""
ROOM_INSTRUCTIONS_ZH = """本频道是团队的知识库服务台（示例数据）。
- 有制度问题直接 @kb-answerer。它只依据知识库作答，附出处、负责人和复核日期，并会标出过期或互相矛盾的文档。中文提问用中文答，英文提问用英文答。
- @kb-curator 监听整个频道。有人指出回答有误（例如“这个过时了，现在是 ¥120”），它会在独立 git 分支上修改所有受影响的文档，附影响分析和新回答预览，并请文档负责人审批。
- 只有文档负责人可以 `approve KB-n` / `reject KB-n`（也可以写“批准 KB-n”/“驳回 KB-n”）。批准后合并、重建索引，并记入频道文档“KB decision log”。
- 知识库答不上的问题会记入频道文档“KB knowledge gaps”。发送 `@kb-curator 复核报告` 查看超期未复核的文档。
"""
ROOM_INSTRUCTIONS = {"en": ROOM_INSTRUCTIONS_EN, "zh": ROOM_INSTRUCTIONS_ZH}.get(
    KB_LANG, ROOM_INSTRUCTIONS_EN + "\n" + ROOM_INSTRUCTIONS_ZH)


def seed_kb() -> None:
    if (KB_REPO / ".git").exists():
        return
    from docsource import make_source
    source = make_source()
    if source.name == 'github':
        source.clone()
        return
    KB_REPO.mkdir(parents=True, exist_ok=True)
    for item in SEED.iterdir():
        if KB_LANG == "en" and item.name == "zh":
            continue
        if KB_LANG == "zh" and item.name not in ("zh", "README.md"):
            continue
        dst = KB_REPO / item.name
        shutil.copytree(item, dst) if item.is_dir() else shutil.copy(item, dst)
    git(KB_REPO, "init", "-q", "-b", "main")
    git(KB_REPO, "add", "-A")
    git(KB_REPO, "-c", "user.name=kb-seed", "-c", "user.email=kb-seed@example.invalid",
        "commit", "-q", "-m", "Seed example knowledge base")
    log.info("KB repo seeded at %s (KB_LANG=%s)", KB_REPO, KB_LANG)


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
