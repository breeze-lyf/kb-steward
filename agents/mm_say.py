"""Rehearsal helper: post into the kb-ops channel as a demo persona, or dump it.

  python mm_say.py newhire "@kb-answerer what's the meal allowance?"
  python mm_say.py newhire "reply text" --reply-last   # reply in the latest thread
  python mm_say.py --dump [N]
Demo personas only (example data). Passwords live in /data/state/demo-users.json.
"""
import json, os, sys
from pathlib import Path
import httpx

MM = os.environ.get("MATTERMOST_URL", "http://mattermost:8065") + "/api/v4"
TEAM = os.environ.get("MATTERMOST_TEAM_NAME", "switch")
STATE = Path(os.environ.get("STATE_DIR", "/data/state"))


def login(user: str) -> httpx.Client:
    creds = json.loads((STATE / "demo-users.json").read_text())
    if user in creds:
        pw = creds[user]
    elif user == os.environ.get("MATTERMOST_ADMIN_USER"):
        pw = os.environ["MATTERMOST_ADMIN_PASSWORD"]
    elif user == os.environ.get("MATTERMOST_USER"):
        pw = os.environ["MATTERMOST_USER_PASSWORD"]
    else:
        sys.exit(f"unknown persona {user}")
    c = httpx.Client(base_url=MM, timeout=20)
    r = c.post("/users/login", json={"login_id": user, "password": pw}); r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.headers["Token"]
    return c


def channel(c):
    for ch in c.get("/users/me/channels").json():
        if ch["display_name"] == "kb-ops" or ch["name"].startswith("kb-ops"):
            return ch["id"]
    sys.exit("kb-ops channel not found for this user")


def main():
    a = sys.argv[1:]
    if a and a[0] == "--dump":
        c = login(os.environ.get("MATTERMOST_ADMIN_USER")); ch = channel(c)
        p = c.get(f"/channels/{ch}/posts", params={"per_page": int(a[1]) if len(a) > 1 else 30}).json()
        names = {}
        for pid in reversed(p["order"]):
            post = p["posts"][pid]; u = post["user_id"]
            if u not in names: names[u] = c.get(f"/users/{u}").json().get("username")
            t = " ↳" if post.get("root_id") else ""
            print(f"---{t} [{names[u]}] {pid[:6]}\n{post['message']}")
        return
    user, text = a[0], a[1]
    c = login(user); ch = channel(c)
    body = {"channel_id": ch, "message": text}
    if "--reply-last" in a:
        p = c.get(f"/channels/{ch}/posts", params={"per_page": 1}).json()
        last = p["posts"][p["order"][0]]
        body["root_id"] = last.get("root_id") or last["id"]
    r = c.post("/posts", json=body); r.raise_for_status(); print("posted", r.json()["id"][:6])


main()
