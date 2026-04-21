from __future__ import annotations

import re
from urllib.parse import urlparse

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QTextEdit


def _deduplicate_urls(urls: list[str]) -> list[str]:
    unique_urls: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = url.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_urls.append(normalized)
    return unique_urls


def _looks_like_url(value: str) -> bool:
    if not value:
        return False
    normalized = value.strip()
    if any(char.isspace() for char in normalized):
        return False
    parsed = urlparse(normalized)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []

    candidates: list[str] = []
    stripped = text.strip()
    if _looks_like_url(stripped):
        candidates.append(stripped)

    for line in stripped.splitlines():
        line = line.strip()
        if _looks_like_url(line):
            candidates.append(line)

    for match in re.findall(r"https?://[^\s<>\"]+", stripped, flags=re.IGNORECASE):
        candidates.append(match.rstrip("),.;]}>"))

    return _deduplicate_urls(candidates)


def _extract_urls_from_bytes(data: bytes) -> list[str]:
    if not data:
        return []

    decoded_variants: list[str] = []
    for encoding in ("utf-16-le", "utf-16", "utf-8", "cp1251", "latin-1"):
        try:
            decoded = data.decode(encoding).replace("\x00", "").strip()
        except UnicodeDecodeError:
            continue
        if decoded and decoded not in decoded_variants:
            decoded_variants.append(decoded)

    urls: list[str] = []
    for decoded in decoded_variants:
        urls.extend(_extract_urls_from_text(decoded))
    return _deduplicate_urls(urls)


def _extract_urls_from_html(html: str) -> list[str]:
    if not html:
        return []
    matches = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    urls = [match.strip() for match in matches if _looks_like_url(match)]
    if urls:
        return _deduplicate_urls(urls)
    return _extract_urls_from_text(html)


def extract_urls_from_mime_data(mime_data) -> list[str]:
    urls: list[str] = []

    if mime_data is None:
        return []

    if mime_data.hasUrls():
        for url in mime_data.urls():
            text = url.toString().strip()
            if _looks_like_url(text):
                urls.append(text)

    browser_formats = (
        "text/uri-list",
        "text/x-moz-url",
        'application/x-qt-windows-mime;value="UniformResourceLocatorW"',
        'application/x-qt-windows-mime;value="UniformResourceLocator"',
    )
    for mime_type in browser_formats:
        if mime_data.hasFormat(mime_type):
            urls.extend(_extract_urls_from_bytes(bytes(mime_data.data(mime_type))))

    if mime_data.hasHtml():
        urls.extend(_extract_urls_from_html(mime_data.html()))

    if mime_data.hasText():
        urls.extend(_extract_urls_from_text(mime_data.text()))

    return _deduplicate_urls(urls)


def extract_clipboard_urls_text() -> str:
    clipboard = QGuiApplication.clipboard()
    mime_data = clipboard.mimeData()
    urls = extract_urls_from_mime_data(mime_data)
    if urls:
        return "\n".join(urls)
    return clipboard.text().strip()


class UrlInputTextEdit(QTextEdit):
    def insertFromMimeData(self, source) -> None:  # noqa: N802
        urls = extract_urls_from_mime_data(source)
        if urls:
            cursor = self.textCursor()
            text = "\n".join(urls)
            if not cursor.atBlockStart() and self.toPlainText():
                cursor.insertText("\n")
            cursor.insertText(text)
            self.setTextCursor(cursor)
            return
        super().insertFromMimeData(source)
