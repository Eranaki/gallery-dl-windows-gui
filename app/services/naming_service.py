from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from gallery_dl import formatter


WINDOWS_INVALID_RE = re.compile(r'[<>:"/\\|?*]')
ASCII_INVALID_RE = re.compile(r"[^0-9A-Za-z._ -]")


@dataclass(frozen=True, slots=True)
class NamingPreset:
    id: str
    label: str
    description: str
    directory_template: str
    filename_template: str
    use_original_filenames: bool = False


NAMING_PRESETS: tuple[NamingPreset, ...] = (
    NamingPreset(
        id="site-author-id",
        label="\u0421\u0430\u0439\u0442 / \u0410\u0432\u0442\u043e\u0440 / ID",
        description="\u041f\u0430\u043f\u043a\u0430 \u0441\u0430\u0439\u0442\u0430, \u0430\u0432\u0442\u043e\u0440 \u0438 \u043a\u043e\u0440\u043e\u0442\u043a\u043e\u0435 \u0438\u043c\u044f \u043f\u043e ID.",
        directory_template="{category}/{user[id]}",
        filename_template="{id}.{extension}",
    ),
    NamingPreset(
        id="site-title",
        label="\u0421\u0430\u0439\u0442 / \u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a",
        description="\u041f\u043e\u0434\u0445\u043e\u0434\u0438\u0442 \u0434\u043b\u044f \u043f\u043e\u0441\u0442\u043e\u0432 \u0438 \u0433\u0430\u043b\u0435\u0440\u0435\u0439 \u0441 \u0447\u0438\u0442\u0430\u0435\u043c\u044b\u043c\u0438 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u044f\u043c\u0438.",
        directory_template="{category}",
        filename_template="{title}.{extension}",
    ),
    NamingPreset(
        id="manga",
        label="\u041c\u0430\u043d\u0433\u0430 / \u0413\u043b\u0430\u0432\u0430 / \u0421\u0442\u0440\u0430\u043d\u0438\u0446\u0430",
        description="\u0418\u0435\u0440\u0430\u0440\u0445\u0438\u044f \u0434\u043b\u044f \u043c\u0430\u043d\u0433\u0438 \u0438 \u0433\u043b\u0430\u0432.",
        directory_template="{category}/{manga}/c{chapter} - {title}",
        filename_template="{manga}_c{chapter}_{page:>03}.{extension}",
    ),
    NamingPreset(
        id="booru",
        label="Booru / ID",
        description="\u041f\u0440\u043e\u0441\u0442\u0430\u044f \u0441\u0445\u0435\u043c\u0430 \u0434\u043b\u044f booru-\u0441\u0430\u0439\u0442\u043e\u0432 \u0438 \u043f\u043e\u0441\u0442\u043e\u0432 \u043f\u043e ID.",
        directory_template="{category}/{search_tags}",
        filename_template="{id}.{extension}",
    ),
    NamingPreset(
        id="original",
        label="\u041e\u0440\u0438\u0433\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0435 \u0438\u043c\u0435\u043d\u0430 \u0432 \u043f\u0430\u043f\u043a\u0435 \u0441\u0430\u0439\u0442\u0430",
        description="\u041e\u0441\u0442\u0430\u0432\u043b\u044f\u0435\u0442 \u0438\u043c\u0435\u043d\u0430 \u043a\u0430\u043a \u043d\u0430 \u0441\u0430\u0439\u0442\u0435.",
        directory_template="{category}",
        filename_template="",
        use_original_filenames=True,
    ),
)


COMMON_KEYWORD_REFERENCE = """category
id
title
filename
extension
user[id]
user[name]
manga
chapter
page
search_tags
_now
"""


def split_directory_template(template: str) -> list[str]:
    normalized = template.replace("\\", "/").strip().strip("/")
    if not normalized:
        return []
    return [segment.strip() for segment in normalized.split("/") if segment.strip()]


def build_sample_keywords(url: str = "") -> dict[str, object]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    category = host or "site"
    now = datetime.now()
    return {
        "category": category,
        "id": 123456,
        "title": "Sample Title",
        "filename": "original-file",
        "extension": "jpg",
        "num": 1,
        "page": 3,
        "chapter": "12",
        "manga": "Sample Manga",
        "search_tags": "tag1 tag2",
        "user": {
            "id": "artist123",
            "name": "Author Name",
        },
        "date": now,
        "_now": now,
    }


