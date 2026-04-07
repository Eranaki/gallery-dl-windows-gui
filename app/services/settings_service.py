from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class AppSettings:
    gallery_dl_path: str = "gallery-dl"
    default_download_dir: str = str(Path.home() / "Downloads")
    last_cookies_browser: str = ""
    include_images: bool = True
    include_videos: bool = True
    include_archives: bool = False
    custom_extensions: str = ""
    naming_base_directory: str = ""
    naming_directory_template: str = ""
    naming_filename_template: str = ""
    naming_use_original_filenames: bool = False
    naming_path_compatibility_mode: str = "auto"
    naming_path_restrict: str = ""
    naming_path_replace: str = ""
    naming_path_remove: str = ""
    naming_path_strip: str = ""


class SettingsService:
    def __init__(self) -> None:
        self._storage_dir = Path.home() / "AppData" / "Roaming" / "GalleryDlGui"
        self._path = self._storage_dir / "settings.json"
        self._settings = self.load()

    @property
    def data(self) -> AppSettings:
        return self._settings

    @property
    def storage_dir(self) -> Path:
        return self._storage_dir

    def load(self) -> AppSettings:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return AppSettings()
        except Exception:
            return AppSettings()
        return AppSettings(**{**asdict(AppSettings()), **payload})

    def save(self, settings: AppSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._settings = settings
