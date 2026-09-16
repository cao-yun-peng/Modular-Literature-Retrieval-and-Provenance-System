"""Token-free MinerU Agent API with content-keyed local caching.

PDFs are uploaded to MinerU. Failed requests are never replaced with local text.
Only Markdown is supplied by this API; page coordinates and image files are not.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx


class MinerUAgentClient:
    base_url = "https://mineru.net/api/v1/agent/parse"
    request_max_bytes = 10 * 1024 * 1024
    request_max_pages = 20
    document_max_bytes = 50 * 1024 * 1024
    document_max_pages = 200

    def __init__(self, cache_dir="data/mineru-agent", timeout=600, poll_interval=8):
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.poll_interval = poll_interval

    @staticmethod
    def _data(response):
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"MinerU {payload.get('code')}: {payload.get('msg')}")
        return payload["data"]

    @staticmethod
    def _https(url):
        if not isinstance(url, str) or urlparse(url).scheme != "https":
            raise ValueError("MinerU returned a non-HTTPS resource URL")
        return url

    def prepare_parts(self, path: Path) -> list[dict]:
        """Make lossless request-sized copies, retaining original page ranges.

        The original PDF is never modified. Every physical page occurs once.
        This method performs no network requests.
        """
        import fitz

        path = Path(path)
        if path.stat().st_size > self.document_max_bytes:
            raise ValueError("PDF exceeds the 50 MB document limit")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with fitz.open(path) as pdf:
            pages = len(pdf)
            if pdf.needs_pass or not 1 <= pages <= self.document_max_pages:
                raise ValueError("Expected an unencrypted PDF of 1-200 pages")
            if pages <= self.request_max_pages and path.stat().st_size <= self.request_max_bytes:
                return [dict(path=str(path.resolve()), sha256=digest, page_start=1,
                             page_end=pages, bytes=path.stat().st_size)]
            folder = self.cache_dir / digest / "parts"
            folder.mkdir(parents=True, exist_ok=True)
            parts = []

            def add(first, last):
                with fitz.open() as part:
                    part.insert_pdf(pdf, from_page=first, to_page=last)
                    content = part.tobytes(garbage=4, deflate=True, no_new_id=True)
                if len(content) > self.request_max_bytes:
                    if first == last:
                        raise ValueError(f"PDF page {first + 1} alone exceeds MinerU's 10 MB limit")
                    middle = (first + last) // 2
                    add(first, middle)
                    add(middle + 1, last)
                    return
                target = folder / f"pages-{first + 1:04d}-{last + 1:04d}.pdf"
                if not target.exists() or target.read_bytes() != content:
                    target.write_bytes(content)
                parts.append(dict(path=str(target.resolve()), sha256=hashlib.sha256(content).hexdigest(),
                                  page_start=first + 1, page_end=last + 1, bytes=len(content)))

            for first in range(0, pages, self.request_max_pages):
                add(first, min(pages - 1, first + self.request_max_pages - 1))
        (folder.parent / "parts.json").write_text(json.dumps(
            {"source_sha256": digest, "page_count": pages, "parts": parts}, indent=2), encoding="utf-8")
        return parts

    def parse(self, path: Path) -> tuple[str, dict]:
        path = Path(path)
        parts = self.prepare_parts(path)
        if len(parts) > 1 or Path(parts[0]["path"]) != path.resolve():
            return self._parse_parts(path, parts)
        return self._parse_single(path)

    def _parse_parts(self, path, parts):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        folder = self.cache_dir / digest
        md_path, task_path = folder / "paper.md", folder / "task.json"
        pieces, records = [], []
        for part in parts:
            markdown, task = self._parse_single(Path(part["path"]))
            pieces.append(markdown.strip())
            records.append({**part, "task_id": task["task_id"], "cache_hit": task["cache_hit"],
                            "markdown_path": task["markdown_path"]})
        markdown = "\n\n".join(pieces) + "\n"
        temporary = md_path.with_suffix(".tmp")
        temporary.write_text(markdown, encoding="utf-8")
        temporary.replace(md_path)
        task = dict(task_id=None, task_ids=[p["task_id"] for p in records], state="done",
                    sha256=digest, page_count=parts[-1]["page_end"], parser="mineru_agent",
                    parse_mode="page_segments_v1", parts=records,
                    cache_hit=all(p["cache_hit"] for p in records), markdown_path=str(md_path))
        task_path.write_text(json.dumps(task, indent=2), encoding="utf-8")
        return markdown, task

    def _parse_single(self, path: Path) -> tuple[str, dict]:
        import fitz

        with fitz.open(path) as pdf:
            pages = len(pdf)
        if pages > self.request_max_pages or path.stat().st_size > self.request_max_bytes:
            raise ValueError("MinerU request exceeds 20 pages or 10 MB")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        folder = self.cache_dir / digest
        folder.mkdir(parents=True, exist_ok=True)
        md_path, task_path = folder / "paper.md", folder / "task.json"
        task = json.loads(task_path.read_text(encoding="utf-8")) if task_path.exists() else {}
        if md_path.exists() and task.get("state") == "done":
            markdown = md_path.read_text(encoding="utf-8")
            if markdown.strip():
                return markdown, {**task, "cache_hit": True, "markdown_path": str(md_path)}
        if task.get("state") == "failed":
            raise RuntimeError(f"MinerU task {task['task_id']} failed: {task.get('error')}")
        start = time.monotonic()
        # No .netrc credentials, API keys or implicit proxy credentials.
        with httpx.Client(timeout=60, trust_env=False, follow_redirects=True) as client:
            if not task:
                data = self._data(client.post(self.base_url + "/file", json={
                    "file_name": path.name, "language": "en", "is_ocr": False,
                    "enable_formula": True, "enable_table": True,
                }))
                task = {"task_id": data["task_id"], "state": "created", "sha256": digest,
                        "page_count": pages, "parser": "mineru_agent"}
                task_path.write_text(json.dumps(task), encoding="utf-8")
                with path.open("rb") as stream:
                    uploaded = client.put(self._https(data["file_url"]), content=stream, timeout=120)
                if uploaded.status_code not in (200, 201):
                    raise RuntimeError(f"MinerU upload failed: HTTP {uploaded.status_code}")
                task["state"] = "uploaded"
                task_path.write_text(json.dumps(task), encoding="utf-8")
            elif task.get("state") == "created":
                raise RuntimeError(f"Upload outcome uncertain for MinerU task {task['task_id']}; inspect before resubmitting")
            while time.monotonic() - start < self.timeout:
                response = client.get(f"{self.base_url}/{task['task_id']}")
                if response.status_code == 429:
                    time.sleep(min(30, self.poll_interval * 4))
                    continue
                data = self._data(response)
                if data["state"] == "failed":
                    task.update(state="failed", error=data.get("err_msg"))
                    task_path.write_text(json.dumps(task), encoding="utf-8")
                    raise RuntimeError(f"MinerU parsing failed: {data.get('err_msg')}")
                if data["state"] == "done":
                    result = client.get(self._https(data["markdown_url"]))
                    result.raise_for_status()
                    markdown = result.content.decode("utf-8-sig")
                    if not markdown.strip():
                        raise RuntimeError("MinerU returned empty Markdown")
                    temporary = md_path.with_suffix(".tmp")
                    temporary.write_text(markdown, encoding="utf-8")
                    temporary.replace(md_path)
                    task.update(state="done", elapsed_seconds=round(time.monotonic() - start, 2))
                    task_path.write_text(json.dumps(task), encoding="utf-8")
                    return markdown, {**task, "cache_hit": False, "markdown_path": str(md_path)}
                time.sleep(self.poll_interval)
        raise TimeoutError(f"MinerU task {task['task_id']} timed out; rerun to resume polling")


def markdown_structure(markdown: str) -> tuple[str, dict]:
    """Adapt Markdown blocks to provider-neutral paper sections and captions.

