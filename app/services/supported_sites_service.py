from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from app.models.supported_sites import SupportedSiteEntry, SupportedSitesPayload


DEFAULT_SECTION = "\u041e\u0441\u043d\u043e\u0432\u043d\u044b\u0435 \u0441\u0430\u0439\u0442\u044b"


class _SupportedSitesTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[tuple[str, int]]] = []
        self._current_row: list[tuple[str, int]] | None = None
        self._current_cell_parts: list[str] | None = None
        self._current_colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag == "tr":
            self._current_row = []
        elif tag == "td" and self._current_row is not None:
            self._current_cell_parts = []
            self._current_colspan = int(attrs_map.get("colspan") or "1")
        elif tag == "br" and self._current_cell_parts is not None:
            self._current_cell_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._current_cell_parts is not None:
            self._current_cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._current_row is not None and self._current_cell_parts is not None:
            text = "".join(self._current_cell_parts)
            cleaned = " ".join(text.replace("\xa0", " ").split())
            self._current_row.append((unescape(cleaned), self._current_colspan))
            self._current_cell_parts = None
            self._current_colspan = 1
        elif tag == "tr":
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = None


class SupportedSitesService:
    source_url = "https://raw.githubusercontent.com/mikf/gallery-dl/master/docs/supportedsites.md"
    refresh_interval = timedelta(days=7)

    def __init__(self, storage_dir: Path) -> None:
        self._cache_path = storage_dir / "supported_sites_cache.json"

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def load_cached(self) -> SupportedSitesPayload | None:
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            return None
        try:
            result = SupportedSitesPayload.from_dict(payload)
        except Exception:
            return None
        return result if result.sites else None

    def needs_refresh(self, payload: SupportedSitesPayload) -> bool:
        if not payload.fetched_at:
            return True
        try:
            fetched_at = datetime.fromisoformat(payload.fetched_at)
        except ValueError:
            return True
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc)
        return age >= self.refresh_interval

    def fetch_latest(self) -> SupportedSitesPayload:
        html = self._download_source()
        sites = self._parse_source(html)
        if not sites:
            raise ValueError("GitHub \u0432\u0435\u0440\u043d\u0443\u043b \u043f\u0443\u0441\u0442\u043e\u0439 \u0441\u043f\u0438\u0441\u043e\u043a \u0441\u0430\u0439\u0442\u043e\u0432.")
        payload = SupportedSitesPayload(
            sites=sites,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source_url=self.source_url,
        )
        self._save_cache(payload)
        return payload

    def load_or_bootstrap(self) -> SupportedSitesPayload | None:
        cached = self.load_cached()
        if cached is not None:
            return cached
        try:
            return self.fetch_latest()
        except Exception:
            return None

    def _save_cache(self, payload: SupportedSitesPayload) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps(payload.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _download_source(self) -> str:
        request = Request(
            self.source_url,
            headers={
                "User-Agent": "gallery-dl-gui/0.1 (+https://github.com/mikf/gallery-dl)",
                "Accept": "text/plain; charset=utf-8",
            },
        )
        with urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8")

    def _parse_source(self, html: str) -> list[SupportedSiteEntry]:
        parser = _SupportedSitesTableParser()
        parser.feed(html)

        entries: list[SupportedSiteEntry] = []
        current_section = DEFAULT_SECTION
        for row in parser.rows:
            if len(row) == 1 and row[0][1] >= 4:
                current_section = row[0][0] or DEFAULT_SECTION
                continue

            texts = [text for text, _colspan in row]
            texts += [""] * (4 - len(texts))
            name, url, capabilities, auth = texts[:4]

            if not any((name, url, capabilities, auth)):
                continue

            if not name and not url:
                continue

            auth = self._normalize_auth(auth)
            tooltip_text = self._build_tooltip(
                name=name,
                url=url,
                capabilities=capabilities,
                auth=auth,
                section=current_section,
            )
            entries.append(
                SupportedSiteEntry(
                    name=name,
                    url=url,
                    capabilities=capabilities,
                    auth=auth,
                    tooltip_text=tooltip_text,
                    section=current_section,
                )
            )
        return entries

    def _normalize_auth(self, auth: str) -> str:
        cleaned = " ".join(auth.replace("\xa0", " ").split())
        return cleaned

    def _build_tooltip(
        self,
        *,
        name: str,
        url: str,
        capabilities: str,
        auth: str,
        section: str,
    ) -> str:
        lines = [
            f"\u0421\u0430\u0439\u0442: {name}",
            f"URL: {url or '-'}",
            f"\u0412\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0441\u0442\u0438: {capabilities or '-'}",
            f"\u0410\u0432\u0442\u043e\u0440\u0438\u0437\u0430\u0446\u0438\u044f: {auth or '-'}",
        ]
        if section and section != DEFAULT_SECTION:
            lines.append(f"\u0421\u0435\u043a\u0446\u0438\u044f: {section}")
        return "\n".join(lines)
