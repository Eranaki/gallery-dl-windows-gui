from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


class TaskMode(str, Enum):
    CHECK = "check"
    DOWNLOAD = "download"

    @property
    def label(self) -> str:
        return "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430" if self is TaskMode.CHECK else "\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        labels = {
            TaskStatus.QUEUED: "\u0412 \u043e\u0447\u0435\u0440\u0435\u0434\u0438",
            TaskStatus.RUNNING: "\u0412\u044b\u043f\u043e\u043b\u043d\u044f\u0435\u0442\u0441\u044f",
            TaskStatus.SUCCESS: "\u0413\u043e\u0442\u043e\u0432\u043e",
            TaskStatus.ERROR: "\u041e\u0448\u0438\u0431\u043a\u0430",
            TaskStatus.CANCELLED: "\u041e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e",
        }
        return labels[self]


@dataclass(slots=True)
class TaskOptions:
    destination: str
    organize_by_site: bool = True
    only_new: bool = True
    include_images: bool = True
    include_videos: bool = True
    include_archives: bool = False
    custom_extensions: str = ""
    base_directory: str = ""
    directory_template: str = ""
    range_text: str = ""
    date_after: str = ""
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
    progress_text: str = "\u041e\u0436\u0438\u0434\u0430\u043d\u0438\u0435"
    last_message: str = ""
    exit_code: int | None = None

    def __post_init__(self) -> None:
        if not self.site:
            self.site = self.detect_site()

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
        return host or "\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e"
