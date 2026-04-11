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


@dataclass(frozen=True, slots=True)
class NamingKeywordEntry:
    name: str
    sample: str
    description: str
    usage: str
    group: str
    template: str
    source_sections: tuple[str, ...]

    @property
    def search_text(self) -> str:
        return " ".join(
            (
                self.name,
                self.sample,
                self.description,
                self.usage,
                self.group,
                self.template,
            )
        ).lower()


class PreviewKeywordObject(dict):
    def __str__(self) -> str:
        for key in ("name", "id"):
            value = self.get(key)
            if value:
                return str(value)
        return super().__str__()


NAMING_PRESETS: tuple[NamingPreset, ...] = (
    NamingPreset(
        id="post-title-original",
        label="\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043f\u043e\u0441\u0442\u0430",
        description="\u041a\u0430\u0436\u0434\u044b\u0439 \u043f\u043e\u0441\u0442 \u0432 \u0441\u0432\u043e\u0435\u0439 \u043f\u0430\u043f\u043a\u0435 \u0441 \u043e\u0440\u0438\u0433\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u043c\u0438 \u0438\u043c\u0435\u043d\u0430\u043c\u0438 \u0444\u0430\u0439\u043b\u043e\u0432.",
        directory_template="{title}",
        filename_template="",
        use_original_filenames=True,
    ),
    NamingPreset(
        id="date-title-original",
        label="\u0414\u0430\u0442\u0430 + \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043f\u043e\u0441\u0442\u0430",
        description="\u0412 \u0438\u043c\u044f \u043f\u0430\u043f\u043a\u0438 \u0434\u043e\u0431\u0430\u0432\u043b\u044f\u0435\u0442\u0441\u044f \u0434\u0430\u0442\u0430 \u043f\u0443\u0431\u043b\u0438\u043a\u0430\u0446\u0438\u0438 \u043f\u043e\u0441\u0442\u0430.",
        directory_template="{date:%Y-%m-%d} {title}",
        filename_template="",
        use_original_filenames=True,
    ),
    NamingPreset(
        id="site-service-user-title-original",
        label="\u0421\u0430\u0439\u0442 / \u0441\u0435\u0440\u0432\u0438\u0441 / \u0430\u0432\u0442\u043e\u0440 / \u043f\u043e\u0441\u0442",
        description="\u0423\u0434\u043e\u0431\u043d\u0430\u044f \u0438\u0435\u0440\u0430\u0440\u0445\u0438\u044f \u0434\u043b\u044f Kemono, Bunkr \u0438 \u043f\u043e\u0445\u043e\u0436\u0438\u0445 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u043e\u0432.",
        directory_template="{category}/{service}/{user}/{title}",
        filename_template="",
        use_original_filenames=True,
    ),
    NamingPreset(
        id="site-service-user-date-title-original",
        label="\u0421\u0430\u0439\u0442 / \u0441\u0435\u0440\u0432\u0438\u0441 / \u0430\u0432\u0442\u043e\u0440 / \u0434\u0430\u0442\u0430 + \u043f\u043e\u0441\u0442",
        description="\u0422\u043e \u0436\u0435, \u043d\u043e \u0441 \u0434\u0430\u0442\u043e\u0439 \u0432 \u0438\u043c\u0435\u043d\u0438 \u043f\u0430\u043f\u043a\u0438 \u043f\u043e\u0441\u0442\u0430.",
        directory_template="{category}/{service}/{user}/{date:%Y-%m-%d} {title}",
        filename_template="",
        use_original_filenames=True,
    ),
    NamingPreset(
        id="site-service-user-date-title-numbered",
        label="\u0421\u0430\u0439\u0442 / \u0441\u0435\u0440\u0432\u0438\u0441 / \u0430\u0432\u0442\u043e\u0440 / \u0434\u0430\u0442\u0430 + \u043f\u043e\u0441\u0442 / \u043d\u0443\u043c\u0435\u0440\u0430\u0446\u0438\u044f",
        description="\u0415\u0441\u043b\u0438 \u043e\u0440\u0438\u0433\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0435 \u0438\u043c\u0435\u043d\u0430 \u0444\u0430\u0439\u043b\u043e\u0432 \u043d\u0435\u0443\u0434\u043e\u0431\u043d\u044b, \u0444\u0430\u0439\u043b\u044b \u0431\u0443\u0434\u0443\u0442 \u0438\u0434\u0442\u0438 \u043f\u043e \u043f\u043e\u0440\u044f\u0434\u043a\u0443.",
        directory_template="{category}/{service}/{user}/{date:%Y-%m-%d} {title}",
        filename_template="{num:>03}.{extension}",
        use_original_filenames=False,
    ),
    NamingPreset(
        id="author-date-title-original",
        label="\u0410\u0432\u0442\u043e\u0440 / \u0434\u0430\u0442\u0430 + \u043f\u043e\u0441\u0442",
        description="\u041a\u043e\u0440\u043e\u0442\u043a\u0438\u0439 \u0432\u0430\u0440\u0438\u0430\u043d\u0442: \u043f\u0430\u043f\u043a\u0430 \u0430\u0432\u0442\u043e\u0440\u0430, \u0430 \u0432\u043d\u0443\u0442\u0440\u0438 \u043f\u043e\u0441\u0442\u044b \u043f\u043e \u0434\u0430\u0442\u0435 \u0438 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u044e.",
        directory_template="{user}/{date:%Y-%m-%d} {title}",
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

GROUP_ORDER: tuple[str, ...] = (
    "Полезно для папок",
    "Полезно для файлов",
    "Автор и профиль",
    "Пост и публикация",
    "Вложения и файлы",
    "Служебные",
)


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
        "user": PreviewKeywordObject(id="artist123", name="Author Name"),
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


def build_common_keyword_entries(url: str = "") -> list[NamingKeywordEntry]:
    keywords = build_sample_keywords(url)
    common_fields = (
        ("category", keywords["category"], ("directory", "file")),
        ("id", keywords["id"], ("directory", "file")),
        ("title", keywords["title"], ("directory", "file")),
        ("date", keywords["date"], ("directory", "file")),
        ("filename", keywords["filename"], ("file",)),
        ("extension", keywords["extension"], ("file",)),
        ("user[id]", keywords["user"]["id"], ("directory", "file")),
        ("user[name]", keywords["user"]["name"], ("directory", "file")),
        ("manga", keywords["manga"], ("directory", "file")),
        ("chapter", keywords["chapter"], ("directory", "file")),
        ("page", keywords["page"], ("file",)),
        ("search_tags", keywords["search_tags"], ("directory",)),
        ("_now", keywords["_now"], ("directory", "file")),
    )
    entries: list[NamingKeywordEntry] = []
    for name, sample, sections in common_fields:
        entries.append(
            _build_keyword_entry(
                name=name,
                sample=_stringify_keyword_sample(sample),
                sections=tuple(sections),
            )
        )
    return sorted(entries, key=_keyword_sort_key)


def parse_gallery_dl_keywords(text: str) -> list[NamingKeywordEntry]:
    if not text.strip():
        return []

    parsed: dict[str, dict[str, object]] = {}
    current_section = ""
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("Keywords for "):
            lowered = stripped.lower()
            if "directory names" in lowered:
                current_section = "directory"
            elif "filenames and --filter" in lowered:
                current_section = "file"
            else:
                current_section = "generic"
            index += 1
            continue

        if stripped and set(stripped) == {"-"}:
            index += 1
            continue

        if raw_line.startswith("  "):
            index += 1
            continue

        field_name = stripped
        samples: list[str] = []
        index += 1

        while index < len(lines):
            next_line = lines[index]
            if next_line.startswith("  "):
                value = next_line.strip()
                if value:
                    samples.append(value)
                index += 1
                continue
            if not next_line.strip():
                index += 1
                if samples:
                    break
                continue
            break

        entry = parsed.setdefault(
            field_name,
            {
                "sample": "",
                "sections": set(),
            },
        )
        if samples and not entry["sample"]:
            entry["sample"] = " | ".join(samples)
        if current_section:
            entry["sections"].add(current_section)

    results: list[NamingKeywordEntry] = []
    for name, payload in parsed.items():
        sections = tuple(sorted(payload["sections"])) or ("generic",)
        results.append(
            _build_keyword_entry(
                name=name,
                sample=str(payload["sample"] or "-"),
                sections=sections,
            )
        )
    return sorted(results, key=_keyword_sort_key)


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


def _keyword_sort_key(entry: NamingKeywordEntry) -> tuple[int, str]:
    try:
        group_index = GROUP_ORDER.index(entry.group)
    except ValueError:
        group_index = len(GROUP_ORDER)
    return group_index, entry.name.lower()


def _build_keyword_entry(
    *,
    name: str,
    sample: str,
    sections: tuple[str, ...],
) -> NamingKeywordEntry:
    normalized_name = normalize_keyword_name(name)
    description, group = describe_keyword(normalized_name)
    usage = determine_keyword_usage(normalized_name, sections)
    return NamingKeywordEntry(
        name=normalized_name,
        sample=sample,
        description=description,
        usage=usage,
        group=group,
        template=build_keyword_template(normalized_name),
        source_sections=sections,
    )


def normalize_keyword_name(name: str) -> str:
    return re.sub(r"\['([^']+)'\]", r"[\1]", name)


def build_keyword_template(name: str) -> str:
    return "{" + normalize_keyword_name(name) + "}"


def determine_keyword_usage(name: str, sections: tuple[str, ...]) -> str:
    section_set = set(sections)
    if name.startswith(("attachments[", "file[")):
        return "Файлы"
    if "directory" in section_set and "file" in section_set:
        return "Папки и файлы"
    if "directory" in section_set:
        return "Папки"
    if "file" in section_set:
        return "Файлы и фильтр"
    if name in {"filename", "extension", "name", "path", "hash", "url", "num"}:
        return "Файлы"
    return "Папки и файлы"


def describe_keyword(name: str) -> tuple[str, str]:
    exact: dict[str, tuple[str, str]] = {
        "category": ("Категория или сайт, через который идет загрузка.", "Полезно для папок"),
        "service": ("Источник внутри сервиса, например patreon или fanbox.", "Пост и публикация"),
        "subcategory": ("Подкатегория или подтип источника.", "Служебные"),
        "title": ("Название поста, галереи или записи.", "Пост и публикация"),
        "date": ("Дата публикации в формате даты и времени.", "Пост и публикация"),
        "published": ("Дата публикации как исходная строка сервиса.", "Пост и публикация"),
        "id": ("Уникальный идентификатор поста или объекта.", "Пост и публикация"),
        "user": ("Идентификатор автора или профиля.", "Автор и профиль"),
        "username": ("Имя автора, если сервис его отдает отдельно.", "Автор и профиль"),
        "count": ("Количество файлов или вложений внутри поста.", "Пост и публикация"),
        "filename": ("Имя файла без расширения.", "Полезно для файлов"),
        "extension": ("Расширение файла без точки.", "Полезно для файлов"),
        "name": ("Полное имя файла, обычно с расширением.", "Полезно для файлов"),
        "num": ("Порядковый номер файла в посте или галерее.", "Полезно для файлов"),
        "path": ("Внутренний путь файла на сервере источника.", "Вложения и файлы"),
        "url": ("Прямая ссылка на файл или вложение.", "Вложения и файлы"),
        "hash": ("Хэш или служебный идентификатор содержимого.", "Вложения и файлы"),
        "substring": ("Служебная часть совпадения URL у extractor-а.", "Служебные"),
        "_now": ("Текущее время на момент запуска задачи.", "Служебные"),
        "manga": ("Название манги или серии.", "Пост и публикация"),
        "chapter": ("Номер или название главы.", "Пост и публикация"),
        "page": ("Номер страницы или кадра.", "Полезно для файлов"),
        "search_tags": ("Теги поиска или запроса.", "Пост и публикация"),
    }
    if name in exact:
        return exact[name]

    if name.startswith("user_profile["):
        suffix = name[len("user_profile["):-1]
        return (
            f"Поле профиля автора: {suffix}. Обычно это дополнительные сведения об аккаунте.",
            "Автор и профиль",
        )

    if name.startswith("attachments["):
        suffix = name.split("]", 1)[-1].lstrip("[").rstrip("]")
        return (
            f"Свойство одного из вложений поста: {suffix or 'поле вложения'}.",
            "Вложения и файлы",
        )

    if name.startswith("file["):
        suffix = name[len("file["):-1]
        return (
            f"Свойство текущего файла или основного вложения: {suffix}.",
            "Вложения и файлы",
        )

    if name.startswith("user["):
        suffix = name[len("user["):-1]
        return (
            f"Поле автора или пользователя: {suffix}.",
            "Автор и профиль",
        )

    return ("Техническое поле extractor-а, зависит от конкретного сайта.", "Служебные")


def _stringify_keyword_sample(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)
