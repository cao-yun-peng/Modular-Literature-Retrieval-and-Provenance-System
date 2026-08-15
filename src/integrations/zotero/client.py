"""Minimal read-only client for Zotero Desktop Local API v3."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import Request, url2pathname, urlopen

from src.integrations.zotero.models import SourceAttachment, SourceDocument


class ZoteroClientError(RuntimeError):
    """Base error for the read-only Zotero client."""


class ZoteroUnavailableError(ZoteroClientError):
    """Raised when Zotero Local API cannot be reached."""


class ZoteroApiError(ZoteroClientError):
    """Raised when Zotero Local API returns an invalid or failing response."""


class ZoteroLocalClient:
    """Read items and PDF attachment locations from Zotero Desktop Local API.

    The client deliberately exposes no write operation. It targets the local
    desktop user's Web API v3 namespace at ``/api/users/0`` and keeps all
    endpoint construction in one module so MCP/query code never depends on
    Zotero protocol details.
    """

    _USER_PATH = "/api/users/0"
    _PAGE_LIMIT = 100
    _SAFE_HOSTS = {"127.0.0.1", "localhost", "::1"}

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:23119",
        timeout: float = 10.0,
        allowed_attachment_roots: tuple[str | Path, ...] = (),
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in self._SAFE_HOSTS:
            raise ValueError(
                "Zotero Local API base_url must use http and a loopback host"
            )
        if timeout <= 0:
            raise ValueError("Zotero Local API timeout must be greater than zero")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.allowed_attachment_roots = tuple(
            Path(root).expanduser().resolve() for root in allowed_attachment_roots
        )

    def list_documents(self, collection_key: str | None = None) -> list[SourceDocument]:
        """Return one indexable SourceDocument per PDF attachment.

        A Zotero parent item may own several attachments. Non-PDF attachments
        are intentionally ignored because the current ingestion pipeline is
        PDF-first and must not receive unsupported local files.
        """
        documents: list[SourceDocument] = []
        for item in self.list_top_level_items(collection_key):
            item_data = self._data(item)
            item_key = self._item_key(item)
            if not item_key or item_data.get("itemType") in {"attachment", "note"}:
                continue
            for child in self.list_children(item_key):
                child_data = self._data(child)
                if child_data.get("itemType") != "attachment":
                    continue
                if not self._is_pdf_attachment(child_data):
                    continue
                attachment_key = self._item_key(child)
                if not attachment_key:
                    continue
                local_path = self.get_attachment_file_path(attachment_key)
                documents.append(
                    SourceDocument(
                        item_key=item_key,
                        attachment=SourceAttachment(
                            key=attachment_key,
                            local_path=local_path,
                            version=str(child.get("version", "")),
                            content_type=str(child_data.get("contentType", "application/pdf")),
                        ),
                        title=str(item_data.get("title", "")),
                        creators=tuple(self._creators(item_data)),
                        year=self._year(str(item_data.get("date", ""))),
                        doi=str(item_data.get("DOI", "")),
                        collection_keys=tuple(str(v) for v in item_data.get("collections", []) if v),
                        tags=tuple(self._tags(item_data)),
                        item_version=str(item.get("version", "")),
                        citation_key=(
                            str(item_data.get("citationKey"))
                            if item_data.get("citationKey")
                            else None
                        ),
                        extra_metadata={
                            "zotero_item_type": str(item_data.get("itemType", ""))
                        },
                    )
                )
        return documents

    @staticmethod
    def get_attachment(document: SourceDocument) -> SourceAttachment:
        """Return the already-resolved read-only attachment contract."""
        return document.attachment

    @staticmethod
    def get_version(document: SourceDocument) -> str:
        """Return the source version used by the sync state comparator."""
        return f"{document.item_version}:{document.attachment.version}"

    def list_top_level_items(self, collection_key: str | None = None) -> list[dict[str, Any]]:
        """List regular Zotero items with stable pagination."""
        parameters: dict[str, Any] = {"limit": self._PAGE_LIMIT, "sort": "title", "direction": "asc"}
        path = f"{self._USER_PATH}/items/top"
        if collection_key:
            path = (
                f"{self._USER_PATH}/collections/"
                f"{quote(collection_key, safe='')}/items/top"
            )
        return self._list_paginated(path, parameters)

    def list_children(self, item_key: str) -> list[dict[str, Any]]:
        """List children of one regular Zotero item."""
        return self._list_paginated(
            f"{self._USER_PATH}/items/{quote(item_key, safe='')}/children",
            {"limit": self._PAGE_LIMIT},
        )

    def get_attachment_file_path(self, attachment_key: str) -> Path:
        """Resolve a Zotero attachment's local file URL to a filesystem path."""
        value, _ = self._get_text(
            f"{self._USER_PATH}/items/{quote(attachment_key, safe='')}/file/view/url"
        )
        if not value:
            raise ZoteroApiError(
                f"Zotero attachment {attachment_key} did not return a file URL"
            )
        parsed = urlsplit(value)
        if parsed.scheme != "file":
            raise ZoteroApiError(
                f"Zotero attachment {attachment_key} returned a non-file URL"
            )
        local_path = Path(url2pathname(unquote(parsed.path)))
        if not local_path.is_file():
            raise ZoteroApiError(
                f"Zotero attachment {attachment_key} points to an unavailable file: {local_path}"
            )
        resolved = local_path.resolve()
        if self.allowed_attachment_roots and not any(
            resolved.is_relative_to(root) for root in self.allowed_attachment_roots
        ):
            raise ZoteroApiError(
                f"Zotero attachment {attachment_key} is outside allowed attachment roots"
            )
        return resolved

    def _list_paginated(self, path: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            page_parameters = {**parameters, "start": start}
            payload, headers = self._get_json(path, page_parameters)
            if not isinstance(payload, list):
                raise ZoteroApiError(f"Expected a JSON list from {path}")
            rows.extend(item for item in payload if isinstance(item, dict))
            total = self._total_results(headers)
            if not payload or len(payload) < int(parameters["limit"]):
                break
            start += len(payload)
            if total is not None and start >= total:
                break
        return rows

    def _get_json(
        self, path: str, parameters: dict[str, Any] | None = None
    ) -> tuple[Any, dict[str, str]]:
        body, headers = self._get_text(path, parameters)
        try:
            return json.loads(body), headers
        except json.JSONDecodeError as exc:
            raise ZoteroApiError(f"Zotero Local API returned invalid JSON for {path}") from exc

    def _get_text(
        self, path: str, parameters: dict[str, Any] | None = None
    ) -> tuple[str, dict[str, str]]:
        """Return a decoded Local API response without assuming JSON content."""
        query = f"?{urlencode(parameters)}" if parameters else ""
        url = f"{self.base_url}{path}{query}"
        request = Request(url, headers={"Zotero-API-Version": "3"}, method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                headers = {key.lower(): value for key, value in response.headers.items()}
        except HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise ZoteroApiError(
                f"Zotero Local API GET {path} failed with HTTP {exc.code}: {message[:300]}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ZoteroUnavailableError(
                "Cannot reach Zotero Local API. Ensure Zotero is running and Local API is enabled."
            ) from exc
        return body.strip(), headers

    @staticmethod
    def _data(item: dict[str, Any]) -> dict[str, Any]:
        data = item.get("data", item)
        return data if isinstance(data, dict) else {}

    @classmethod
    def _item_key(cls, item: dict[str, Any]) -> str:
        data = cls._data(item)
        value = item.get("key") or data.get("key")
        return str(value) if value else ""

    @staticmethod
    def _is_pdf_attachment(data: dict[str, Any]) -> bool:
        content_type = str(data.get("contentType", "")).lower()
        filename = str(data.get("filename", "")).lower()
        return content_type == "application/pdf" or filename.endswith(".pdf")

    @staticmethod
    def _creators(data: dict[str, Any]) -> list[str]:
        creators = data.get("creators", [])
        if not isinstance(creators, list):
            return []
        values: list[str] = []
        for creator in creators:
            if not isinstance(creator, dict):
                continue
            name = " ".join(
                part for part in (str(creator.get("firstName", "")).strip(), str(creator.get("lastName", "")).strip()) if part
            ) or str(creator.get("name", "")).strip()
            if name:
                values.append(name)
        return values

    @staticmethod
    def _tags(data: dict[str, Any]) -> list[str]:
        tags = data.get("tags", [])
        if not isinstance(tags, list):
            return []
        return [str(tag.get("tag", "")).strip() for tag in tags if isinstance(tag, dict) and str(tag.get("tag", "")).strip()]

    @staticmethod
    def _year(date_value: str) -> str:
        match = re.search(r"\b(\d{4})\b", date_value)
        return match.group(1) if match else ""

    @staticmethod
    def _total_results(headers: dict[str, str]) -> int | None:
        value = headers.get("total-results")
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None
