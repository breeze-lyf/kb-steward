"""Minimal client for the Switch agent protocol (WebSocket in, HTTP out).

Implements the wire contract documented at
https://docs.switchagents.ai/switch-rooms/internals/agent-protocol :
register once, keep the API key, open /agents/{id}/connection/ws, answer every
ping with a pong carrying our cursor, act on `message` events, and call
operations over HTTP with the X-Switch-Connection-Id header.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import websockets

log = logging.getLogger("switch")

STATE_DIR = Path(os.environ.get("KBS_STATE_DIR", "/data/state"))


def _profile(model: str = "always_on") -> dict:
    return {
        "connection_model": model,
        "message_exchange": True,
        "pre_invocation_mediation": [],
        "post_invocation_mediation": [],
        "event_reporting": [],
    }


def parse_answered_by(s: str) -> str:
    import re
    return re.sub(r"^switch-mattermost-.{36}-", "", s.lstrip("@").split(":", 1)[0]).lower()


class SwitchAgent:
    """One registered Switch agent with a persistent identity on disk."""

    def __init__(self, name: str, display_name: str, description: str, base_url: str):
        self.name = name
        self.display_name = display_name
        self.description = description
        self.base = base_url.rstrip("/")
        self.http = httpx.AsyncClient(timeout=60)
        self.agent_id: str = ""
        self.api_key: str = ""
        self.connection_id: str = ""
        self.cursor: int | None = None
        self.generation: int | None = None
        self.rooms: list[str] = []
        self._started_sessions: set[str] = set()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_file = STATE_DIR / f"{name}.json"

    # ---------- identity ----------
    async def ensure_registered(self, registration_token: str) -> None:
        if self._state_file.exists():
            st = json.loads(self._state_file.read_text())
            self.agent_id, self.api_key = st["agent_id"], st["api_key"]
            self.connection_id = st.get("connection_id") or str(uuid.uuid4())
            r = await self.http.get(f"{self.base}/agents/{self.agent_id}/ops", headers=self._h())
            if r.status_code == 200:
                self._save()
                return
            log.warning("%s: stored key rejected (%s); re-registering", self.name, r.status_code)
        r = await self.http.post(
            f"{self.base}/agents",
            headers={"Authorization": f"Bearer {registration_token}"},
            json={
                "name": self.name,
                "display_name": self.display_name,
                "description": self.description,
                "connector_type": "kb-steward",
                "integration_profile": _profile(),
                "overwrite": True,
            },
        )
        r.raise_for_status()
        d = r.json()
        self.agent_id, self.api_key = d["id"], d["api_key"]
        self.connection_id = str(uuid.uuid4())
        self._save()
        log.info("%s registered as %s", self.name, self.agent_id)

    def _save(self) -> None:
        self._state_file.write_text(json.dumps(
            {"agent_id": self.agent_id, "api_key": self.api_key, "connection_id": self.connection_id}))
        os.chmod(self._state_file, 0o600)

    def _h(self, with_conn: bool = False) -> dict:
        h = {"Authorization": f"Bearer {self.api_key}"}
        if with_conn and self.connection_id:
            h["X-Switch-Connection-Id"] = self.connection_id
        return h

    # ---------- operations ----------
    async def op(self, _op: str, _room: bool = True, **args: Any) -> Any:
        r = await self.http.post(f"{self.base}/agents/{self.agent_id}/ops/{_op}",
                                 headers=self._h(with_conn=_room), json=args)
        if r.status_code >= 400:
            raise RuntimeError(f"op {_op} -> {r.status_code}: {r.text[:400]}")
        return r.json().get("result")

    async def post(self, room_id: str, body: str) -> Any:
        """Post to a room without depending on the socket (POST /agents/{id}/message)."""
        r = await self.http.post(f"{self.base}/agents/{self.agent_id}/message",
                                 headers=self._h(), json={"room_id": room_id, "content": body})
        if r.status_code >= 400:
            raise RuntimeError(f"post -> {r.status_code}: {r.text[:300]}")
        return r.json()

    async def typing(self, room_id: str, on: bool = True) -> None:
        try:
            await self.http.post(f"{self.base}/agents/{self.agent_id}/typing",
                                 headers=self._h(), json={"room_id": room_id, "is_typing": on})
        except httpx.HTTPError:
            pass

    async def reply(self, body: str, thread_id: str | None = None) -> Any:
        return await self.op("post_message", body=body, thread_id=thread_id)

    async def open_card(self, session_id, request_id, title, detail, question_id, prompt, options, room_id, thread_id):
        uuid.UUID(session_id)
        if session_id not in self._started_sessions:
            self._started_sessions.add(session_id)
            try:
                await self.http.post(f"{self.base}/agent-sessions/{session_id}/started", headers=self._h(), json={"start_source": "automation"})
            except Exception:
                pass
        r = await self.http.post(f"{self.base}/agent-sessions/{session_id}/approvals", headers=self._h(), json={
            "request_id": request_id, "turn_id": "review", "kind": "questions", "title": title, "detail": detail,
            "options": [], "questions": [{"id": question_id, "title": "Review", "prompt": prompt,
            "multi_select": False, "allow_custom_answer": False,
            "options": [{"id": i, "label": label, "description": None} for i, label in options]}],
            "room_id": room_id, "thread_id": thread_id, "expires_at": None})
        r.raise_for_status()

    async def card_outcomes(self):
        r = await self.http.get(f"{self.base}/agent-sessions/approvals/outcomes", headers=self._h())
        r.raise_for_status()
        j = r.json()
        return j if isinstance(j, list) else j.get("outcomes") or j.get("items") or []

    async def card_delivered(self, session_id, request_id):
        r = await self.http.post(f"{self.base}/agent-sessions/{session_id}/approvals/{request_id}/delivered", headers=self._h())
        r.raise_for_status()

    async def card_close(self, session_id, request_id):
        r = await self.http.post(f"{self.base}/agent-sessions/{session_id}/approvals/{request_id}/close", headers=self._h())
        r.raise_for_status()

    # ---------- event loop ----------
    async def run(self, rooms: list[str], on_message: Callable[[dict, dict], Awaitable[None]]) -> None:
        """Open the socket and dispatch message events forever, reconnecting on drops."""
        backoff = 1
        while True:
            q = f"connection_id={self.connection_id}&scope=single&filter=all&client=kb-steward&client_version=0.1"
            if rooms:
                q += "&rooms=" + ",".join(rooms)
            if self.cursor is not None:
                q += f"&start_from={self.cursor}"
            if self.generation is not None:
                q += f"&expected_generation={self.generation}"
            url = self.base.replace("http", "ws", 1) + f"/agents/{self.agent_id}/connection/ws?{q}"
            try:
                async with websockets.connect(url, additional_headers=self._h(),
                                              ping_interval=None, max_size=2**24) as ws:
                    backoff = 1
                    async for raw in ws:
                        frame = json.loads(raw)
                        kind = frame.get("type") or frame.get("event")
                        if kind == "ping":
                            await ws.send(json.dumps({"type": "pong", "cursor": self.cursor or 0}))
                            continue
                        if kind == "connection_state":
                            self.generation = frame.get("generation")
                            self.rooms = frame.get("rooms", [])
                            if self.cursor is None:
                                self.cursor = frame.get("cursor")
                            log.info("%s connected, rooms=%s", self.name, self.rooms)
                            missing = [r for r in rooms if r not in self.rooms]
                            if missing:
                                asyncio.create_task(self._claim(missing[0]))
                            continue
                        if kind == "evicted":
                            log.warning("%s evicted: %s", self.name, frame)
                            if frame.get("code") == "taken_over":
                                return
                            break
                        if kind == "gap":
                            self.cursor = frame.get("resumed_at", self.cursor)
                            continue
                        if frame.get("event") == "message":
                            data = frame["data"]
                            self.cursor = frame.get("id", self.cursor)
                            # Never block pings behind model calls.
                            asyncio.create_task(self._safe(on_message, data, data.get("payload", {})))
                        elif "id" in frame:
                            self.cursor = frame["id"]
            except (OSError, websockets.ConnectionClosed, websockets.InvalidStatus) as e:
                log.warning("%s socket dropped: %s", self.name, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _claim(self, room: str) -> None:
        try:
            ctx = await self.op("connect_to_room", room_id=room, include_general_instructions=False)
            self.rooms = [room]
            log.info("%s claimed room %s (%s)", self.name, room, (ctx or {}).get("name", ""))
        except Exception as e:  # noqa: BLE001
            log.error("%s could not claim %s: %s", self.name, room, e)

    async def _safe(self, fn, data, payload):
        try:
            await fn(data, payload)
        except Exception as e:
            log.exception("%s handler failed", self.name)
            if type(e).__name__ == "LLMError" and payload.get("addressed"):
                try:
                    await self.reply(f"⚠️ I couldn't think about that just now: {e}. Nothing was changed.",
                                     payload.get("thread_id") or payload.get("message_id"))
                except Exception:  # noqa: BLE001
                    pass
