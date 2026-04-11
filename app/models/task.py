from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
import re
from urllib.parse import urlparse
from uuid import uuid4


class TaskMode(str, Enum):
    CHECK = "check"
    DOWNLOAD = "download"

    def label(self, language: str = "ru") -> str:
        if language == "en":
            return "Check" if self is TaskMode.CHECK else "Download"
        return "Проверка" if self is TaskMode.CHECK else "Загрузка"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"

    def label(self, language: str = "ru") -> str:
        if language == "en":
            labels = {
                TaskStatus.QUEUED: "Queued",
                TaskStatus.RUNNING: "Running",
                TaskStatus.SUCCESS: "Done",
                TaskStatus.ERROR: "Error",
                TaskStatus.CANCELLED: "Stopped",
            }
            return labels[self]
        labels = {
            TaskStatus.QUEUED: "В очереди",
            TaskStatus.RUNNING: "Выполняется",
            TaskStatus.SUCCESS: "Готово",
            TaskStatus.ERROR: "Ошибка",
            TaskStatus.CANCELLED: "Остановлено",
        }
        return labels[self]


@dataclass(slots=True)
class TaskOptions:
    destination: str
    organize_by_site: bool = True
    only_new: bool = True
    save_log: bool = False
    include_all_files: bool = False
    include_images: bool = True
    include_videos: bool = True
    include_archives: bool = False
    custom_extensions: str = ""
    base_directory: str = ""
    directory_template: str = ""
    range_text: str = ""
    date_before: str = ""
    date_after: str = ""
    filesize_min: str = ""
    filesize_max: str = ""
    username: str = ""
    password: str = ""
    cookies_file: str = ""
    cookies_from_browser: str = ""
    filename_template: str = ""
    use_original_filenames: bool = False
    path_compatibility_mode: str = "auto"
    path_restrict: str = ""
    path_replace: str = ""
    path_remove: str = ""
    path_strip: str = ""
    write_metadata: bool = False
    write_info_json: bool = False
    write_tags: bool = False
    archive_format: str = "none"
    ugoira_format: str = "none"
    proxy_url: str = ""
    retries: str = ""
    timeout: str = ""


@dataclass(slots=True)
class DownloadTask:
    url: str
    mode: TaskMode
    options: TaskOptions
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=datetime.now)
    status: TaskStatus = TaskStatus.QUEUED
    site: str = ""
    progress_text: str = "Ожидание"
    last_message: str = ""
    exit_code: int | None = None
    log_file_path: str = ""

    def __post_init__(self) -> None:
        if not self.site:
            self.site = self.detect_site()
        if self.options.save_log and not self.log_file_path:
            self.log_file_path = self._build_log_file_path()

    @property
    def target_folder(self) -> str:
        return str(Path(self.options.destination))

    @property
    def title(self) -> str:
        parsed = urlparse(self.url)
        tail = parsed.path.strip("/").split("/")[-1] if parsed.path else ""
        if tail:
            return f"{parsed.netloc}/{tail}"
        return parsed.netloc or self.url

    def detect_site(self) -> str:
        parsed = urlparse(self.url)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or "unknown"

    def _build_log_file_path(self) -> str:
        base_dir = Path(self.options.destination) / "gallery-dl-logs"
        timestamp = self.created_at.strftime("%Y%m%d-%H%M%S")
        safe_site = re.sub(r"[^A-Za-z0-9._-]+", "_", self.site or "unknown").strip("._-") or "unknown"
        safe_mode = self.mode.value
        filename = f"{timestamp}_{safe_mode}_{safe_site}_{self.id[:8]}.log.txt"
        return str(base_dir / filename)
