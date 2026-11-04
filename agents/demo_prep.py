"""Pre-recording cleanup run by scripts/demo-start.sh (example data only).

  python demo_prep.py cards    # close every still-open Switch review card of this curator
  python demo_prep.py github   # GitHub mode: close open kb/* PRs, delete their branches and restore the
                               # demo repo main to the `demo-start` tag content with a normal commit
Never rewrites history and refuses repositories whose name does not end with -demo-docs.
"""
import json
import sys

import httpx

from common import STATE, SWITCH_URL
from docsource import make_source


def close_cards():
    st = json.loads((STATE / "kb-curator.json").read_text())
    rv = STATE / "reviews.json"
    if not rv.exists():
        return print("cards: none")
    rv = json.loads(rv.read_text())
    h = {"Authorization": "Bearer " + st["api_key"]}
    n = 0
    for r in rv.get("rows", []):
        if r.get("status") == "open" and r.get("request_id"):
            httpx.post(f"{SWITCH_URL}/agent-sessions/{rv['session_id']}/approvals/{r['request_id']}/close", headers=h, timeout=15)
            n += 1
    print(f"cards: closed {n}")


def reset_github():
    s = make_source()
    if s.name != "github":
        return print("github: local mode, nothing to do")
    if not s.repo.endswith("-demo-docs"):
        sys.exit("github: refusing, repository must end with -demo-docs")
    for pr in s.request("GET", "/pulls", params={"state": "open", "per_page": 100}):
        if pr["head"]["ref"].startswith("kb/"):
            s.request("PATCH", f"/pulls/{pr['number']}", json={"state": "closed"})
            try:
                s.client.delete(f"/repos/{s.repo}/git/refs/heads/{pr['head']['ref']}")
            except Exception:
                pass
            print(f"github: closed PR #{pr['number']}")
    tag = s.request("GET", "/git/ref/tags/demo-start")["object"]["sha"]
    tree = s.request("GET", f"/git/commits/{tag}")["tree"]["sha"]
    head = s.request("GET", f"/git/ref/heads/{s.base}")["object"]["sha"]
    if s.request("GET", f"/git/commits/{head}")["tree"]["sha"] == tree:
        return print("github: main already at demo-start content")
    new = s.request("POST", "/git/commits", json={"message": "Reset demo content to demo-start", "tree": tree, "parents": [head]})["sha"]
    s.request("PATCH", f"/git/refs/heads/{s.base}", json={"sha": new})
    print(f"github: restored main to demo-start content as {new[:7]}")


if __name__ == "__main__":
    {"cards": close_cards, "github": reset_github}[sys.argv[1]]()
