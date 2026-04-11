from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from gallery_dl import formatter


WINDOWS_INVALID_RE = re.compile(r'[<>:"/\\|?*]')
ASCII_INVALID_RE = re.compile(r"[^0-9A-Za-z._ -]")


def _txt(language: str, ru: str, en: str) -> str:
    return ru if language == "ru" else en


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


def get_group_order(language: str = "ru") -> tuple[str, ...]:
    return (
        _txt(language, "Полезно для папок", "Useful for folders"),
        _txt(language, "Полезно для файлов", "Useful for files"),
        _txt(language, "Автор и профиль", "Author and profile"),
        _txt(language, "Пост и публикация", "Post and publication"),
        _txt(language, "Вложения и файлы", "Attachments and files"),
        _txt(language, "Служебные", "Technical"),
    )


GROUP_ORDER: tuple[str, ...] = get_group_order("ru")


def get_naming_presets(language: str = "ru") -> tuple[NamingPreset, ...]:
    return (
        NamingPreset(
            id="post-title-original",
            label=_txt(language, "Название поста", "Post title"),
            description=_txt(
                language,
                "Каждый пост в своей папке с оригинальными именами файлов.",
                "Each post gets its own folder and keeps the original file names.",
            ),
            directory_template="{title}",
            filename_template="",
            use_original_filenames=True,
        ),
        NamingPreset(
            id="date-title-original",
            label=_txt(language, "Дата + название поста", "Date + post title"),
            description=_txt(
                language,
                "В имя папки добавляется дата публикации поста.",
                "Adds the post publication date to the folder name.",
            ),
            directory_template="{date:%Y-%m-%d} {title}",
            filename_template="",
            use_original_filenames=True,
        ),
        NamingPreset(
            id="site-service-user-title-original",
            label=_txt(language, "Сайт / сервис / автор / пост", "Site / service / author / post"),
            description=_txt(
                language,
                "Удобная иерархия для Kemono, Bunkr и похожих источников.",
                "A convenient hierarchy for Kemono, Bunkr, and similar sources.",
            ),
            directory_template="{category}/{service}/{user}/{title}",
            filename_template="",
            use_original_filenames=True,
        ),
        NamingPreset(
            id="site-service-user-date-title-original",
            label=_txt(language, "Сайт / сервис / автор / дата + пост", "Site / service / author / date + post"),
            description=_txt(
                language,
                "То же, но с датой в имени папки поста.",
                "Same structure, but with the date in the post folder name.",
            ),
            directory_template="{category}/{service}/{user}/{date:%Y-%m-%d} {title}",
            filename_template="",
            use_original_filenames=True,
        ),
        NamingPreset(
            id="site-service-user-date-title-numbered",
            label=_txt(language, "Сайт / сервис / автор / дата + пост / нумерация", "Site / service / author / date + post / numbering"),
            description=_txt(
                language,
                "Если оригинальные имена неудобны, файлы будут идти по порядку.",
                "If original file names are messy, files will be numbered in order.",
            ),
            directory_template="{category}/{service}/{user}/{date:%Y-%m-%d} {title}",
            filename_template="{num:>03}.{extension}",
            use_original_filenames=False,
        ),
        NamingPreset(
            id="author-date-title-original",
            label=_txt(language, "Автор / дата + пост", "Author / date + post"),
            description=_txt(
                language,
                "Короткий вариант: папка автора, а внутри посты по дате и названию.",
                "A compact layout: author folder with posts grouped by date and title.",
            ),
            directory_template="{user}/{date:%Y-%m-%d} {title}",
            filename_template="",
            use_original_filenames=True,
        ),
    )


NAMING_PRESETS: tuple[NamingPreset, ...] = get_naming_presets("ru")


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
        "service": "service",
        "id": 123456,
        "title": "Sample Title",
        "filename": "original-file",
        "extension": "jpg",
        "name": "original-file.jpg",
        "num": 1,
        "page": 3,
        "chapter": "12",
        "manga": "Sample Manga",
        "search_tags": "tag1 tag2",
        "user": PreviewKeywordObject(id="artist123", name="Author Name"),
        "username": "Author Name",
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


def get_preset_by_id(preset_id: str, language: str = "ru") -> NamingPreset | None:
    for preset in get_naming_presets(language):
        if preset.id == preset_id:
            return preset
    return None


