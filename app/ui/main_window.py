from __future__ import annotations

import re
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.supported_sites import SupportedSiteEntry, SupportedSitesPayload
from app.models.task import DownloadTask, TaskMode, TaskOptions, TaskStatus
from app.services.gallery_dl_runner import GalleryDlRunner
from app.services.naming_service import (
    NamingKeywordEntry,
    NAMING_PRESETS,
    GROUP_ORDER,
    build_common_keyword_entries,
    build_path_preview,
    get_preset_by_id,
    parse_gallery_dl_keywords,
)
from app.services.settings_service import AppSettings, SettingsService
from app.services.supported_sites_service import DEFAULT_SECTION, SupportedSitesService


SUPPORTED_SITES_DOCK_WIDTH = 440
ARCHIVE_EXTENSIONS_HINT = ".zip, .rar, .7z, .tar, .gz, .bz2, .xz, .tgz, .tbz2, .txz, .cbz, .cbr, .cb7, .cbt, .zst ..."


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


def _extract_urls_from_mime_data(mime_data) -> list[str]:
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


def _extract_clipboard_urls_text() -> str:
    clipboard = QGuiApplication.clipboard()
    mime_data = clipboard.mimeData()
    urls = _extract_urls_from_mime_data(mime_data)
    if urls:
        return "\n".join(urls)
    return clipboard.text().strip()


class UrlInputTextEdit(QTextEdit):
    def insertFromMimeData(self, source) -> None:  # noqa: N802
        urls = _extract_urls_from_mime_data(source)
        if urls:
            cursor = self.textCursor()
            text = "\n".join(urls)
            if not cursor.atBlockStart() and self.toPlainText():
                cursor.insertText("\n")
            cursor.insertText(text)
            self.setTextCursor(cursor)
            return
        super().insertFromMimeData(source)


