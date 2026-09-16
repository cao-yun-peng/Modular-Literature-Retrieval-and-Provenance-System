"""Explicit opt-in bibliographic import, separate from the read-only Local API."""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.request
import uuid
from pathlib import Path


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Zotero Connector redirects are not allowed")


class ZoteroConnector:
    def __init__(
        self,
        *,
        allow_write=False,
        expected_target=None,
        state_path="data/state/research_zotero.sqlite3",
        opener=None,
    ):
        self.allow_write = allow_write
        self.expected_target = expected_target
        self.state_path = Path(state_path)
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    def _post(self, route, data, content_type="application/json"):
        request = urllib.request.Request(
            "http://127.0.0.1:23119" + route,
            data=data,
            headers={"Content-Type": content_type, "X-Zotero-Connector-API-Version": "3"},
        )
        with self.opener.open(request, timeout=60) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Connector response too large")
            return json.loads(raw) if raw else {}

    def selected_target(self):
        data = self._post("/connector/getSelectedCollection", b"{}")
        target = f"C{data['id']}" if data.get("id") else f"L{data['libraryID']}"
        return {
            "target": target,
            "name": data.get("name"),
            "editable": data.get("editable") is True and data.get("libraryEditable") is True,
        }

    def import_paper(self, paper):
        if self.allow_write is not True:
            raise PermissionError("Zotero import requires explicit allow_write")
        if not isinstance(self.expected_target, str) or not re.fullmatch(
            r"[LC]\d+", self.expected_target
        ):
            raise ValueError("Specify the selected Zotero target (L<number> or C<number>)")
        target = self.selected_target()
        if target["target"] != self.expected_target or not target["editable"]:
            raise PermissionError("Zotero selected target changed or is read-only")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        identity = re.sub(r"v\d+$", "", paper.arxiv_id)
        with sqlite3.connect(self.state_path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS imports (identity TEXT, target TEXT, session TEXT, status TEXT, PRIMARY KEY(identity,target))"
            )
            row = db.execute(
                "SELECT session,status FROM imports WHERE identity=? AND target=?",
                (identity, self.expected_target),
            ).fetchone()
            if row:
                if row[1] == "accepted":
                    return {
                        "status": "already_imported",
                        "session": row[0],
                        "target": self.expected_target,
                    }
                raise RuntimeError(
                    "Prior Zotero import is uncertain; reconcile in Zotero before retrying"
                )
            session = "research-" + uuid.uuid4().hex
            db.execute(
                "INSERT INTO imports VALUES (?,?,?,?)",
                (identity, self.expected_target, session, "pending"),
            )
        # No retries after an ambiguous network failure: preserve pending journal entry.
        imported = self._post(
            "/connector/import?session=" + session, paper.ris().encode("utf-8"), "text/plain"
        )
        if not isinstance(imported, list) or not imported:
            raise RuntimeError("Connector did not return imported records")
        with sqlite3.connect(self.state_path) as db:
            db.execute(
                "UPDATE imports SET status='accepted' WHERE identity=? AND target=?",
                (identity, self.expected_target),
            )
        return {
            "status": "import_accepted",
            "session": session,
            "target": self.expected_target,
            "attachment_status": "not_verified",
            "item_count": len(imported),
        }