def build_common_keywords_text(url: str = "", language: str = "ru") -> str:
    category = build_sample_keywords(url)["category"]
    lines = [
        _txt(
            language,
            "Для текущей ссылки точный набор полей зависит от сайта.",
            "The exact list of fields depends on the current site.",
        ),
        "",
        _txt(language, "Быстрый ориентир:", "Quick reference:"),
        COMMON_KEYWORD_REFERENCE.strip(),
        "",
        _txt(
            language,
            f"Для preview сейчас используется category={category}",
            f"The preview currently uses category={category}",
        ),
    ]
    return "\n".join(lines)


def build_common_keyword_entries(url: str = "", language: str = "ru") -> list[NamingKeywordEntry]:
    keywords = build_sample_keywords(url)
    common_fields = (
        ("category", keywords["category"], ("directory", "file")),
        ("service", keywords["service"], ("directory", "file")),
        ("id", keywords["id"], ("directory", "file")),
        ("title", keywords["title"], ("directory", "file")),
        ("date", keywords["date"], ("directory", "file")),
        ("filename", keywords["filename"], ("file",)),
        ("extension", keywords["extension"], ("file",)),
        ("name", keywords["name"], ("file",)),
        ("user[id]", keywords["user"]["id"], ("directory", "file")),
        ("user[name]", keywords["user"]["name"], ("directory", "file")),
        ("username", keywords["username"], ("directory", "file")),
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
                language=language,
            )
        )
    return sorted(entries, key=lambda entry: _keyword_sort_key(entry, language))


def parse_gallery_dl_keywords(text: str, language: str = "ru") -> list[NamingKeywordEntry]:
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
                language=language,
            )
        )
    return sorted(results, key=lambda entry: _keyword_sort_key(entry, language))


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


def _keyword_sort_key(entry: NamingKeywordEntry, language: str) -> tuple[int, str]:
    group_order = get_group_order(language)
    try:
        group_index = group_order.index(entry.group)
    except ValueError:
        group_index = len(group_order)
    return group_index, entry.name.lower()


def _build_keyword_entry(
    *,
    name: str,
    sample: str,
    sections: tuple[str, ...],
    language: str = "ru",
) -> NamingKeywordEntry:
    normalized_name = normalize_keyword_name(name)
    description, group = describe_keyword(normalized_name, language)
    usage = determine_keyword_usage(normalized_name, sections, language)
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


def determine_keyword_usage(name: str, sections: tuple[str, ...], language: str = "ru") -> str:
    section_set = set(sections)
    if name.startswith(("attachments[", "file[")):
        return _txt(language, "Файлы", "Files")
    if "directory" in section_set and "file" in section_set:
        return _txt(language, "Папки и файлы", "Folders and files")
    if "directory" in section_set:
        return _txt(language, "Папки", "Folders")
    if "file" in section_set:
        return _txt(language, "Файлы и фильтр", "Files and filters")
    if name in {"filename", "extension", "name", "path", "hash", "url", "num"}:
        return _txt(language, "Файлы", "Files")
    return _txt(language, "Папки и файлы", "Folders and files")