def build_path_preview(
    *,
    destination: str,
    url: str,
    directory_template: str,
    filename_template: str,
    use_original_filenames: bool,
    path_compatibility_mode: str,
    organize_by_site: bool,
    base_directory: str = "",
    path_replace: str = "",
    path_remove: str = "",
    path_strip: str = "",
) -> tuple[str | None, str | None]:
    keywords = build_sample_keywords(url)
    base_path = Path(base_directory.strip() or destination.strip() or str(Path.home() / "Downloads"))

    try:
        if directory_template.strip():
            raw_segments = [
                _render_template(segment, keywords)
                for segment in split_directory_template(directory_template)
            ]
        elif organize_by_site:
            raw_segments = [_render_template("{category}", keywords)]
        else:
            raw_segments = []

        if use_original_filenames:
            rendered_filename = f"{keywords['filename']}.{keywords['extension']}"
        elif filename_template.strip():
            rendered_filename = _render_template(filename_template, keywords)
        else:
            rendered_filename = "auto-filename.jpg"
    except Exception as exc:
        return None, str(exc)

    cleaned_segments = [
        _sanitize_path_segment(
            segment,
            path_compatibility_mode,
            path_replace=path_replace,
            path_remove=path_remove,
            path_strip=path_strip,
        )
        for segment in raw_segments
    ]
    cleaned_filename = _sanitize_path_segment(
        rendered_filename,
        path_compatibility_mode,
        path_replace=path_replace,
        path_remove=path_remove,
        path_strip=path_strip,
    )

    final_path = base_path
    for segment in cleaned_segments:
        final_path /= segment
    final_path /= cleaned_filename
    return str(final_path), None


def get_preset_by_id(preset_id: str) -> NamingPreset | None:
    for preset in NAMING_PRESETS:
        if preset.id == preset_id:
            return preset
    return None


def build_common_keywords_text(url: str = "") -> str:
    category = build_sample_keywords(url)["category"]
    lines = [
        "\u0414\u043b\u044f \u0442\u0435\u043a\u0443\u0449\u0435\u0439 \u0441\u0441\u044b\u043b\u043a\u0438 \u0442\u043e\u0447\u043d\u044b\u0439 \u043d\u0430\u0431\u043e\u0440 \u043f\u043e\u043b\u0435\u0439 \u0437\u0430\u0432\u0438\u0441\u0438\u0442 \u043e\u0442 \u0441\u0430\u0439\u0442\u0430.",
        "",
        "\u0411\u044b\u0441\u0442\u0440\u044b\u0439 \u043e\u0440\u0438\u0435\u043d\u0442\u0438\u0440:",
        COMMON_KEYWORD_REFERENCE.strip(),
        "",
        f"\u0414\u043b\u044f preview \u0441\u0435\u0439\u0447\u0430\u0441 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0435\u0442\u0441\u044f category={category}",
    ]
    return "\n".join(lines)


def _render_template(template: str, keywords: dict[str, object]) -> str:
    parsed = formatter.parse(template, None)
    return str(parsed.format_map(keywords))


def _sanitize_path_segment(
    segment: str,
    mode: str,
    *,
    path_replace: str = "",
    path_remove: str = "",
    path_strip: str = "",
) -> str:
    mode = (mode or "auto").strip().lower()
    cleaned = segment

    if mode in {"auto", "windows-safe", "windows"}:
        cleaned = WINDOWS_INVALID_RE.sub("_", cleaned)
        cleaned = cleaned.rstrip(" .")
    elif mode in {"ascii", "ascii-safe"}:
        cleaned = ASCII_INVALID_RE.sub("_", cleaned)
        cleaned = cleaned.rstrip(" .")

    if path_remove:
        cleaned = re.sub(f"[{re.escape(path_remove)}]", "", cleaned)
    if path_replace:
        cleaned = WINDOWS_INVALID_RE.sub(path_replace, cleaned)
    if path_strip:
        cleaned = cleaned.rstrip(path_strip)

    return cleaned or "_"
