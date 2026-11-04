"""Rehearsal helper: press a button on a Switch card as a demo persona (what a click in Mattermost does).

  python mm_press.py finance-lead <post-id-prefix> <action-id>      # e.g. switch1 = first option
Demo personas only; this is a human-side demo driver, never used by the agents.
"""
import sys; args=sys.argv[1:]; sys.argv=["x"]; exec(open("/app/agents/mm_say.py").read().replace("\nmain()\n","\n"))
user, short, action = args
c=login(user); ch=channel(c)
p=c.get(f"/channels/{ch}/posts",params={"per_page":30}).json()
pid=next(x for x in p["order"] if x.startswith(short))
r=c.post(f"/posts/{pid}/actions/{action}",json={}); print(r.status_code, r.text[:300])