def describe_keyword(name: str, language: str = "ru") -> tuple[str, str]:
    exact: dict[str, tuple[str, str]] = {
        "category": (_txt(language, "Категория или сайт, через который идет загрузка.", "Category or site name used by the extractor."), _txt(language, "Полезно для папок", "Useful for folders")),
        "service": (_txt(language, "Источник внутри сервиса, например patreon или fanbox.", "Source inside a service, for example patreon or fanbox."), _txt(language, "Пост и публикация", "Post and publication")),
        "subcategory": (_txt(language, "Подкатегория или подтип источника.", "Source subcategory or subtype."), _txt(language, "Служебные", "Technical")),
        "title": (_txt(language, "Название поста, галереи или записи.", "Post, gallery, or entry title."), _txt(language, "Пост и публикация", "Post and publication")),
        "date": (_txt(language, "Дата публикации в формате даты и времени.", "Publication date as a datetime value."), _txt(language, "Пост и публикация", "Post and publication")),
        "published": (_txt(language, "Дата публикации как исходная строка сервиса.", "Publication date as the raw service string."), _txt(language, "Пост и публикация", "Post and publication")),
        "id": (_txt(language, "Уникальный идентификатор поста или объекта.", "Unique post or object identifier."), _txt(language, "Пост и публикация", "Post and publication")),
        "user": (_txt(language, "Идентификатор автора или профиля.", "Author or profile identifier."), _txt(language, "Автор и профиль", "Author and profile")),
        "username": (_txt(language, "Имя автора, если сервис отдает его отдельно.", "Author name, if the service provides it separately."), _txt(language, "Автор и профиль", "Author and profile")),
        "count": (_txt(language, "Количество файлов или вложений внутри поста.", "Number of files or attachments inside the post."), _txt(language, "Пост и публикация", "Post and publication")),
        "filename": (_txt(language, "Имя файла без расширения.", "File name without extension."), _txt(language, "Полезно для файлов", "Useful for files")),
        "extension": (_txt(language, "Расширение файла без точки.", "File extension without the dot."), _txt(language, "Полезно для файлов", "Useful for files")),
        "name": (_txt(language, "Полное имя файла, обычно с расширением.", "Full file name, usually with extension."), _txt(language, "Полезно для файлов", "Useful for files")),
        "num": (_txt(language, "Порядковый номер файла в посте или галерее.", "Sequential file number inside the post or gallery."), _txt(language, "Полезно для файлов", "Useful for files")),
        "path": (_txt(language, "Внутренний путь файла на сервере источника.", "Internal file path on the source server."), _txt(language, "Вложения и файлы", "Attachments and files")),
        "url": (_txt(language, "Прямая ссылка на файл или вложение.", "Direct link to the file or attachment."), _txt(language, "Вложения и файлы", "Attachments and files")),
        "hash": (_txt(language, "Хэш или служебный идентификатор содержимого.", "Hash or internal content identifier."), _txt(language, "Вложения и файлы", "Attachments and files")),
        "substring": (_txt(language, "Служебная часть совпадения URL у extractor-а.", "Technical substring from the extractor URL match."), _txt(language, "Служебные", "Technical")),
        "_now": (_txt(language, "Текущее время на момент запуска задачи.", "Current time when the task starts."), _txt(language, "Служебные", "Technical")),
        "manga": (_txt(language, "Название манги или серии.", "Manga or series title."), _txt(language, "Пост и публикация", "Post and publication")),
        "chapter": (_txt(language, "Номер или название главы.", "Chapter number or title."), _txt(language, "Пост и публикация", "Post and publication")),
        "page": (_txt(language, "Номер страницы или кадра.", "Page or frame number."), _txt(language, "Полезно для файлов", "Useful for files")),
        "search_tags": (_txt(language, "Теги поиска или запроса.", "Search or query tags."), _txt(language, "Пост и публикация", "Post and publication")),
    }
    if name in exact:
        return exact[name]

    if name.startswith("user_profile["):
        suffix = name[len("user_profile["):-1]
        return (
            _txt(language, f"Поле профиля автора: {suffix}. Обычно это дополнительные сведения об аккаунте.", f"Author profile field: {suffix}. Usually this is extra account metadata."),
            _txt(language, "Автор и профиль", "Author and profile"),
        )

    if name.startswith("attachments["):
        suffix = name.split("]", 1)[-1].lstrip("[").rstrip("]")
        return (
            _txt(language, f"Свойство одного из вложений поста: {suffix or 'поле вложения'}.", f"Property of one of the post attachments: {suffix or 'attachment field'}."),
            _txt(language, "Вложения и файлы", "Attachments and files"),
        )

    if name.startswith("file["):
        suffix = name[len("file["):-1]
        return (
            _txt(language, f"Свойство текущего файла или основного вложения: {suffix}.", f"Property of the current file or main attachment: {suffix}."),
            _txt(language, "Вложения и файлы", "Attachments and files"),
        )

    if name.startswith("user["):
        suffix = name[len("user["):-1]
        return (
            _txt(language, f"Поле автора или пользователя: {suffix}.", f"Author or user field: {suffix}."),
            _txt(language, "Автор и профиль", "Author and profile"),
        )

    return (
        _txt(language, "Техническое поле extractor-а, зависит от конкретного сайта.", "Technical extractor field that depends on the specific site."),
        _txt(language, "Служебные", "Technical"),
    )


def _stringify_keyword_sample(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)