Run-in headings are a conservative formatting heuristic, not model output.
Raw API Markdown remains unchanged in the client's cache.
"""
    text = re.sub(r"<!--\s*image\s*-->", "", markdown)
    text = re.sub(r"(?m)^([A-Za-z][A-Za-z0-9 ,()'’\-/]{2,99})—(?=\S)", r"## \1\n\n", text)
    # PRL-style unheaded references: add a boundary at the first numbered entry.
    if not re.search(r"(?im)^#{1,6}\s+References\s*$", text):
        text = re.sub(r"(?m)^(?=\[1\]\s)", "## References\n\n", text, count=1)
    figures, sections, references = [], [], []
    heading, paragraphs = "", []
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        head = re.fullmatch(r"(#{1,6})\s+(.+)", block)
        if head:
            if paragraphs:
                sections.append({"heading": heading, "paragraphs": paragraphs, "level": 1})
            heading, paragraphs = head[2], []
            continue
        fig = re.match(r"^(?:FIG\.|Figure)\s*(\d+)\.?\s+", block)
        if fig:
            figures.append({"id": f"fig_{fig[1]}", "number": int(fig[1]),
                            "type": "figure", "caption": block, "description": ""})
        elif heading.lower() in ("references", "bibliography"):
            references.append(block)
        else:
            paragraphs.append(block)
    if paragraphs:
        sections.append({"heading": heading, "paragraphs": paragraphs, "level": 1})
    meta = {"paper_sections": sections, "paper_figures": figures, "paper_tables": [],
            "sections": [{"title": s["heading"], "level": s["level"]} for s in sections],
            "toc": [s["heading"] for s in sections if s["heading"]],
            "structure_method": "markdown_and_run_in_heading_heuristics",
            "source_locator_type": "document_and_section", "image_assets_available": False}
    if references:
        meta["references_raw"] = "\n\n".join(references)
    for section in sections:
        if section["heading"].strip().lower() == "abstract":
            meta["abstract"] = "\n\n".join(section["paragraphs"])
            meta["abstract_source"] = "explicit_heading"
            break
    # Journals may omit an Abstract heading. Only infer the long paragraph
    # immediately preceding an explicit DOI line within the front matter.
    front = re.split(r"(?m)^DOI:\s*", markdown, maxsplit=1)
    if not meta.get("abstract") and len(front) == 2 and len(front[0]) < 12000:
        preceding = re.split(r"\n\s*\n", front[0].strip())[-1]
        if len(preceding) > 200 and not preceding.startswith("#"):
            meta["abstract"] = preceding
            meta["abstract_source"] = "paragraph_before_doi_heuristic"
    # Remove only the positively identified abstract from body sections. Preserve
    # all other source paragraphs, including deliberate repetition in the paper.
    abstract = meta.get("abstract", "")
    if abstract:
        canonical = lambda value: re.sub(r"\s+", " ", value).strip()
        for section in sections:
            if section["heading"].strip().lower() == "abstract":
                section["paragraphs"] = []
            section["paragraphs"] = [p for p in section["paragraphs"]
                if canonical(p) != canonical(abstract)]
    meta["structure_version"] = "mineru-clean-v2"
    return text, meta