class MainWindow(QMainWindow):
    supported_sites_loaded = Signal(object, bool)
    supported_sites_failed = Signal(str, bool)

    def __init__(self, settings_service: SettingsService) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.app_settings = replace(settings_service.data)
        self.runner = GalleryDlRunner(self.app_settings.gallery_dl_path)
        self.supported_sites_service = SupportedSitesService(settings_service.storage_dir)

        self.task_rows: dict[str, int] = {}
        self.tasks: dict[str, DownloadTask] = {}
        self.history_rows: dict[str, int] = {}
        self._initialized_log_files: set[str] = set()
        self._finalized_log_files: set[str] = set()
        self._failed_log_files: set[str] = set()
        self._current_task_id: str | None = None
        self._current_part_status: str = ""
        self._current_part_path: str = ""
        self._current_part_size: int = 0
        self._current_part_timestamp: float = 0.0
        self.supported_sites_payload: SupportedSitesPayload | None = None
        self._supported_sites_refresh_active = False
        self._supported_sites_thread: threading.Thread | None = None
        self._download_poll_timer = QTimer(self)
        self._download_poll_timer.setInterval(1500)
        self._download_poll_timer.timeout.connect(self._poll_active_download_progress)

        self.setWindowTitle("gallery-dl GUI")
        self.resize(1360, 860)

        self._build_ui()
        self._wire_signals()

        self.supported_sites_loaded.connect(self._on_supported_sites_loaded)
        self.supported_sites_failed.connect(self._on_supported_sites_failed)

        self._initialize_supported_sites()

    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.downloads_tab = QWidget()
        self.history_tab = QWidget()
        self.settings_tab = QWidget()
        self.tabs.addTab(self.downloads_tab, "\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0438")
        self.tabs.addTab(self.history_tab, "\u0418\u0441\u0442\u043e\u0440\u0438\u044f")
        self.tabs.addTab(self.settings_tab, "\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438")

        self._build_downloads_tab()
        self._build_history_tab()
        self._build_settings_tab()
        self._build_advanced_dock()
        self._build_supported_sites_dock()
        self.tabifyDockWidget(self.advanced_dock, self.supported_sites_dock)
        self._build_statusbar()
        self._build_actions()

    def _build_downloads_tab(self) -> None:
        root = QVBoxLayout(self.downloads_tab)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top_card = QFrame()
        top_card.setFrameShape(QFrame.Shape.StyledPanel)
        top_layout = QVBoxLayout(top_card)
        top_layout.setSpacing(10)

        title = QLabel("\u041d\u043e\u0432\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        top_layout.addWidget(title)

        urls_layout = QHBoxLayout()
        self.urls_edit = UrlInputTextEdit()
        self.urls_edit.setPlaceholderText(
            "\u0412\u0441\u0442\u0430\u0432\u044c \u043e\u0434\u043d\u0443 \u0438\u043b\u0438 \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e "
            "\u0441\u0441\u044b\u043b\u043e\u043a. \u041a\u0430\u0436\u0434\u0430\u044f \u0441\u0441\u044b\u043b\u043a\u0430 \u0441 \u043d\u043e\u0432\u043e\u0439 "
            "\u0441\u0442\u0440\u043e\u043a\u0438."
        )
        self.urls_edit.setMinimumHeight(120)
        urls_layout.addWidget(self.urls_edit, 1)

        url_buttons_layout = QVBoxLayout()
        url_buttons_layout.setSpacing(8)
        self.paste_button = QPushButton("\u0412\u0441\u0442\u0430\u0432\u0438\u0442\u044c")
        self.clear_button = QPushButton("\u041e\u0447\u0438\u0441\u0442\u0438\u0442\u044c")
        for button in (self.paste_button, self.clear_button):
            button.setMinimumHeight(36)
            button.setMinimumWidth(120)
            url_buttons_layout.addWidget(button)
        url_buttons_layout.addStretch(1)
        urls_layout.addLayout(url_buttons_layout)
        top_layout.addLayout(urls_layout)

        path_layout = QHBoxLayout()
        self.destination_edit = QComboBox()
        self.destination_edit.setEditable(True)
        self.destination_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.destination_edit.setMinimumContentsLength(40)
        self._load_recent_destinations()
        self.destination_button = QPushButton("\u041e\u0431\u0437\u043e\u0440")
        path_layout.addWidget(QLabel("\u041f\u0430\u043f\u043a\u0430:"))
        path_layout.addWidget(self.destination_edit, 1)
        path_layout.addWidget(self.destination_button)
        top_layout.addLayout(path_layout)

        quick_layout = QGridLayout()
        self.only_new_check = QCheckBox("\u0422\u043e\u043b\u044c\u043a\u043e \u043d\u043e\u0432\u043e\u0435")
        self.only_new_check.setChecked(True)
        self.organize_by_site_check = QCheckBox("\u0421\u043e\u0437\u0434\u0430\u0432\u0430\u0442\u044c \u043f\u0430\u043f\u043a\u0438 \u043f\u043e \u0441\u0430\u0439\u0442\u0443")
        self.organize_by_site_check.setChecked(True)
        self.save_log_check = QCheckBox("\u0421\u043e\u0445\u0440\u0430\u043d\u044f\u0442\u044c \u043b\u043e\u0433 \u0432 \u0444\u0430\u0439\u043b")
        self.save_log_check.setChecked(self.app_settings.save_logs_by_default)
        self.save_log_check.setToolTip(
            "\u0414\u043b\u044f \u044d\u0442\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438 \u0431\u0443\u0434\u0435\u0442 \u0441\u043e\u0437\u0434\u0430\u043d \u043e\u0442\u0434\u0435\u043b\u044c\u043d\u044b\u0439 log-\u0444\u0430\u0439\u043b \u0432 \u043f\u0430\u043f\u043a\u0435 gallery-dl-logs."
        )
        self.include_all_files_check = QCheckBox("\u0412\u0441\u0451")
        self.include_all_files_check.setChecked(self.app_settings.include_all_files)
        self.include_images_check = QCheckBox("\u0418\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u044f")
        self.include_images_check.setChecked(self.app_settings.include_images)
        self.include_videos_check = QCheckBox("\u0412\u0438\u0434\u0435\u043e")
        self.include_videos_check.setChecked(self.app_settings.include_videos)
        self.include_archives_check = QCheckBox("\u0410\u0440\u0445\u0438\u0432\u044b")
        self.include_archives_check.setChecked(self.app_settings.include_archives)
        self.include_archives_check.setToolTip(
            "\u041e\u0441\u043d\u043e\u0432\u043d\u044b\u0435 \u0430\u0440\u0445\u0438\u0432\u043d\u044b\u0435 \u0440\u0430\u0441\u0448\u0438\u0440\u0435\u043d\u0438\u044f: "
            + ARCHIVE_EXTENSIONS_HINT
        )
        self.include_custom_extensions_check = QCheckBox("\u0421\u0432\u043e\u0435")
        self.custom_extensions_edit = QLineEdit(self.app_settings.custom_extensions)
        self.custom_extensions_edit.setPlaceholderText(".psd, .epub, .pdf")
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("5, 1-20, 1:20, 1:24:3")
        self.range_edit.setToolTip(
            "\u041f\u043e\u0437\u0432\u043e\u043b\u044f\u0435\u0442 \u0441\u043a\u0430\u0447\u0430\u0442\u044c \u0442\u043e\u043b\u044c\u043a\u043e \u0447\u0430\u0441\u0442\u044c \u0444\u0430\u0439\u043b\u043e\u0432 "
            "\u043f\u043e \u043f\u043e\u0440\u044f\u0434\u043a\u043e\u0432\u044b\u043c \u043d\u043e\u043c\u0435\u0440\u0430\u043c."
        )
        if self.app_settings.custom_extensions.strip():
            self.include_custom_extensions_check.setChecked(True)

        file_types_widget = QWidget()
        file_types_layout = QHBoxLayout(file_types_widget)
        file_types_layout.setContentsMargins(0, 0, 0, 0)
        file_types_layout.addWidget(self.include_all_files_check)
        file_types_layout.addWidget(self.include_images_check)
        file_types_layout.addWidget(self.include_videos_check)
        file_types_layout.addWidget(self.include_archives_check)
        file_types_layout.addWidget(self.include_custom_extensions_check)
        file_types_layout.addWidget(self.custom_extensions_edit, 1)

        quick_layout.addWidget(self.only_new_check, 0, 0)
        quick_layout.addWidget(self.organize_by_site_check, 0, 1)
        quick_layout.addWidget(self.save_log_check, 0, 2, 1, 2)
        quick_layout.addWidget(QLabel("\u0422\u0438\u043f\u044b \u0444\u0430\u0439\u043b\u043e\u0432:"), 1, 0)
        quick_layout.addWidget(file_types_widget, 1, 1, 1, 3)
        quick_layout.addWidget(QLabel("\u041a\u0430\u043a\u0438\u0435 \u044d\u043b\u0435\u043c\u0435\u043d\u0442\u044b \u0441\u043a\u0430\u0447\u0438\u0432\u0430\u0442\u044c:"), 2, 0)
        quick_layout.addWidget(self.range_edit, 2, 1, 1, 3)
        top_layout.addLayout(quick_layout)

        naming_group = QGroupBox("\u0418\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435")
        naming_layout = QGridLayout(naming_group)
        self.naming_preset_combo = QComboBox()
        self.naming_preset_combo.addItem(
            "\u0428\u0430\u0431\u043b\u043e\u043d \u043d\u0435 \u0432\u044b\u0431\u0440\u0430\u043d",
            "",
        )
        for preset in NAMING_PRESETS:
            self.naming_preset_combo.addItem(preset.label, preset.id)
        self.naming_fields_button = QPushButton("\u0414\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0435 \u043f\u043e\u043b\u044f")
        self.naming_directory_edit = QLineEdit(self.app_settings.naming_directory_template)
        self.naming_directory_edit.setPlaceholderText(
            "\u041f\u0443\u0441\u0442\u043e = \u043a\u0430\u043a \u0443 gallery-dl, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: {category}/{user[id]}"
        )
        self.naming_filename_quick_edit = QLineEdit(self.app_settings.naming_filename_template)
        self.naming_filename_quick_edit.setPlaceholderText(
            "\u041f\u0443\u0441\u0442\u043e = \u043a\u0430\u043a \u0443 gallery-dl, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: {title}.{extension}"
        )
        self.use_original_filenames_check = QCheckBox(
            "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c \u043e\u0440\u0438\u0433\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0435 \u0438\u043c\u0435\u043d\u0430"
        )
        self.use_original_filenames_check.setChecked(self.app_settings.naming_use_original_filenames)
        self.path_compatibility_combo = QComboBox()
        self.path_compatibility_combo.addItem("\u0410\u0432\u0442\u043e", "auto")
        self.path_compatibility_combo.addItem("Windows-safe", "windows")
        self.path_compatibility_combo.addItem("ASCII-safe", "ascii")
        self._set_combo_value(
            self.path_compatibility_combo,
            self.app_settings.naming_path_compatibility_mode or "auto",
        )

        self.naming_preview_label = QLabel("-")
        self.naming_preview_label.setWordWrap(True)
        self.naming_preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        naming_layout.addWidget(QLabel("\u0428\u0430\u0431\u043b\u043e\u043d:"), 0, 0)
        naming_layout.addWidget(self.naming_preset_combo, 0, 1)
        naming_layout.addWidget(self.naming_fields_button, 0, 2)
        naming_layout.addWidget(QLabel("\u0421\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0430 \u043f\u0430\u043f\u043e\u043a:"), 1, 0)
        naming_layout.addWidget(self.naming_directory_edit, 1, 1, 1, 3)
        naming_layout.addWidget(QLabel("\u0418\u043c\u044f \u0444\u0430\u0439\u043b\u0430:"), 2, 0)
        naming_layout.addWidget(self.naming_filename_quick_edit, 2, 1, 1, 3)
        naming_layout.addWidget(self.use_original_filenames_check, 3, 0, 1, 2)
        naming_layout.addWidget(QLabel("\u0421\u043e\u0432\u043c\u0435\u0441\u0442\u0438\u043c\u043e\u0441\u0442\u044c \u0438\u043c\u0435\u043d:"), 3, 2)
        naming_layout.addWidget(self.path_compatibility_combo, 3, 3)
        naming_layout.addWidget(QLabel("\u041f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 \u043f\u0443\u0442\u0438:"), 4, 0)
        naming_layout.addWidget(self.naming_preview_label, 4, 1, 1, 3)
        top_layout.addWidget(naming_group)

        primary_actions_layout = QHBoxLayout()
        self.check_button = QPushButton("\u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c")
        self.download_button = QPushButton("\u0421\u043a\u0430\u0447\u0430\u0442\u044c")
        self.cancel_button = QPushButton("\u041e\u0442\u043c\u0435\u043d\u0438\u0442\u044c")
        self.cancel_button.setEnabled(False)
        self.supported_sites_button = QPushButton("\u041f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u043c\u044b\u0435 \u0441\u0430\u0439\u0442\u044b")
        self.advanced_button = QPushButton("\u0415\u0449\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438")

        self.check_button.setMinimumHeight(44)
        self.download_button.setMinimumHeight(44)
        self.cancel_button.setMinimumHeight(44)
        self.check_button.setMinimumWidth(150)
        self.download_button.setMinimumWidth(180)
        self.cancel_button.setMinimumWidth(150)
        self.check_button.setStyleSheet("font-weight: 600;")
        self.download_button.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.cancel_button.setStyleSheet("font-weight: 600;")

        primary_actions_layout.addWidget(self.check_button)
        primary_actions_layout.addWidget(self.download_button)
        primary_actions_layout.addWidget(self.cancel_button)
        primary_actions_layout.addStretch(1)

        secondary_actions_layout = QHBoxLayout()
        secondary_actions_layout.setSpacing(8)
        secondary_actions_layout.addWidget(self.supported_sites_button)
        secondary_actions_layout.addWidget(self.advanced_button)

        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(8)
        actions_layout.addLayout(primary_actions_layout)
        actions_layout.addLayout(secondary_actions_layout)
        top_layout.addLayout(actions_layout)

        self.current_task_label = QLabel("\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438")
        self.current_task_label.setWordWrap(True)
        self.current_task_progress = QProgressBar()
        self.current_task_progress.setRange(0, 1)
        self.current_task_progress.setValue(0)

        status_layout = QHBoxLayout()
        status_layout.addWidget(self.current_task_label, 1)
        status_layout.addWidget(self.current_task_progress, 2)
        top_layout.addLayout(status_layout)
        root.addWidget(top_card)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.queue_table = QTableWidget(0, 6)
        self.queue_table.setHorizontalHeaderLabels(
            [
                "URL",
                "\u0421\u0430\u0439\u0442",
                "\u0420\u0435\u0436\u0438\u043c",
                "\u0421\u0442\u0430\u0442\u0443\u0441",
                "\u041f\u0430\u043f\u043a\u0430",
                "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435",
            ]
        )
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.queue_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.queue_table.horizontalHeader().setStretchLastSection(False)
        for index in range(6):
            self.queue_table.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        self.queue_table.setColumnWidth(0, 320)
        self.queue_table.setColumnWidth(1, 140)
        self.queue_table.setColumnWidth(2, 120)
        self.queue_table.setColumnWidth(3, 130)
        self.queue_table.setColumnWidth(4, 260)
        self.queue_table.setColumnWidth(5, 420)

        self.log_panel = QWidget()
        log_layout = QVBoxLayout(self.log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_header = QHBoxLayout()
        log_title = QLabel("\u0416\u0443\u0440\u043d\u0430\u043b")
        log_title.setStyleSheet("font-weight: 600;")
        self.log_toggle_button = QToolButton()
        self.log_toggle_button.setText("\u0421\u043a\u0440\u044b\u0442\u044c")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        log_header.addWidget(self.log_toggle_button)
        log_layout.addLayout(log_header)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_output)

        self.splitter.addWidget(self.queue_table)
        self.splitter.addWidget(self.log_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        root.addWidget(self.splitter, 1)

    def _build_history_tab(self) -> None:
        layout = QVBoxLayout(self.history_tab)
        layout.setContentsMargins(12, 12, 12, 12)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(
            [
                "URL",
                "\u0421\u0430\u0439\u0442",
                "\u0420\u0435\u0436\u0438\u043c",
                "\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442",
                "\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0439",
            ]
        )
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setStretchLastSection(False)
        for index in range(5):
            self.history_table.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        self.history_table.setColumnWidth(0, 320)
        self.history_table.setColumnWidth(1, 140)
        self.history_table.setColumnWidth(2, 120)
        self.history_table.setColumnWidth(3, 130)
        self.history_table.setColumnWidth(4, 420)
        layout.addWidget(self.history_table)

    def _build_settings_tab(self) -> None:
        layout = QVBoxLayout(self.settings_tab)
        layout.setContentsMargins(12, 12, 12, 12)

        general_group = QGroupBox("\u041e\u0431\u0449\u0438\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438")
        form = QFormLayout(general_group)
        self.gallery_dl_path_edit = QLineEdit(self.app_settings.gallery_dl_path)
        self.gallery_dl_path_button = QPushButton("\u041e\u0431\u0437\u043e\u0440")
        path_widget = QWidget()
        path_layout = QHBoxLayout(path_widget)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.addWidget(self.gallery_dl_path_edit, 1)
        path_layout.addWidget(self.gallery_dl_path_button)

        self.default_folder_edit = QLineEdit(self.app_settings.default_download_dir)
        self.default_folder_button = QPushButton("\u041e\u0431\u0437\u043e\u0440")
        folder_widget = QWidget()
        folder_layout = QHBoxLayout(folder_widget)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(self.default_folder_edit, 1)
        folder_layout.addWidget(self.default_folder_button)

        form.addRow("\u041f\u0443\u0442\u044c \u043a gallery-dl:", path_widget)
        form.addRow("\u041f\u0430\u043f\u043a\u0430 \u043f\u043e \u0443\u043c\u043e\u043b\u0447\u0430\u043d\u0438\u044e:", folder_widget)

        self.save_settings_button = QPushButton("\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438")
        layout.addWidget(general_group)
        layout.addWidget(self.save_settings_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

    def _build_advanced_dock(self) -> None:
        self.advanced_dock = QDockWidget("\u0414\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438", self)
        self.advanced_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.advanced_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.advanced_dock)
        self.advanced_dock.hide()

        container = QWidget()
        layout = QVBoxLayout(container)

        auth_group = QGroupBox("\u0412\u0445\u043e\u0434")
        auth_form = QFormLayout(auth_group)
        self.username_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.cookies_file_edit = QLineEdit()
        self.cookies_file_button = QPushButton("\u041e\u0431\u0437\u043e\u0440")
        cookies_widget = QWidget()
        cookies_layout = QHBoxLayout(cookies_widget)
        cookies_layout.setContentsMargins(0, 0, 0, 0)
        cookies_layout.addWidget(self.cookies_file_edit, 1)
        cookies_layout.addWidget(self.cookies_file_button)
        self.browser_cookies_edit = QLineEdit(self.app_settings.last_cookies_browser)
        self.browser_cookies_edit.setPlaceholderText("firefox, chrome, edge...")
        auth_form.addRow("\u041b\u043e\u0433\u0438\u043d:", self.username_edit)
        auth_form.addRow("\u041f\u0430\u0440\u043e\u043b\u044c:", self.password_edit)
        auth_form.addRow("Cookies \u0444\u0430\u0439\u043b:", cookies_widget)
        auth_form.addRow("Cookies \u0438\u0437 \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u0430:", self.browser_cookies_edit)

        filters_group = QGroupBox("\u0424\u0438\u043b\u044c\u0442\u0440\u044b")
        filters_form = QFormLayout(filters_group)
        self.date_after_edit = QLineEdit()
        self.date_after_edit.setPlaceholderText("2026-01-01")
        filters_form.addRow("\u0414\u0430\u0442\u0430 \u043f\u043e\u0441\u043b\u0435:", self.date_after_edit)

        naming_group = QGroupBox("\u0418\u043c\u0435\u043d\u0430 \u0444\u0430\u0439\u043b\u043e\u0432")
        naming_form = QFormLayout(naming_group)
        self.filename_template_edit = QLineEdit(self.app_settings.naming_filename_template)
        self.filename_template_edit.setPlaceholderText("{filename}.{extension}")
        self.base_directory_edit = QLineEdit(self.app_settings.naming_base_directory)
        self.base_directory_edit.setPlaceholderText(
            "\u041f\u0443\u0441\u0442\u043e = \u0431\u0440\u0430\u0442\u044c \u043e\u0441\u043d\u043e\u0432\u043d\u0443\u044e \u043f\u0430\u043f\u043a\u0443 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438"
        )
        self.path_restrict_edit = QLineEdit(self.app_settings.naming_path_restrict)
        self.path_restrict_edit.setPlaceholderText("auto / windows / ascii")
        self.path_replace_edit = QLineEdit(self.app_settings.naming_path_replace)
        self.path_replace_edit.setPlaceholderText("_")
        self.path_remove_edit = QLineEdit(self.app_settings.naming_path_remove)
        self.path_remove_edit.setPlaceholderText("\\x00-\\x1f\\x7f")
        self.path_strip_edit = QLineEdit(self.app_settings.naming_path_strip)
        self.path_strip_edit.setPlaceholderText(". ")
        naming_form.addRow("\u0428\u0430\u0431\u043b\u043e\u043d \u0438\u043c\u0435\u043d\u0438:", self.filename_template_edit)
        naming_form.addRow("Base directory:", self.base_directory_edit)
        naming_form.addRow("Path restrict:", self.path_restrict_edit)
        naming_form.addRow("Path replace:", self.path_replace_edit)
        naming_form.addRow("Path remove:", self.path_remove_edit)
        naming_form.addRow("Path strip:", self.path_strip_edit)

        post_group = QGroupBox("\u041f\u043e\u0441\u043b\u0435 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438")
        post_form = QFormLayout(post_group)
        self.write_metadata_check = QCheckBox("\u0421\u043e\u0445\u0440\u0430\u043d\u044f\u0442\u044c metadata JSON")
        self.write_info_json_check = QCheckBox("\u0421\u043e\u0445\u0440\u0430\u043d\u044f\u0442\u044c info.json")
        self.write_tags_check = QCheckBox("\u0421\u043e\u0445\u0440\u0430\u043d\u044f\u0442\u044c \u0442\u0435\u0433\u0438")
        self.archive_combo = QComboBox()
        self.archive_combo.addItems(["\u041d\u0435 \u0443\u043f\u0430\u043a\u043e\u0432\u044b\u0432\u0430\u0442\u044c", "ZIP", "CBZ"])
        self.ugoira_combo = QComboBox()
        self.ugoira_combo.addItems(["\u041d\u0435 \u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c", "webm", "mp4", "gif", "copy", "zip"])
        post_form.addRow(self.write_metadata_check)
        post_form.addRow(self.write_info_json_check)
        post_form.addRow(self.write_tags_check)
        post_form.addRow("\u0410\u0440\u0445\u0438\u0432:", self.archive_combo)
        post_form.addRow("Ugoira:", self.ugoira_combo)

        network_group = QGroupBox("\u0421\u0435\u0442\u044c")
        network_form = QFormLayout(network_group)
        self.proxy_edit = QLineEdit()
        self.retries_edit = QLineEdit()
        self.timeout_edit = QLineEdit()
        network_form.addRow("\u041f\u0440\u043e\u043a\u0441\u0438:", self.proxy_edit)
        network_form.addRow("\u041f\u043e\u0432\u0442\u043e\u0440\u044b:", self.retries_edit)
        network_form.addRow("\u0422\u0430\u0439\u043c\u0430\u0443\u0442:", self.timeout_edit)

        layout.addWidget(auth_group)
        layout.addWidget(filters_group)
        layout.addWidget(naming_group)
        layout.addWidget(post_group)
        layout.addWidget(network_group)
        layout.addStretch(1)
        self.advanced_dock.setWidget(container)

    def _build_supported_sites_dock(self) -> None:
        self.supported_sites_dock = QDockWidget("\u041f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u043c\u044b\u0435 \u0441\u0430\u0439\u0442\u044b", self)
        self.supported_sites_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.supported_sites_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.supported_sites_dock.setMinimumWidth(SUPPORTED_SITES_DOCK_WIDTH)
        self.supported_sites_dock.setMaximumWidth(SUPPORTED_SITES_DOCK_WIDTH)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.supported_sites_dock)
        self.supported_sites_dock.hide()

        container = QWidget()
        layout = QVBoxLayout(container)

        source_label = QLabel(
            '<a href="https://github.com/mikf/gallery-dl/blob/master/docs/supportedsites.md">'
            "\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a: supportedsites.md \u0438\u0437 gallery-dl \u043d\u0430 GitHub"
            "</a>"
        )
        source_label.setOpenExternalLinks(True)
        source_label.setWordWrap(True)
        layout.addWidget(source_label)

        controls_layout = QHBoxLayout()
        self.supported_sites_search_edit = QLineEdit()
        self.supported_sites_search_edit.setPlaceholderText(
            "\u041f\u043e\u0438\u0441\u043a \u043f\u043e \u0441\u0430\u0439\u0442\u0443, URL \u0438\u043b\u0438 \u0432\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0441\u0442\u044f\u043c"
        )
        self.supported_sites_refresh_button = QPushButton("\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c")
        controls_layout.addWidget(self.supported_sites_search_edit, 1)
        controls_layout.addWidget(self.supported_sites_refresh_button)
        layout.addLayout(controls_layout)

        self.supported_sites_tree = QTreeWidget()
        self.supported_sites_tree.setHeaderHidden(True)
        self.supported_sites_tree.setAlternatingRowColors(True)
        layout.addWidget(self.supported_sites_tree, 1)

        details_group = QGroupBox("\u041f\u043e\u0434\u0440\u043e\u0431\u043d\u043e\u0441\u0442\u0438")
        details_form = QFormLayout(details_group)
        self.site_name_value = QLabel("\u0412\u044b\u0431\u0435\u0440\u0438 \u0441\u0430\u0439\u0442 \u0438\u0437 \u0441\u043f\u0438\u0441\u043a\u0430.")
        self.site_name_value.setWordWrap(True)
        self.site_url_value = QLabel("-")
        self.site_url_value.setOpenExternalLinks(True)
        self.site_url_value.setWordWrap(True)
        self.site_capabilities_value = QLabel("-")
        self.site_capabilities_value.setWordWrap(True)
        self.site_auth_value = QLabel("-")
        self.site_auth_value.setWordWrap(True)
        self.site_section_value = QLabel("-")
        self.site_section_value.setWordWrap(True)
        details_form.addRow("\u0421\u0430\u0439\u0442:", self.site_name_value)
        details_form.addRow("URL:", self.site_url_value)
        details_form.addRow("\u0412\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0441\u0442\u0438:", self.site_capabilities_value)
        details_form.addRow("\u0410\u0432\u0442\u043e\u0440\u0438\u0437\u0430\u0446\u0438\u044f:", self.site_auth_value)
        details_form.addRow("\u0421\u0435\u043a\u0446\u0438\u044f:", self.site_section_value)
        layout.addWidget(details_group)

        footer_layout = QVBoxLayout()
        self.supported_sites_updated_label = QLabel(
            "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0435 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435: -"
        )
        self.supported_sites_status_label = QLabel(
            "\u0421\u043f\u0438\u0441\u043e\u043a \u0441\u0430\u0439\u0442\u043e\u0432 \u0435\u0449\u0435 \u043d\u0435 \u0437\u0430\u0433\u0440\u0443\u0436\u0435\u043d."
        )
        self.supported_sites_status_label.setWordWrap(True)
        footer_layout.addWidget(self.supported_sites_updated_label)
        footer_layout.addWidget(self.supported_sites_status_label)
        layout.addLayout(footer_layout)

        self.supported_sites_dock.setWidget(container)

    def _build_statusbar(self) -> None:
        statusbar = QStatusBar()
        self.setStatusBar(statusbar)
        self.status_message = QLabel("\u0413\u043e\u0442\u043e\u0432\u043e")
        statusbar.addPermanentWidget(self.status_message)

    def _build_actions(self) -> None:
        self.menuBar().hide()

    def _set_combo_value(self, combo: QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _load_recent_destinations(self) -> None:
        combo = self.destination_edit
        combo.blockSignals(True)
        combo.clear()
        for path in self.app_settings.recent_destinations[:10]:
            combo.addItem(path)
        combo.blockSignals(False)
        current_text = (
            self.app_settings.recent_destinations[0]
            if self.app_settings.recent_destinations
            else self.app_settings.default_download_dir
        )
        combo.setCurrentText(current_text)

    def _destination_text(self) -> str:
        return self.destination_edit.currentText().strip()

    def _set_destination_text(self, value: str) -> None:
        self.destination_edit.setCurrentText(value)

    def _register_recent_destination(self, value: str) -> None:
        normalized = value.strip()
        if not normalized:
            return
        recent = [path for path in self.app_settings.recent_destinations if path.strip() and path.strip() != normalized]
        recent.insert(0, normalized)
        self.app_settings.recent_destinations = recent[:10]
        self._load_recent_destinations()

    def _write_task_log_line(self, task: DownloadTask, line: str) -> None:
        if not task.options.save_log or not task.log_file_path:
            return
        if task.id in self._failed_log_files:
            return

        log_path = Path(task.log_file_path)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            if task.id not in self._initialized_log_files:
                header = [
                    f"Время запуска: {task.created_at.strftime('%d.%m.%Y %H:%M:%S')}",
                    f"Режим: {task.mode.label}",
                    f"Сайт: {task.site}",
                    f"URL: {task.url}",
                    f"Папка: {task.target_folder}",
                    "",
                ]
                log_path.write_text("\n".join(header), encoding="utf-8")
                self._initialized_log_files.add(task.id)
            timestamp = datetime.now().strftime("%H:%M:%S")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{timestamp}] {line}\n")
        except Exception as exc:
            self._failed_log_files.add(task.id)
            self.log_output.appendPlainText(f"[Система] Не удалось записать лог задачи: {exc}")

    def _finalize_task_log(self, task: DownloadTask) -> None:
        if (
            not task.options.save_log
            or not task.log_file_path
            or task.id in self._finalized_log_files
            or task.id in self._failed_log_files
        ):
            return
        summary = f"Итог: {task.status.label}"
        if task.last_message:
            summary += f". {task.last_message}"
        self._write_task_log_line(task, summary)
        self._finalized_log_files.add(task.id)

    def _format_size(self, value: int) -> str:
        size = float(value)
        units = ("Б", "КБ", "МБ", "ГБ")
        for unit in units:
            if size < 1024 or unit == units[-1]:
                if unit == "Б":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{int(value)} Б"

    def _format_speed(self, value: float) -> str:
        if value <= 0:
            return "0 Б/с"
        size = value
        units = ("Б/с", "КБ/с", "МБ/с", "ГБ/с")
        for unit in units:
            if size < 1024 or unit == units[-1]:
                if unit == "Б/с":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{int(value)} Б/с"

    def _find_recent_partial_file(self, task: DownloadTask) -> Path | None:
        root = Path(task.target_folder)
        if not root.exists():
            return None

        created_ts = task.created_at.timestamp() - 5
        newest_path: Path | None = None
        newest_mtime = 0.0
        try:
            candidates = root.rglob("*.part")
        except Exception:
            return None

        for path in candidates:
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < created_ts:
                continue
            if stat.st_mtime >= newest_mtime:
                newest_mtime = stat.st_mtime
                newest_path = path
        return newest_path

    def _render_current_task_banner(self, task: DownloadTask | None) -> None:
        if task is None:
            self.current_task_label.setText("\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438")
            self.current_task_progress.setRange(0, 1)
            self.current_task_progress.setValue(0)
            return

        details = self._current_part_status or task.last_message or task.progress_text
        self.current_task_label.setText(f"{task.mode.label}: {task.title}\n{details}")
        self.current_task_progress.setRange(0, 0)

    def _poll_active_download_progress(self) -> None:
        if self._current_task_id is None:
            return
        task = self.tasks.get(self._current_task_id)
        if task is None or task.status is not TaskStatus.RUNNING or task.mode is not TaskMode.DOWNLOAD:
            self._download_poll_timer.stop()
            self._current_part_status = ""
            self._current_part_path = ""
            self._current_part_size = 0
            self._current_part_timestamp = 0.0
            return

        part_path = self._find_recent_partial_file(task)
        if part_path is None:
            return

        try:
            stat = part_path.stat()
        except OSError:
            return

        now = time.monotonic()
        relative_path = part_path.name
        try:
            relative_path = str(part_path.relative_to(Path(task.target_folder)))
        except ValueError:
            relative_path = part_path.name

        speed_text = ""
        part_path_text = str(part_path)
        if self._current_part_path == part_path_text and self._current_part_timestamp:
            delta_size = stat.st_size - self._current_part_size
            delta_time = now - self._current_part_timestamp
            if delta_time > 0:
                speed_text = self._format_speed(max(0, delta_size) / delta_time)

        self._current_part_path = part_path_text
        self._current_part_size = stat.st_size
        self._current_part_timestamp = now

        message = f"\u0421\u043a\u0430\u0447\u0438\u0432\u0430\u0435\u0442\u0441\u044f: {relative_path} ({self._format_size(stat.st_size)})"
        if speed_text:
            message = f"{message} • {speed_text}"
        if message == self._current_part_status:
            return

        self._current_part_status = message
        row = self.task_rows.get(task.id)
        if row is not None:
            item = self.queue_table.item(row, 5)
            if item is None:
                item = QTableWidgetItem()
                self.queue_table.setItem(row, 5, item)
            item.setText(message)
        self._render_current_task_banner(task)

    def _selected_naming_compatibility(self) -> str:
        return str(self.path_compatibility_combo.currentData() or "auto")

    def _wire_signals(self) -> None:
        self.paste_button.clicked.connect(self._paste_urls)
        self.clear_button.clicked.connect(self.urls_edit.clear)
        self.destination_button.clicked.connect(self._choose_destination)
        self.check_button.clicked.connect(lambda: self._queue_tasks(TaskMode.CHECK))
        self.download_button.clicked.connect(lambda: self._queue_tasks(TaskMode.DOWNLOAD))
        self.cancel_button.clicked.connect(self.runner.stop_current)
        self.supported_sites_button.clicked.connect(self._toggle_supported_sites)
        self.advanced_button.clicked.connect(self._toggle_advanced)
        self.log_toggle_button.clicked.connect(self._toggle_log_panel)

        self.gallery_dl_path_button.clicked.connect(self._choose_gallery_dl_path)
        self.default_folder_button.clicked.connect(self._choose_default_folder)
        self.save_settings_button.clicked.connect(self._save_settings)
        self.cookies_file_button.clicked.connect(self._choose_cookies_file)
        self.naming_preset_combo.currentIndexChanged.connect(self._apply_naming_preset)
        self.naming_fields_button.clicked.connect(self._show_available_fields)
        self.use_original_filenames_check.toggled.connect(self._sync_filename_state)
        self.include_all_files_check.toggled.connect(self._sync_file_type_controls)
        self.include_custom_extensions_check.toggled.connect(self._sync_custom_extensions_state)
        self.naming_filename_quick_edit.textChanged.connect(self._sync_filename_template_from_quick)
        self.filename_template_edit.textChanged.connect(self._sync_filename_template_from_advanced)

        preview_signals = (
            self.urls_edit.textChanged,
            self.destination_edit.editTextChanged,
            self.naming_directory_edit.textChanged,
            self.naming_filename_quick_edit.textChanged,
            self.use_original_filenames_check.toggled,
            self.path_compatibility_combo.currentIndexChanged,
            self.organize_by_site_check.toggled,
            self.base_directory_edit.textChanged,
            self.path_restrict_edit.textChanged,
            self.path_replace_edit.textChanged,
            self.path_remove_edit.textChanged,
            self.path_strip_edit.textChanged,
        )
        for signal in preview_signals:
            signal.connect(self._update_naming_preview)

        self.supported_sites_search_edit.textChanged.connect(self._filter_supported_sites)
        self.supported_sites_tree.currentItemChanged.connect(self._on_supported_site_selected)
        self.supported_sites_refresh_button.clicked.connect(lambda: self._start_supported_sites_refresh(manual=True))

        self.runner.task_changed.connect(self._upsert_task)
        self.runner.task_output.connect(self._append_log)
        self.runner.queue_state_changed.connect(self._update_queue_state)
        self.runner.current_task_changed.connect(self._update_current_task_banner)
        self._sync_file_type_controls()
        self._sync_filename_state()
        self._update_naming_preview()

    def _toggle_advanced(self) -> None:
        visible = self.advanced_dock.isVisible()
        self.advanced_dock.setVisible(not visible)
        if not visible:
            self.advanced_dock.raise_()

    def _toggle_supported_sites(self) -> None:
        visible = self.supported_sites_dock.isVisible()
        self.supported_sites_dock.setVisible(not visible)
        if not visible:
            self.supported_sites_dock.raise_()

    def _toggle_log_panel(self) -> None:
        visible = self.log_panel.isVisible()
        self.log_panel.setVisible(not visible)
        self.log_toggle_button.setText("\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c" if visible else "\u0421\u043a\u0440\u044b\u0442\u044c")

    def _sync_filename_state(self) -> None:
        use_original = self.use_original_filenames_check.isChecked()
        self.naming_filename_quick_edit.setEnabled(not use_original)
        self.filename_template_edit.setEnabled(not use_original)
        if use_original:
            self.naming_filename_quick_edit.setPlaceholderText(
                "\u0412\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e: \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u044e\u0442\u0441\u044f \u043e\u0440\u0438\u0433\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0435 \u0438\u043c\u0435\u043d\u0430"
            )
        else:
            self.naming_filename_quick_edit.setPlaceholderText(
                "\u041f\u0443\u0441\u0442\u043e = \u043a\u0430\u043a \u0443 gallery-dl, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: {title}.{extension}"
            )
        self._update_naming_preview()

    def _sync_custom_extensions_state(self) -> None:
        if self.include_all_files_check.isChecked():
            self.custom_extensions_edit.setEnabled(False)
            return
        enabled = self.include_custom_extensions_check.isChecked()
        self.custom_extensions_edit.setEnabled(enabled)

    def _sync_file_type_controls(self) -> None:
        include_all = self.include_all_files_check.isChecked()
        for checkbox in (
            self.include_images_check,
            self.include_videos_check,
            self.include_archives_check,
            self.include_custom_extensions_check,
        ):
            checkbox.setEnabled(not include_all)
        self._sync_custom_extensions_state()

    def _sync_filename_template_from_quick(self, value: str) -> None:
        if self.filename_template_edit.text() != value:
            self.filename_template_edit.blockSignals(True)
            self.filename_template_edit.setText(value)
            self.filename_template_edit.blockSignals(False)

    def _sync_filename_template_from_advanced(self, value: str) -> None:
        if self.naming_filename_quick_edit.text() != value:
            self.naming_filename_quick_edit.blockSignals(True)
            self.naming_filename_quick_edit.setText(value)
            self.naming_filename_quick_edit.blockSignals(False)
        self._update_naming_preview()

    def _apply_naming_preset(self, _index: int | None = None) -> None:
        preset_id = str(self.naming_preset_combo.currentData() or "")
        preset = get_preset_by_id(preset_id)
        if preset is None:
            return
        self.naming_directory_edit.setText(preset.directory_template)
        self.naming_filename_quick_edit.setText(preset.filename_template)
        self.use_original_filenames_check.setChecked(preset.use_original_filenames)
        self.status_message.setText(preset.description)
        self._update_naming_preview()

    def _open_advanced_naming(self) -> None:
        self.advanced_dock.show()
        self.advanced_dock.raise_()
        self.base_directory_edit.setFocus()

    def _show_available_fields(self) -> None:
        urls = [line.strip() for line in self.urls_edit.toPlainText().splitlines() if line.strip()]
        if not urls:
            self._show_keyword_browser_dialog(
                entries=build_common_keyword_entries(),
                note=(
                    "Ссылка еще не указана, поэтому показан общий набор часто используемых полей. "
                    "Когда ты добавишь URL, здесь появятся точные поля для конкретного сайта."
                ),
                raw_output="",
            )
            return

        success, output = self.runner.inspect_keywords(urls[0])
        if success:
            entries = parse_gallery_dl_keywords(output)
            if entries:
                self._show_keyword_browser_dialog(
                    entries=entries,
                    note=(
                        "Показаны поля, которые gallery-dl вернул для первой ссылки из списка. "
                        "Их можно вставлять в шаблон папки или имени файла."
                    ),
                    raw_output=output,
                )
                return

        note = (
            "Не удалось получить точный список полей от gallery-dl. "
            "Показан общий набор, который подходит для большинства сайтов."
        )
        if output:
            note += "\n\nТехническое сообщение:\n" + output
        self._show_keyword_browser_dialog(
            entries=build_common_keyword_entries(urls[0]),
            note=note,
            raw_output=output,
        )

    def _show_keyword_browser_dialog(
        self,
        *,
        entries: list[NamingKeywordEntry],
        note: str,
        raw_output: str,
    ) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Доступные поля")
        dialog.resize(1080, 680)

        layout = QVBoxLayout(dialog)

        note_label = QLabel(note)
        note_label.setWordWrap(True)
        note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(note_label)

        helper_label = QLabel(
            "Выбери поле в списке ниже. Вставка идет в текущее место курсора в шаблоне."
        )
        helper_label.setStyleSheet("color: #555;")
        helper_label.setWordWrap(True)
        layout.addWidget(helper_label)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Поиск:"))
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("Например: title, date, filename, user")
        search_layout.addWidget(search_edit, 1)
        layout.addLayout(search_layout)

        tree = QTreeWidget()
        tree.setColumnCount(4)
        tree.setHeaderLabels(("Поле", "Пример", "Что означает", "Где использовать"))
        tree.setAlternatingRowColors(True)
        tree.setRootIsDecorated(True)
        tree.setUniformRowHeights(False)
        tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        tree.setColumnWidth(0, 240)
        tree.setColumnWidth(1, 220)
        tree.setColumnWidth(3, 150)
        self._populate_keyword_tree(tree, entries)
        layout.addWidget(tree, 1)

        selected_group = QGroupBox("Выбранное поле")
        selected_layout = QGridLayout(selected_group)
        token_value = QLabel("Выбери поле в списке.")
        token_value.setWordWrap(True)
        token_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        description_value = QLabel("-")
        description_value.setWordWrap(True)
        description_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        selected_layout.addWidget(QLabel("Шаблон:"), 0, 0)
        selected_layout.addWidget(token_value, 0, 1)
        selected_layout.addWidget(QLabel("Пояснение:"), 1, 0)
        selected_layout.addWidget(description_value, 1, 1)
        layout.addWidget(selected_group)

        buttons_layout = QHBoxLayout()
        insert_directory_button = QPushButton("Вставить в папку")
        insert_filename_button = QPushButton("Вставить в имя файла")
        raw_button = QPushButton("Показать сырой вывод")
        close_button = QPushButton("Закрыть")
        insert_directory_button.setEnabled(False)
        insert_filename_button.setEnabled(False)
        raw_button.setEnabled(bool(raw_output.strip()))
        buttons_layout.addWidget(insert_directory_button)
        buttons_layout.addWidget(insert_filename_button)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(raw_button)
        buttons_layout.addWidget(close_button)
        layout.addLayout(buttons_layout)

        def current_entry() -> NamingKeywordEntry | None:
            item = tree.currentItem()
            if item is None:
                return None
            entry = item.data(0, Qt.ItemDataRole.UserRole)
            return entry if isinstance(entry, NamingKeywordEntry) else None

        def sync_selection() -> None:
            entry = current_entry()
            has_entry = entry is not None
            insert_directory_button.setEnabled(has_entry)
            insert_filename_button.setEnabled(has_entry)
            if not has_entry:
                token_value.setText("Выбери поле в списке.")
                description_value.setText("-")
                return
            token_value.setText(entry.template)
            description_value.setText(
                f"{entry.description}\n\nПример: {entry.sample}\nГде использовать: {entry.usage}"
            )

        def insert_into_directory() -> None:
            entry = current_entry()
            if entry is None:
                return
            self._insert_text_into_line_edit(self.naming_directory_edit, entry.template)
            self.naming_directory_edit.setFocus()
            self.status_message.setText(f"В шаблон папок вставлено {entry.template}")

        def insert_into_filename() -> None:
            entry = current_entry()
            if entry is None:
                return
            if self.use_original_filenames_check.isChecked():
                self.use_original_filenames_check.setChecked(False)
            self._insert_text_into_line_edit(self.naming_filename_quick_edit, entry.template)
            self.naming_filename_quick_edit.setFocus()
            self.status_message.setText(f"В шаблон имени файла вставлено {entry.template}")

        search_edit.textChanged.connect(lambda text: self._filter_keyword_tree(tree, text))
        tree.currentItemChanged.connect(lambda _current, _previous: sync_selection())
        insert_directory_button.clicked.connect(insert_into_directory)
        insert_filename_button.clicked.connect(insert_into_filename)
        raw_button.clicked.connect(
            lambda: self._show_readonly_text_dialog("Сырой вывод gallery-dl", raw_output)
        )
        close_button.clicked.connect(dialog.close)

        self._filter_keyword_tree(tree, "")
        sync_selection()
        dialog.exec()

    def _populate_keyword_tree(
        self,
        tree: QTreeWidget,
        entries: list[NamingKeywordEntry],
    ) -> None:
        tree.clear()
        grouped: dict[str, list[NamingKeywordEntry]] = {group: [] for group in GROUP_ORDER}
        for entry in entries:
            grouped.setdefault(entry.group, []).append(entry)

        for group_name in GROUP_ORDER:
            group_entries = grouped.get(group_name, [])
            if not group_entries:
                continue
            group_item = QTreeWidgetItem([group_name])
            group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            group_item.setFirstColumnSpanned(True)
            tree.addTopLevelItem(group_item)

            for entry in group_entries:
                item = QTreeWidgetItem(
                    [
                        entry.name,
                        entry.sample,
                        entry.description,
                        entry.usage,
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, entry)
                item.setToolTip(0, entry.template)
                item.setToolTip(1, entry.sample)
                item.setToolTip(2, entry.description)
                item.setToolTip(3, entry.usage)
                group_item.addChild(item)

            group_item.setExpanded(group_name in {"Полезно для папок", "Полезно для файлов"})

    def _filter_keyword_tree(self, tree: QTreeWidget, text: str) -> None:
        query = text.strip().lower()
        first_visible_item: QTreeWidgetItem | None = None

        for index in range(tree.topLevelItemCount()):
            group_item = tree.topLevelItem(index)
            visible_children = 0
            for child_index in range(group_item.childCount()):
                child = group_item.child(child_index)
                entry = child.data(0, Qt.ItemDataRole.UserRole)
                visible = True
                if isinstance(entry, NamingKeywordEntry) and query:
                    visible = query in entry.search_text
                child.setHidden(not visible)
                if visible:
                    visible_children += 1
                    if first_visible_item is None:
                        first_visible_item = child

            group_item.setHidden(visible_children == 0)
            if query:
                group_item.setExpanded(visible_children > 0)

        current = tree.currentItem()
        if current is not None and current.isHidden():
            tree.setCurrentItem(None)

        if first_visible_item is not None and tree.currentItem() is None:
            tree.setCurrentItem(first_visible_item)

    def _insert_text_into_line_edit(self, line_edit: QLineEdit, text: str) -> None:
        line_edit.insert(text)

    def _show_readonly_text_dialog(self, title: str, text: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 520)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.close)
        layout.addWidget(buttons)
        dialog.exec()

    def _update_naming_preview(self) -> None:
        url = ""
        for line in self.urls_edit.toPlainText().splitlines():
            stripped = line.strip()
            if stripped:
                url = stripped
                break

        preview, error = build_path_preview(
            destination=self._destination_text() or self.app_settings.default_download_dir,
            url=url,
            directory_template=self.naming_directory_edit.text().strip(),
            filename_template=self.naming_filename_quick_edit.text().strip(),
            use_original_filenames=self.use_original_filenames_check.isChecked(),
            path_compatibility_mode=self.path_restrict_edit.text().strip() or self._selected_naming_compatibility(),
            organize_by_site=self.organize_by_site_check.isChecked(),
            base_directory=self.base_directory_edit.text().strip(),
            path_replace=self.path_replace_edit.text(),
            path_remove=self.path_remove_edit.text(),
            path_strip=self.path_strip_edit.text(),
        )
        if error:
            self.naming_preview_label.setText(f"\u041e\u0448\u0438\u0431\u043a\u0430 preview: {error}")
            self.naming_preview_label.setStyleSheet("color: #b00020;")
            return

        self.naming_preview_label.setText(preview or "-")
        self.naming_preview_label.setStyleSheet("")

    def _initialize_supported_sites(self) -> None:
        cached = self.supported_sites_service.load_cached()
        if cached is not None:
            self._apply_supported_sites_payload(cached)
            self.supported_sites_status_label.setText(
                "\u041f\u043e\u043a\u0430\u0437\u0430\u043d \u043a\u044d\u0448\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0439 \u0441\u043f\u0438\u0441\u043e\u043a \u0441\u0430\u0439\u0442\u043e\u0432."
            )
            if self.supported_sites_service.needs_refresh(cached):
                QTimer.singleShot(0, lambda: self._start_supported_sites_refresh(manual=False))
            return

        self.supported_sites_status_label.setText(
            "\u0421\u043f\u0438\u0441\u043e\u043a \u0441\u0430\u0439\u0442\u043e\u0432 \u0437\u0430\u0433\u0440\u0443\u0436\u0430\u0435\u0442\u0441\u044f \u0441 GitHub..."
        )
        QTimer.singleShot(0, lambda: self._start_supported_sites_refresh(manual=False))

    def _start_supported_sites_refresh(self, *, manual: bool) -> None:
        if self._supported_sites_refresh_active:
            if manual:
                self.supported_sites_status_label.setText(
                    "\u041e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 \u0443\u0436\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u044f\u0435\u0442\u0441\u044f."
                )
            return

        self._supported_sites_refresh_active = True
        self.supported_sites_refresh_button.setEnabled(False)
        self.supported_sites_status_label.setText(
            "\u041e\u0431\u043d\u043e\u0432\u043b\u044f\u044e \u0441\u043f\u0438\u0441\u043e\u043a \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u043c\u044b\u0445 \u0441\u0430\u0439\u0442\u043e\u0432..."
        )

        def worker() -> None:
            try:
                payload = self.supported_sites_service.fetch_latest()
            except Exception as exc:
                self.supported_sites_failed.emit(str(exc), manual)
                return
            self.supported_sites_loaded.emit(payload, manual)

        self._supported_sites_thread = threading.Thread(target=worker, daemon=True)
        self._supported_sites_thread.start()

    def _on_supported_sites_loaded(self, payload: object, manual: bool) -> None:
        self._supported_sites_refresh_active = False
        self.supported_sites_refresh_button.setEnabled(True)

        if not isinstance(payload, SupportedSitesPayload):
            self._on_supported_sites_failed("\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442 \u043e\u0442 GitHub.", manual)
            return

        self._apply_supported_sites_payload(payload)
        self.supported_sites_status_label.setText(
            "\u0421\u043f\u0438\u0441\u043e\u043a \u0441\u0430\u0439\u0442\u043e\u0432 \u0430\u043a\u0442\u0443\u0430\u043b\u0435\u043d."
        )
        if manual:
            self.status_message.setText(
                "\u0421\u043f\u0438\u0441\u043e\u043a \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u043c\u044b\u0445 \u0441\u0430\u0439\u0442\u043e\u0432 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d"
            )

    def _on_supported_sites_failed(self, message: str, manual: bool) -> None:
        self._supported_sites_refresh_active = False
        self.supported_sites_refresh_button.setEnabled(True)

        if self.supported_sites_payload is None:
            self.supported_sites_status_label.setText(
                "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0437\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c \u0441\u043f\u0438\u0441\u043e\u043a \u0441\u0430\u0439\u0442\u043e\u0432. "
                "\u041f\u0440\u043e\u0432\u0435\u0440\u044c \u0441\u0435\u0442\u044c \u0438\u043b\u0438 \u043f\u043e\u043f\u0440\u043e\u0431\u0443\u0439 \u043f\u043e\u0437\u0436\u0435."
            )
        else:
            self.supported_sites_status_label.setText(
                "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u0441\u043f\u0438\u0441\u043e\u043a. "
                "\u041f\u043e\u043a\u0430\u0437\u0430\u043d \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043d\u044b\u0439 \u043a\u044d\u0448."
            )

        if manual:
            self.status_message.setText(message)

    def _apply_supported_sites_payload(self, payload: SupportedSitesPayload) -> None:
        self.supported_sites_payload = payload
        self.supported_sites_tree.clear()

        section_items: dict[str, QTreeWidgetItem] = {}
        first_site_item: QTreeWidgetItem | None = None

        for site in payload.sites:
            section_name = site.section or DEFAULT_SECTION
            if section_name not in section_items:
                section_item = QTreeWidgetItem([section_name])
                section_item.setFlags(section_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                section_item.setToolTip(0, section_name)
                self.supported_sites_tree.addTopLevelItem(section_item)
                section_items[section_name] = section_item

            item = QTreeWidgetItem([site.name])
            item.setToolTip(0, site.tooltip_text)
            item.setData(0, Qt.ItemDataRole.UserRole, site)
            section_items[section_name].addChild(item)
            if first_site_item is None:
                first_site_item = item

        if section_items:
            next(iter(section_items.values())).setExpanded(True)

        self.supported_sites_updated_label.setText(
            "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0435 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435: "
            f"{self._format_timestamp(payload.fetched_at)}"
        )
        self._filter_supported_sites(self.supported_sites_search_edit.text())

        if first_site_item is not None and self.supported_sites_tree.currentItem() is None:
            self.supported_sites_tree.setCurrentItem(first_site_item)

    def _filter_supported_sites(self, text: str) -> None:
        query = text.strip().lower()
        first_visible_item: QTreeWidgetItem | None = None

        for index in range(self.supported_sites_tree.topLevelItemCount()):
            section_item = self.supported_sites_tree.topLevelItem(index)
            visible_children = 0

            for child_index in range(section_item.childCount()):
                child = section_item.child(child_index)
                entry = child.data(0, Qt.ItemDataRole.UserRole)
                visible = True
                if isinstance(entry, SupportedSiteEntry) and query:
                    visible = query in entry.search_text
                child.setHidden(not visible)
                if visible:
                    visible_children += 1
                    if first_visible_item is None:
                        first_visible_item = child

            section_item.setHidden(visible_children == 0)
            if query:
                section_item.setExpanded(visible_children > 0)

        current = self.supported_sites_tree.currentItem()
        if current is not None and current.isHidden():
            self.supported_sites_tree.setCurrentItem(None)

        if first_visible_item is not None and self.supported_sites_tree.currentItem() is None:
            self.supported_sites_tree.setCurrentItem(first_visible_item)
        elif first_visible_item is None:
            self._clear_supported_site_details()

    def _on_supported_site_selected(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            self._clear_supported_site_details()
            return

        entry = current.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(entry, SupportedSiteEntry):
            self._show_supported_site_details(entry)
            return

        self._clear_supported_site_details()

    def _show_supported_site_details(self, entry: SupportedSiteEntry) -> None:
        self.site_name_value.setText(entry.name or "-")
        if entry.url:
            self.site_url_value.setText(f'<a href="{entry.url}">{entry.url}</a>')
        else:
            self.site_url_value.setText("-")
        self.site_capabilities_value.setText(entry.capabilities or "-")
        self.site_auth_value.setText(entry.auth or "-")
        self.site_section_value.setText(entry.section or DEFAULT_SECTION)

    def _clear_supported_site_details(self) -> None:
        self.site_name_value.setText("\u0412\u044b\u0431\u0435\u0440\u0438 \u0441\u0430\u0439\u0442 \u0438\u0437 \u0441\u043f\u0438\u0441\u043a\u0430.")
        self.site_url_value.setText("-")
        self.site_capabilities_value.setText("-")
        self.site_auth_value.setText("-")
        self.site_section_value.setText("-")

    def _format_timestamp(self, value: str) -> str:
        if not value:
            return "-"
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return value
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%d.%m.%Y %H:%M")

    def _paste_urls(self) -> None:
        text = _extract_clipboard_urls_text()
        if not text:
            return
        current = self.urls_edit.toPlainText().strip()
        self.urls_edit.setPlainText(current + "\n" + text if current else text)

    def _choose_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "\u0412\u044b\u0431\u0435\u0440\u0438 \u043f\u0430\u043f\u043a\u0443 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438",
            self._destination_text() or self.default_folder_edit.text(),
        )
        if folder:
            self._register_recent_destination(folder)
            self.settings_service.save(self.app_settings)

    def _choose_default_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "\u041f\u0430\u043f\u043a\u0430 \u043f\u043e \u0443\u043c\u043e\u043b\u0447\u0430\u043d\u0438\u044e",
            self.default_folder_edit.text() or str(Path.home() / "Downloads"),
        )
        if folder:
            self.default_folder_edit.setText(folder)

    def _choose_gallery_dl_path(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "\u0423\u043a\u0430\u0436\u0438 \u043f\u0443\u0442\u044c \u043a gallery-dl",
            str(Path.home()),
            "\u0418\u0441\u043f\u043e\u043b\u043d\u044f\u0435\u043c\u044b\u0435 \u0444\u0430\u0439\u043b\u044b (*.exe);;\u0412\u0441\u0435 \u0444\u0430\u0439\u043b\u044b (*.*)",
        )
        if file_path:
            self.gallery_dl_path_edit.setText(file_path)

    def _choose_cookies_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "\u0412\u044b\u0431\u0435\u0440\u0438 cookies \u0444\u0430\u0439\u043b",
            str(Path.home()),
            "Text files (*.txt *.json);;All files (*.*)",
        )
        if file_path:
            self.cookies_file_edit.setText(file_path)

    def _save_settings(self) -> None:
        self.app_settings = AppSettings(
            gallery_dl_path=self.gallery_dl_path_edit.text().strip() or "gallery-dl",
            default_download_dir=self.default_folder_edit.text().strip() or str(Path.home() / "Downloads"),
            recent_destinations=list(self.app_settings.recent_destinations),
            last_cookies_browser=self.browser_cookies_edit.text().strip(),
            save_logs_by_default=self.save_log_check.isChecked(),
            include_all_files=self.include_all_files_check.isChecked(),
            include_images=self.include_images_check.isChecked(),
            include_videos=self.include_videos_check.isChecked(),
            include_archives=self.include_archives_check.isChecked(),
            custom_extensions=self.custom_extensions_edit.text().strip() if self.include_custom_extensions_check.isChecked() else "",
            naming_base_directory=self.base_directory_edit.text().strip(),
            naming_directory_template=self.naming_directory_edit.text().strip(),
            naming_filename_template=self.naming_filename_quick_edit.text().strip(),
            naming_use_original_filenames=self.use_original_filenames_check.isChecked(),
            naming_path_compatibility_mode=self._selected_naming_compatibility(),
            naming_path_restrict=self.path_restrict_edit.text().strip(),
            naming_path_replace=self.path_replace_edit.text(),
            naming_path_remove=self.path_remove_edit.text(),
            naming_path_strip=self.path_strip_edit.text(),
        )
        self.settings_service.save(self.app_settings)
        self.runner.set_gallery_dl_path(self.app_settings.gallery_dl_path)
        if not self._destination_text():
            self._set_destination_text(self.app_settings.default_download_dir)
        self.status_message.setText("\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b")

    def _queue_tasks(self, mode: TaskMode) -> None:
        urls = [line.strip() for line in self.urls_edit.toPlainText().splitlines() if line.strip()]
        if not urls:
            QMessageBox.information(
                self,
                "\u041d\u0435\u0442 \u0441\u0441\u044b\u043b\u043e\u043a",
                "\u0414\u043e\u0431\u0430\u0432\u044c \u0445\u043e\u0442\u044f \u0431\u044b \u043e\u0434\u043d\u0443 \u0441\u0441\u044b\u043b\u043a\u0443.",
            )
            return

        destination = self._destination_text() or self.app_settings.default_download_dir
        if not destination:
            QMessageBox.warning(
                self,
                "\u041d\u0435\u0442 \u043f\u0430\u043f\u043a\u0438",
                "\u0423\u043a\u0430\u0436\u0438 \u043f\u0430\u043f\u043a\u0443 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f.",
            )
            return

        if not self._has_selected_file_types():
            QMessageBox.warning(
                self,
                "\u041d\u0435 \u0432\u044b\u0431\u0440\u0430\u043d\u044b \u0442\u0438\u043f\u044b \u0444\u0430\u0439\u043b\u043e\u0432",
                "\u041e\u0442\u043c\u0435\u0442\u044c \u0445\u043e\u0442\u044f \u0431\u044b \u043e\u0434\u0438\u043d \u0442\u0438\u043f \u0444\u0430\u0439\u043b\u043e\u0432 \u0438\u043b\u0438 \u0443\u043a\u0430\u0436\u0438 \u0441\u0432\u043e\u0438 \u0440\u0430\u0441\u0448\u0438\u0440\u0435\u043d\u0438\u044f.",
            )
            return

        self._register_recent_destination(destination)
        options = self._collect_task_options(destination)
        tasks = [DownloadTask(url=url, mode=mode, options=options) for url in urls]
        self.runner.enqueue(tasks)
        self.settings_service.save(self.app_settings)
        self.status_message.setText(f"\u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e \u0437\u0430\u0434\u0430\u0447: {len(tasks)}")

    def _collect_task_options(self, destination: str) -> TaskOptions:
        archive_map = {0: "none", 1: "zip", 2: "cbz"}
        ugoira_map = {0: "none", 1: "webm", 2: "mp4", 3: "gif", 4: "copy", 5: "zip"}
        return TaskOptions(
            destination=destination,
            organize_by_site=self.organize_by_site_check.isChecked(),
            only_new=self.only_new_check.isChecked(),
            save_log=self.save_log_check.isChecked(),
            include_all_files=self.include_all_files_check.isChecked(),
            include_images=self.include_images_check.isChecked(),
            include_videos=self.include_videos_check.isChecked(),
            include_archives=self.include_archives_check.isChecked(),
            custom_extensions=self.custom_extensions_edit.text().strip() if self.include_custom_extensions_check.isChecked() else "",
            base_directory=self.base_directory_edit.text().strip(),
            directory_template=self.naming_directory_edit.text().strip(),
            range_text=self.range_edit.text().strip(),
            date_after=self.date_after_edit.text().strip(),
            username=self.username_edit.text().strip(),
            password=self.password_edit.text(),
            cookies_file=self.cookies_file_edit.text().strip(),
            cookies_from_browser=self.browser_cookies_edit.text().strip(),
            filename_template=self.naming_filename_quick_edit.text().strip(),
            use_original_filenames=self.use_original_filenames_check.isChecked(),
            path_compatibility_mode=self._selected_naming_compatibility(),
            path_restrict=self.path_restrict_edit.text().strip(),
            path_replace=self.path_replace_edit.text(),
            path_remove=self.path_remove_edit.text(),
            path_strip=self.path_strip_edit.text(),
            write_metadata=self.write_metadata_check.isChecked(),
            write_info_json=self.write_info_json_check.isChecked(),
            write_tags=self.write_tags_check.isChecked(),
            archive_format=archive_map[self.archive_combo.currentIndex()],
            ugoira_format=ugoira_map[self.ugoira_combo.currentIndex()],
            proxy_url=self.proxy_edit.text().strip(),
            retries=self.retries_edit.text().strip(),
            timeout=self.timeout_edit.text().strip(),
        )

    def _has_selected_file_types(self) -> bool:
        if self.include_all_files_check.isChecked():
            return True
        return any(
            (
                self.include_images_check.isChecked(),
                self.include_videos_check.isChecked(),
                self.include_archives_check.isChecked(),
                self.include_custom_extensions_check.isChecked() and self.custom_extensions_edit.text().strip(),
            )
        )

    def _upsert_task(self, task: DownloadTask) -> None:
        self.tasks[task.id] = task
        if task.id not in self.task_rows:
            row = self.queue_table.rowCount()
            self.queue_table.insertRow(row)
            self.task_rows[task.id] = row
        row = self.task_rows[task.id]
        values = [
            task.title,
            task.site,
            task.mode.label,
            task.status.label,
            task.target_folder,
            task.last_message or task.progress_text,
        ]
        for column, value in enumerate(values):
            item = self.queue_table.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                self.queue_table.setItem(row, column, item)
            item.setText(value)

        if task.id == self._current_task_id:
            if task.status is not TaskStatus.RUNNING or task.mode is not TaskMode.DOWNLOAD:
                self._current_part_status = ""
            self._render_current_task_banner(task)

        if task.status in {TaskStatus.SUCCESS, TaskStatus.ERROR, TaskStatus.CANCELLED}:
            self._upsert_history(task)

    def _upsert_history(self, task: DownloadTask) -> None:
        if task.id not in self.history_rows:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            self.history_rows[task.id] = row
        row = self.history_rows[task.id]
        values = [
            task.title,
            task.site,
            task.mode.label,
            task.status.label,
            task.last_message,
        ]
        for column, value in enumerate(values):
            item = self.history_table.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                self.history_table.setItem(row, column, item)
            item.setText(value)
        self._finalize_task_log(task)

    def _append_log(self, task_id: str, message: str, stream: str) -> None:
        task = self.tasks.get(task_id)
        prefix = ""
        if task is not None:
            prefix = f"[{task.mode.label} | {task.site}] "
        if stream == "stderr":
            prefix += "ERR "
        elif stream == "meta":
            prefix += "CMD "
        line = prefix + message
        self.log_output.appendPlainText(line)
        if task is not None:
            self._write_task_log_line(task, line)

    def _update_queue_state(self, busy: bool) -> None:
        self.cancel_button.setEnabled(busy)

    def _update_current_task_banner(self, task: DownloadTask | None) -> None:
        self._current_task_id = task.id if task is not None else None
        self._current_part_status = ""
        self._current_part_path = ""
        self._current_part_size = 0
        self._current_part_timestamp = 0.0
        if task is None:
            self._download_poll_timer.stop()
            self._render_current_task_banner(None)
            return

        if task.mode is TaskMode.DOWNLOAD and task.status is TaskStatus.RUNNING:
            self._download_poll_timer.start()
        else:
            self._download_poll_timer.stop()
        self._render_current_task_banner(task)
