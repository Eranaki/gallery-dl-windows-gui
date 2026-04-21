from __future__ import annotations

import sys
import re
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QProcess, QTimer, Signal
from PySide6.QtGui import QGuiApplication
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
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
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
    build_common_keyword_entries,
    build_path_preview,
    get_group_order,
    get_naming_presets,
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
        self.language = self.app_settings.language if self.app_settings.language in {"ru", "en"} else "ru"
        self.runner = GalleryDlRunner(self.app_settings.gallery_dl_path, self.language)
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
        self.history_dialog: QDialog | None = None
        self.history_dialog_table: QTableWidget | None = None
        self._download_poll_timer = QTimer(self)
        self._download_poll_timer.setInterval(1500)
        self._download_poll_timer.timeout.connect(self._poll_active_download_progress)

        self.setWindowTitle(self._txt("gallery-dl GUI", "gallery-dl GUI"))
        self.resize(1360, 860)

        self._build_ui()
        self._wire_signals()

        self.supported_sites_loaded.connect(self._on_supported_sites_loaded)
        self.supported_sites_failed.connect(self._on_supported_sites_failed)
        self.language_combo.currentIndexChanged.connect(self._change_language)

        self._initialize_supported_sites()

    def _txt(self, ru: str, en: str) -> str:
        return ru if self.language == "ru" else en

    def _build_ui(self) -> None:
        self.downloads_tab = QWidget()
        self.setCentralWidget(self.downloads_tab)

        self._build_downloads_tab()
        self._build_hidden_history_storage()
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

        title = QLabel(self._txt("Новая задача", "New task"))
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        top_layout.addWidget(title)

        urls_layout = QHBoxLayout()
        self.urls_edit = UrlInputTextEdit()
        self.urls_edit.setPlaceholderText(
            self._txt(
                "Вставь одну или несколько ссылок. Каждая ссылка с новой строки.",
                "Paste one or more URLs. Put each URL on a new line.",
            )
        )
        self.urls_edit.setMinimumHeight(120)
        urls_layout.addWidget(self.urls_edit, 1)

        url_buttons_layout = QVBoxLayout()
        url_buttons_layout.setSpacing(8)
        self.paste_button = QPushButton(self._txt("Вставить", "Paste"))
        self.clear_button = QPushButton(self._txt("Очистить", "Clear"))
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
        self.destination_button = QPushButton(self._txt("Обзор", "Browse"))
        path_layout.addWidget(QLabel(self._txt("Папка:", "Folder:")))
        path_layout.addWidget(self.destination_edit, 1)
        path_layout.addWidget(self.destination_button)
        top_layout.addLayout(path_layout)

        quick_layout = QGridLayout()
        self.only_new_check = QCheckBox(self._txt("Только новое", "Only new"))
        self.only_new_check.setChecked(True)
        self.organize_by_site_check = QCheckBox(self._txt("Создавать папки по сайту", "Create folders by site"))
        self.organize_by_site_check.setChecked(True)
        self.save_log_check = QCheckBox(self._txt("Сохранять лог в файл", "Save log to file"))
        self.save_log_check.setChecked(self.app_settings.save_logs_by_default)
        self.save_log_check.setToolTip(
            self._txt(
                "Для этой задачи будет создан отдельный log-файл в папке gallery-dl-logs.",
                "This task will create a separate log file in the gallery-dl-logs folder.",
            )
        )
        self.include_all_files_check = QCheckBox(self._txt("Всё", "All"))
        self.include_all_files_check.setChecked(self.app_settings.include_all_files)
        self.include_images_check = QCheckBox(self._txt("Изображения", "Images"))
        self.include_images_check.setChecked(self.app_settings.include_images)
        self.include_videos_check = QCheckBox(self._txt("Видео", "Videos"))
        self.include_videos_check.setChecked(self.app_settings.include_videos)
        self.include_archives_check = QCheckBox(self._txt("Архивы", "Archives"))
        self.include_archives_check.setChecked(self.app_settings.include_archives)
        self.include_archives_check.setToolTip(
            self._txt("Основные архивные расширения: ", "Common archive extensions: ")
            + ARCHIVE_EXTENSIONS_HINT
        )
        self.include_custom_extensions_check = QCheckBox(self._txt("Свое", "Custom"))
        self.custom_extensions_edit = QLineEdit(self.app_settings.custom_extensions)
        self.custom_extensions_edit.setPlaceholderText(".psd, .epub, .pdf")
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("5, 1-20, 1:20, 1:24:3")
        self.range_edit.setToolTip(
            self._txt(
                "Позволяет скачать только часть файлов по порядковым номерам.",
                "Lets you download only part of the files by their index numbers.",
            )
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
        quick_layout.addWidget(QLabel(self._txt("Типы файлов:", "File types:")), 1, 0)
        quick_layout.addWidget(file_types_widget, 1, 1, 1, 3)
        quick_layout.addWidget(QLabel(self._txt("Какие элементы скачивать:", "Which items to download:")), 2, 0)
        quick_layout.addWidget(self.range_edit, 2, 1, 1, 3)
        top_layout.addLayout(quick_layout)

        naming_group = QGroupBox(self._txt("Именование", "Naming"))
        naming_layout = QGridLayout(naming_group)
        self.naming_preset_combo = QComboBox()
        self.naming_preset_combo.addItem(
            self._txt("Шаблон не выбран", "No template selected"),
            "",
        )
        for preset in get_naming_presets(self.language):
            self.naming_preset_combo.addItem(preset.label, preset.id)
        self.naming_fields_button = QPushButton(self._txt("Доступные поля", "Available fields"))
        self.naming_directory_edit = QLineEdit(self.app_settings.naming_directory_template)
        self.naming_directory_edit.setPlaceholderText(
            self._txt(
                "Пусто = как у gallery-dl, например: {category}/{user[id]}",
                "Empty = use gallery-dl default, for example: {category}/{user[id]}",
            )
        )
        self.naming_filename_quick_edit = QLineEdit(self.app_settings.naming_filename_template)
        self.naming_filename_quick_edit.setPlaceholderText(
            self._txt(
                "Пусто = как у gallery-dl, например: {title}.{extension}",
                "Empty = use gallery-dl default, for example: {title}.{extension}",
            )
        )
        self.use_original_filenames_check = QCheckBox(
            self._txt("Использовать оригинальные имена", "Use original names")
        )
        self.use_original_filenames_check.setChecked(self.app_settings.naming_use_original_filenames)
        self.path_compatibility_combo = QComboBox()
        self.path_compatibility_combo.addItem(self._txt("Авто", "Auto"), "auto")
        self.path_compatibility_combo.addItem("Windows-safe", "windows")
        self.path_compatibility_combo.addItem("ASCII-safe", "ascii")
        self._set_combo_value(
            self.path_compatibility_combo,
            self.app_settings.naming_path_compatibility_mode or "auto",
        )

        self.naming_preview_label = QLabel("-")
        self.naming_preview_label.setWordWrap(True)
        self.naming_preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        naming_layout.addWidget(QLabel(self._txt("Шаблон:", "Template:")), 0, 0)
        naming_layout.addWidget(self.naming_preset_combo, 0, 1)
        naming_layout.addWidget(self.naming_fields_button, 0, 2)
        naming_layout.addWidget(QLabel(self._txt("Структура папок:", "Folder structure:")), 1, 0)
        naming_layout.addWidget(self.naming_directory_edit, 1, 1, 1, 3)
        naming_layout.addWidget(QLabel(self._txt("Имя файла:", "File name:")), 2, 0)
        naming_layout.addWidget(self.naming_filename_quick_edit, 2, 1, 1, 3)
        naming_layout.addWidget(self.use_original_filenames_check, 3, 0, 1, 2)
        naming_layout.addWidget(QLabel(self._txt("Совместимость имен:", "Name compatibility:")), 3, 2)
        naming_layout.addWidget(self.path_compatibility_combo, 3, 3)
        naming_layout.addWidget(QLabel(self._txt("Предпросмотр пути:", "Path preview:")), 4, 0)
        naming_layout.addWidget(self.naming_preview_label, 4, 1, 1, 3)
        top_layout.addWidget(naming_group)

        primary_actions_layout = QHBoxLayout()
        self.check_button = QPushButton(self._txt("Проверить", "Check"))
        self.download_button = QPushButton(self._txt("Скачать", "Download"))
        self.cancel_button = QPushButton(self._txt("Отменить", "Cancel"))
        self.cancel_button.setEnabled(False)
        self.supported_sites_button = QPushButton(self._txt("Поддерживаемые сайты", "Supported sites"))
        self.history_button = QPushButton(self._txt("История", "History"))
        self.advanced_button = QPushButton(self._txt("Еще настройки", "More settings"))
        self.language_label = QLabel(self._txt("Язык:", "Language:"))
        self.language_combo = QComboBox()
        self.language_combo.addItem("Русский", "ru")
        self.language_combo.addItem("English", "en")
        self._set_combo_value(self.language_combo, self.language)

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
        primary_actions_layout.addWidget(self.supported_sites_button)
        primary_actions_layout.addWidget(self.history_button)
        primary_actions_layout.addWidget(self.advanced_button)
        primary_actions_layout.addSpacing(12)
        primary_actions_layout.addWidget(self.language_label)
        primary_actions_layout.addWidget(self.language_combo)

        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(8)
        actions_layout.addLayout(primary_actions_layout)
        top_layout.addLayout(actions_layout)

        self.current_task_label = QLabel(self._txt("Нет активной задачи", "No active task"))
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
                self._txt("Сайт", "Site"),
                self._txt("Режим", "Mode"),
                self._txt("Статус", "Status"),
                self._txt("Папка", "Folder"),
                self._txt("Последнее сообщение", "Last message"),
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
        log_title = QLabel(self._txt("Журнал", "Log"))
        log_title.setStyleSheet("font-weight: 600;")
        self.log_toggle_button = QToolButton()
        self.log_toggle_button.setText(self._txt("Скрыть", "Hide"))
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

    def _build_hidden_history_storage(self) -> None:
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(
            [
                "URL",
                self._txt("Сайт", "Site"),
                self._txt("Режим", "Mode"),
                self._txt("Результат", "Result"),
                self._txt("Комментарий", "Comment"),
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

    def _configure_history_table(self, table: QTableWidget) -> None:
        table.setHorizontalHeaderLabels(
            [
                "URL",
                self._txt("Сайт", "Site"),
                self._txt("Режим", "Mode"),
                self._txt("Результат", "Result"),
                self._txt("Комментарий", "Comment"),
            ]
        )
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(False)
        for index in range(5):
            table.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(0, 320)
        table.setColumnWidth(1, 140)
        table.setColumnWidth(2, 120)
        table.setColumnWidth(3, 130)
        table.setColumnWidth(4, 420)

    def _build_advanced_dock(self) -> None:
        self.advanced_dock = QDockWidget(self._txt("Дополнительные настройки", "Additional settings"), self)
        self.advanced_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.advanced_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.advanced_dock)
        self.advanced_dock.hide()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        auth_content = QWidget()
        auth_form = QFormLayout(auth_content)
        self.username_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.cookies_file_edit = QLineEdit()
        self.cookies_file_button = QPushButton(self._txt("Обзор", "Browse"))
        cookies_widget = QWidget()
        cookies_layout = QHBoxLayout(cookies_widget)
        cookies_layout.setContentsMargins(0, 0, 0, 0)
        cookies_layout.addWidget(self.cookies_file_edit, 1)
        cookies_layout.addWidget(self.cookies_file_button)
        self.browser_cookies_edit = QLineEdit(self.app_settings.last_cookies_browser)
        self.browser_cookies_edit.setPlaceholderText("firefox, chrome, edge...")
        auth_form.addRow(self._txt("Логин:", "Username:"), self.username_edit)
        auth_form.addRow(self._txt("Пароль:", "Password:"), self.password_edit)
        auth_form.addRow(self._txt("Cookies файл:", "Cookies file:"), cookies_widget)
        auth_form.addRow(self._txt("Cookies из браузера:", "Cookies from browser:"), self.browser_cookies_edit)

        filters_content = QWidget()
        filters_form = QFormLayout(filters_content)
        self.date_before_edit = QLineEdit()
        self.date_before_edit.setPlaceholderText("2026-12-31")
        self.date_after_edit = QLineEdit()
        self.date_after_edit.setPlaceholderText("2026-01-01")
        self.filesize_min_edit = QLineEdit()
        self.filesize_min_edit.setPlaceholderText("500k")
        self.filesize_max_edit = QLineEdit()
        self.filesize_max_edit.setPlaceholderText("2.5M")
        filters_form.addRow(self._txt("Дата до:", "Date before:"), self.date_before_edit)
        filters_form.addRow(self._txt("Дата после:", "Date after:"), self.date_after_edit)
        filters_form.addRow(self._txt("Размер от:", "Size from:"), self.filesize_min_edit)
        filters_form.addRow(self._txt("Размер до:", "Size to:"), self.filesize_max_edit)

        post_content = QWidget()
        post_form = QFormLayout(post_content)
        self.write_metadata_check = QCheckBox(self._txt("Сохранять metadata (.json)", "Save metadata (.json)"))
        self.write_info_json_check = QCheckBox(self._txt("Сохранять info.json", "Save info.json"))
        self.write_tags_check = QCheckBox(self._txt("Сохранять теги", "Save tags"))
        self.archive_combo = QComboBox()
        self.archive_combo.addItems([self._txt("Нет", "None"), "ZIP", "CBZ"])
        self.ugoira_combo = QComboBox()
        self.ugoira_combo.addItems([self._txt("Нет", "None"), "WEBM", "MP4", "GIF", "Copy", "ZIP"])
        post_form.addRow(self.write_metadata_check)
        post_form.addRow(self.write_info_json_check)
        post_form.addRow(self.write_tags_check)
        post_form.addRow(self._txt("Упаковать:", "Pack into:"), self.archive_combo)
        post_form.addRow("Ugoira:", self.ugoira_combo)

        app_network_content = QWidget()
        app_network_form = QFormLayout(app_network_content)
        self.gallery_dl_path_edit = QLineEdit(self.app_settings.gallery_dl_path)
        self.gallery_dl_path_button = QPushButton(self._txt("Обзор", "Browse"))
        path_widget = QWidget()
        path_layout = QHBoxLayout(path_widget)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.addWidget(self.gallery_dl_path_edit, 1)
        path_layout.addWidget(self.gallery_dl_path_button)

        self.default_folder_edit = QLineEdit(self.app_settings.default_download_dir)
        self.default_folder_button = QPushButton(self._txt("Обзор", "Browse"))
        folder_widget = QWidget()
        folder_layout = QHBoxLayout(folder_widget)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(self.default_folder_edit, 1)
        folder_layout.addWidget(self.default_folder_button)

        self.proxy_edit = QLineEdit()
        self.retries_edit = QLineEdit()
        self.timeout_edit = QLineEdit()
        app_network_form.addRow(self._txt("Путь к gallery-dl:", "Path to gallery-dl:"), path_widget)
        app_network_form.addRow(self._txt("Папка по умолчанию:", "Default folder:"), folder_widget)
        app_network_form.addRow(self._txt("Прокси:", "Proxy:"), self.proxy_edit)
        app_network_form.addRow(self._txt("Повторы:", "Retries:"), self.retries_edit)
        app_network_form.addRow(self._txt("Таймаут:", "Timeout:"), self.timeout_edit)

        expert_content = QWidget()
        expert_form = QFormLayout(expert_content)
        self.filename_template_edit = QLineEdit(self.app_settings.naming_filename_template)
        self.filename_template_edit.setPlaceholderText("{filename}.{extension}")
        self.base_directory_edit = QLineEdit(self.app_settings.naming_base_directory)
        self.base_directory_edit.setPlaceholderText(
            self._txt(
                "Пусто = брать основную папку загрузки",
                "Empty = use the main download folder",
            )
        )
        self.path_restrict_edit = QLineEdit(self.app_settings.naming_path_restrict)
        self.path_restrict_edit.setPlaceholderText("auto / windows / ascii")
        self.path_replace_edit = QLineEdit(self.app_settings.naming_path_replace)
        self.path_replace_edit.setPlaceholderText("_")
        self.path_remove_edit = QLineEdit(self.app_settings.naming_path_remove)
        self.path_remove_edit.setPlaceholderText("\\x00-\\x1f\\x7f")
        self.path_strip_edit = QLineEdit(self.app_settings.naming_path_strip)
        self.path_strip_edit.setPlaceholderText(". ")
        expert_form.addRow(self._txt("Сырой шаблон имени:", "Raw filename template:"), self.filename_template_edit)
        expert_form.addRow("Base directory:", self.base_directory_edit)
        expert_form.addRow("Path restrict:", self.path_restrict_edit)
        expert_form.addRow("Path replace:", self.path_replace_edit)
        expert_form.addRow("Path remove:", self.path_remove_edit)
        expert_form.addRow("Path strip:", self.path_strip_edit)

        layout.addWidget(self._create_collapsible_section(self._txt("Доступ", "Access"), auth_content, expanded=False))
        layout.addWidget(self._create_collapsible_section(self._txt("Фильтры", "Filters"), filters_content, expanded=True))
        layout.addWidget(self._create_collapsible_section(self._txt("После загрузки", "After download"), post_content, expanded=True))
        layout.addWidget(self._create_collapsible_section(self._txt("Приложение и сеть", "App and network"), app_network_content, expanded=True))
        layout.addWidget(self._create_collapsible_section(self._txt("Для опытных", "Expert"), expert_content, expanded=False))
        self.save_settings_button = QPushButton(self._txt("Сохранить настройки", "Save settings"))
        layout.addWidget(self.save_settings_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        scroll.setWidget(container)
        self.advanced_dock.setWidget(scroll)

    def _create_collapsible_section(self, title: str, content: QWidget, *, expanded: bool) -> QWidget:
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(4)

        toggle = QToolButton()
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setChecked(expanded)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        toggle.setStyleSheet("font-weight: 600; text-align: left;")

        content.setVisible(expanded)

        def on_toggled(checked: bool) -> None:
            content.setVisible(checked)
            toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

        toggle.toggled.connect(on_toggled)
        wrapper_layout.addWidget(toggle)
        wrapper_layout.addWidget(content)
        return wrapper

    def _build_supported_sites_dock(self) -> None:
        self.supported_sites_dock = QDockWidget(self._txt("Поддерживаемые сайты", "Supported sites"), self)
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
            '<a href="https://github.com/mikf/gallery-dl/blob/master/docs/supportedsites.md">' +
            self._txt(
                "Источник: supportedsites.md из gallery-dl на GitHub",
                "Source: gallery-dl supportedsites.md on GitHub",
            )
            + "</a>"
        )
        source_label.setOpenExternalLinks(True)
        source_label.setWordWrap(True)
        layout.addWidget(source_label)

        controls_layout = QHBoxLayout()
        self.supported_sites_search_edit = QLineEdit()
        self.supported_sites_search_edit.setPlaceholderText(
            self._txt(
                "Поиск по сайту, URL или возможностям",
                "Search by site, URL, or capabilities",
            )
        )
        self.supported_sites_refresh_button = QPushButton(self._txt("Обновить", "Refresh"))
        controls_layout.addWidget(self.supported_sites_search_edit, 1)
        controls_layout.addWidget(self.supported_sites_refresh_button)
        layout.addLayout(controls_layout)

        self.supported_sites_tree = QTreeWidget()
        self.supported_sites_tree.setHeaderHidden(True)
        self.supported_sites_tree.setAlternatingRowColors(True)
        layout.addWidget(self.supported_sites_tree, 1)

        details_group = QGroupBox(self._txt("Подробности", "Details"))
        details_form = QFormLayout(details_group)
        self.site_name_value = QLabel(self._txt("Выбери сайт из списка.", "Select a site from the list."))
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
        details_form.addRow(self._txt("Сайт:", "Site:"), self.site_name_value)
        details_form.addRow("URL:", self.site_url_value)
        details_form.addRow(self._txt("Возможности:", "Capabilities:"), self.site_capabilities_value)
        details_form.addRow(self._txt("Авторизация:", "Authentication:"), self.site_auth_value)
        details_form.addRow(self._txt("Секция:", "Section:"), self.site_section_value)
        layout.addWidget(details_group)

        footer_layout = QVBoxLayout()
        self.supported_sites_updated_label = QLabel(
            self._txt("Последнее обновление: -", "Last update: -")
        )
        self.supported_sites_status_label = QLabel(
            self._txt("Список сайтов еще не загружен.", "The site list has not been loaded yet.")
        )
        self.supported_sites_status_label.setWordWrap(True)
        footer_layout.addWidget(self.supported_sites_updated_label)
        footer_layout.addWidget(self.supported_sites_status_label)
        layout.addLayout(footer_layout)

        self.supported_sites_dock.setWidget(container)

    def _build_statusbar(self) -> None:
        statusbar = QStatusBar()
        self.setStatusBar(statusbar)
        self.status_message = QLabel(self._txt("Готово", "Ready"))
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
        self.history_button.clicked.connect(self._open_history_window)
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
        self.log_toggle_button.setText(self._txt("Показать", "Show") if visible else self._txt("Скрыть", "Hide"))

    def _open_history_window(self) -> None:
        if self.history_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle(self._txt("История", "History"))
            dialog.resize(1040, 560)
            layout = QVBoxLayout(dialog)

            table = QTableWidget(0, 5)
            self._configure_history_table(table)
            layout.addWidget(table, 1)

            buttons_layout = QHBoxLayout()
            clear_button = QPushButton(self._txt("Очистить", "Clear"))
            close_button = QPushButton(self._txt("Закрыть", "Close"))
            buttons_layout.addWidget(clear_button)
            buttons_layout.addStretch(1)
            buttons_layout.addWidget(close_button)
            layout.addLayout(buttons_layout)

            clear_button.clicked.connect(self._clear_history_records)
            close_button.clicked.connect(dialog.close)

            self.history_dialog = dialog
            self.history_dialog_table = table
            self._rebuild_history_dialog_table()

        self._rebuild_history_dialog_table()
        self.history_dialog.show()
        self.history_dialog.raise_()
        self.history_dialog.activateWindow()

    def _rebuild_history_dialog_table(self) -> None:
        if self.history_dialog_table is None:
            return
        self.history_dialog_table.setRowCount(0)
        for row in range(self.history_table.rowCount()):
            self.history_dialog_table.insertRow(row)
            for column in range(self.history_table.columnCount()):
                source_item = self.history_table.item(row, column)
                value = source_item.text() if source_item is not None else ""
                self._set_table_item_text(self.history_dialog_table, row, column, value)

    def _clear_history_records(self) -> None:
        self.history_table.setRowCount(0)
        if self.history_dialog_table is not None:
            self.history_dialog_table.setRowCount(0)
        self.history_rows.clear()
        self.status_message.setText(self._txt("История очищена", "History cleared"))

    def _sync_filename_state(self) -> None:
        use_original = self.use_original_filenames_check.isChecked()
        self.naming_filename_quick_edit.setEnabled(not use_original)
        self.filename_template_edit.setEnabled(not use_original)
        if use_original:
            self.naming_filename_quick_edit.setPlaceholderText(
                self._txt(
                    "Выключено: используются оригинальные имена",
                    "Disabled: original names are used",
                )
            )
        else:
            self.naming_filename_quick_edit.setPlaceholderText(
                self._txt(
                    "Пусто = как у gallery-dl, например: {title}.{extension}",
                    "Empty = use gallery-dl default, for example: {title}.{extension}",
                )
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
        preset = get_preset_by_id(preset_id, self.language)
        if preset is None:
            return
        self.naming_directory_edit.setText(preset.directory_template)
        self.naming_filename_quick_edit.setText(preset.filename_template)
        self.use_original_filenames_check.setChecked(preset.use_original_filenames)
        self.status_message.setText(preset.description)
        self._update_naming_preview()

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

    def _supported_site_section_label(self, section: str) -> str:
        normalized = (section or DEFAULT_SECTION).strip()
        if normalized == DEFAULT_SECTION:
            return self._txt("Основные сайты", "Main sites")
        return normalized

    def _build_supported_site_tooltip(self, entry: SupportedSiteEntry) -> str:
        lines = [
            f"{self._txt('Сайт', 'Site')}: {entry.name or '-'}",
            f"URL: {entry.url or '-'}",
            f"{self._txt('Возможности', 'Capabilities')}: {entry.capabilities or '-'}",
            f"{self._txt('Авторизация', 'Authentication')}: {entry.auth or '-'}",
        ]
        section_label = self._supported_site_section_label(entry.section)
        if section_label != self._txt("Основные сайты", "Main sites"):
            lines.append(f"{self._txt('Секция', 'Section')}: {section_label}")
        return "\n".join(lines)

    def _show_supported_site_details(self, entry: SupportedSiteEntry) -> None:
        self.site_name_value.setText(entry.name or "-")
        if entry.url:
            self.site_url_value.setText(f'<a href="{entry.url}">{entry.url}</a>')
        else:
            self.site_url_value.setText("-")
        self.site_capabilities_value.setText(entry.capabilities or "-")
        self.site_auth_value.setText(entry.auth or "-")
        self.site_section_value.setText(self._supported_site_section_label(entry.section))

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

    def _save_settings(self) -> None:
        self.app_settings = AppSettings(
            language=self.language,
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
        self.status_message.setText(self._txt("Настройки сохранены", "Settings saved"))

    def _queue_tasks(self, mode: TaskMode) -> None:
        urls = [line.strip() for line in self.urls_edit.toPlainText().splitlines() if line.strip()]
        if not urls:
            QMessageBox.information(
                self,
                self._txt("Нет ссылок", "No URLs"),
                self._txt("Добавь хотя бы одну ссылку.", "Add at least one URL."),
            )
            return

        destination = self._destination_text() or self.app_settings.default_download_dir
        if not destination:
            QMessageBox.warning(
                self,
                self._txt("Нет папки", "No folder selected"),
                self._txt("Укажи папку сохранения.", "Choose a destination folder."),
            )
            return

        if not self._has_selected_file_types():
            QMessageBox.warning(
                self,
                self._txt("Не выбраны типы файлов", "No file types selected"),
                self._txt(
                    "Отметь хотя бы один тип файлов или укажи свои расширения.",
                    "Select at least one file type or enter custom extensions.",
                ),
            )
            return

        self._register_recent_destination(destination)
        options = self._collect_task_options(destination)
        tasks = [DownloadTask(url=url, mode=mode, options=options) for url in urls]
        self.runner.enqueue(tasks)
        self.settings_service.save(self.app_settings)
        self.status_message.setText(self._txt(f"Добавлено задач: {len(tasks)}", f"Queued tasks: {len(tasks)}"))

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
            date_before=self.date_before_edit.text().strip(),
            date_after=self.date_after_edit.text().strip(),
            filesize_min=self.filesize_min_edit.text().strip(),
            filesize_max=self.filesize_max_edit.text().strip(),
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
            task.mode.label(self.language),
            task.status.label(self.language),
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
            task.mode.label(self.language),
            task.status.label(self.language),
            task.last_message,
        ]
        self._write_history_row(self.history_table, row, values)
        if self.history_dialog_table is not None:
            while self.history_dialog_table.rowCount() <= row:
                self.history_dialog_table.insertRow(self.history_dialog_table.rowCount())
            self._write_history_row(self.history_dialog_table, row, values)
        self._finalize_task_log(task)

    def _write_history_row(self, table: QTableWidget, row: int, values: list[str]) -> None:
        for column, value in enumerate(values):
            self._set_table_item_text(table, row, column, value)

    def _set_table_item_text(self, table: QTableWidget, row: int, column: int, value: str) -> None:
        item = table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            table.setItem(row, column, item)
        item.setText(value)

    def _append_log(self, task_id: str, message: str, stream: str) -> None:
        task = self.tasks.get(task_id)
        prefix = ""
        if task is not None:
            prefix = f"[{task.mode.label(self.language)} | {task.site}] "
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

    def _change_language(self) -> None:
        selected = str(self.language_combo.currentData() or "ru")
        if selected == self.language:
            return
        self.language = selected
        self.app_settings.language = selected
        self.runner.set_language(selected)
        self.settings_service.save(self.app_settings)
        if getattr(sys, "frozen", False):
            program = sys.executable
            args = sys.argv[1:]
        else:
            program = sys.executable
            args = sys.argv

        if QProcess.startDetached(program, args):
            self.close()
            return

        QMessageBox.information(
            self,
            self._txt("Язык сохранен", "Language saved"),
            self._txt(
                "Новый язык сохранен. Перезапусти приложение, чтобы весь интерфейс обновился.",
                "The new language has been saved. Restart the app to update the whole interface.",
            ),
        )

    def _show_available_fields(self) -> None:
        urls = [line.strip() for line in self.urls_edit.toPlainText().splitlines() if line.strip()]
        if not urls:
            self._show_keyword_browser_dialog(
                entries=build_common_keyword_entries(language=self.language),
                note=self._txt(
                    "Ссылка еще не указана, поэтому показан общий набор часто используемых полей. Когда ты добавишь URL, здесь появятся точные поля для конкретного сайта.",
                    "No URL has been entered yet, so the dialog shows a common set of frequently used fields. After you add a URL, the exact fields for that site will appear here.",
                ),
                raw_output="",
            )
            return

        success, output = self.runner.inspect_keywords(urls[0])
        if success:
            entries = parse_gallery_dl_keywords(output, self.language)
            if entries:
                self._show_keyword_browser_dialog(
                    entries=entries,
                    note=self._txt(
                        "Показаны поля, которые gallery-dl вернул для первой ссылки из списка. Их можно вставлять в шаблон папки или имени файла.",
                        "These are the fields returned by gallery-dl for the first URL in the list. You can insert them into the folder or filename template.",
                    ),
                    raw_output=output,
                )
                return

        note = self._txt(
            "Не удалось получить точный список полей от gallery-dl. Показан общий набор, который подходит для большинства сайтов.",
            "Could not get the exact field list from gallery-dl. A common set of fields is shown instead.",
        )
        if output:
            note += "\n\n" + self._txt("Техническое сообщение:\n", "Technical message:\n") + output
        self._show_keyword_browser_dialog(
            entries=build_common_keyword_entries(urls[0], self.language),
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
        dialog.setWindowTitle(self._txt("Доступные поля", "Available fields"))
        dialog.resize(1080, 680)

        layout = QVBoxLayout(dialog)

        note_label = QLabel(note)
        note_label.setWordWrap(True)
        note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(note_label)

        helper_label = QLabel(
            self._txt(
                "Выбери поле в списке ниже. Вставка идет в текущее место курсора в шаблоне.",
                "Select a field below. It will be inserted at the current cursor position in the template.",
            )
        )
        helper_label.setStyleSheet("color: #555;")
        helper_label.setWordWrap(True)
        layout.addWidget(helper_label)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel(self._txt("Поиск:", "Search:")))
        search_edit = QLineEdit()
        search_edit.setPlaceholderText(self._txt("Например: title, date, filename, user", "Example: title, date, filename, user"))
        search_layout.addWidget(search_edit, 1)
        layout.addLayout(search_layout)

        tree = QTreeWidget()
        tree.setColumnCount(4)
        tree.setHeaderLabels(
            (
                self._txt("Поле", "Field"),
                self._txt("Пример", "Example"),
                self._txt("Что означает", "Meaning"),
                self._txt("Где использовать", "Usage"),
            )
        )
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

        selected_group = QGroupBox(self._txt("Выбранное поле", "Selected field"))
        selected_layout = QGridLayout(selected_group)
        token_value = QLabel(self._txt("Выбери поле в списке.", "Select a field from the list."))
        token_value.setWordWrap(True)
        token_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        description_value = QLabel("-")
        description_value.setWordWrap(True)
        description_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        selected_layout.addWidget(QLabel(self._txt("Шаблон:", "Template:")), 0, 0)
        selected_layout.addWidget(token_value, 0, 1)
        selected_layout.addWidget(QLabel(self._txt("Пояснение:", "Description:")), 1, 0)
        selected_layout.addWidget(description_value, 1, 1)
        layout.addWidget(selected_group)

        buttons_layout = QHBoxLayout()
        insert_directory_button = QPushButton(self._txt("Вставить в папку", "Insert into folder"))
        insert_filename_button = QPushButton(self._txt("Вставить в имя файла", "Insert into file name"))
        raw_button = QPushButton(self._txt("Показать сырой вывод", "Show raw output"))
        close_button = QPushButton(self._txt("Закрыть", "Close"))
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
                token_value.setText(self._txt("Выбери поле в списке.", "Select a field from the list."))
                description_value.setText("-")
                return
            token_value.setText(entry.template)
            description_value.setText(
                f"{entry.description}\n\n{self._txt('Пример', 'Example')}: {entry.sample}\n{self._txt('Где использовать', 'Usage')}: {entry.usage}"
            )

        def insert_into_directory() -> None:
            entry = current_entry()
            if entry is None:
                return
            self._insert_text_into_line_edit(self.naming_directory_edit, entry.template)
            self.naming_directory_edit.setFocus()
            self.status_message.setText(self._txt(f"В шаблон папок вставлено {entry.template}", f"Inserted {entry.template} into the folder template"))

        def insert_into_filename() -> None:
            entry = current_entry()
            if entry is None:
                return
            if self.use_original_filenames_check.isChecked():
                self.use_original_filenames_check.setChecked(False)
            self._insert_text_into_line_edit(self.naming_filename_quick_edit, entry.template)
            self.naming_filename_quick_edit.setFocus()
            self.status_message.setText(self._txt(f"В шаблон имени файла вставлено {entry.template}", f"Inserted {entry.template} into the filename template"))

        search_edit.textChanged.connect(lambda text: self._filter_keyword_tree(tree, text))
        tree.currentItemChanged.connect(lambda _current, _previous: sync_selection())
        insert_directory_button.clicked.connect(insert_into_directory)
        insert_filename_button.clicked.connect(insert_into_filename)
        raw_button.clicked.connect(lambda: self._show_readonly_text_dialog(self._txt("Сырой вывод gallery-dl", "Raw gallery-dl output"), raw_output))
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
        group_order = get_group_order(self.language)
        grouped: dict[str, list[NamingKeywordEntry]] = {group: [] for group in group_order}
        for entry in entries:
            grouped.setdefault(entry.group, []).append(entry)

        for group_name in group_order:
            group_entries = grouped.get(group_name, [])
            if not group_entries:
                continue
            group_item = QTreeWidgetItem([group_name])
            group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            group_item.setFirstColumnSpanned(True)
            tree.addTopLevelItem(group_item)

            for entry in group_entries:
                item = QTreeWidgetItem([entry.name, entry.sample, entry.description, entry.usage])
                item.setData(0, Qt.ItemDataRole.UserRole, entry)
                item.setToolTip(0, entry.template)
                item.setToolTip(1, entry.sample)
                item.setToolTip(2, entry.description)
                item.setToolTip(3, entry.usage)
                group_item.addChild(item)

            group_item.setExpanded(group_name in set(group_order[:2]))

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
            self.naming_preview_label.setText(self._txt(f"Ошибка preview: {error}", f"Preview error: {error}"))
            self.naming_preview_label.setStyleSheet("color: #b00020;")
            return

        self.naming_preview_label.setText(preview or "-")
        self.naming_preview_label.setStyleSheet("")

    def _initialize_supported_sites(self) -> None:
        cached = self.supported_sites_service.load_cached()
        if cached is not None:
            self._apply_supported_sites_payload(cached)
            self.supported_sites_status_label.setText(self._txt("Показан кэшированный список сайтов.", "Showing the cached site list."))
            if self.supported_sites_service.needs_refresh(cached):
                QTimer.singleShot(0, lambda: self._start_supported_sites_refresh(manual=False))
            return

        self.supported_sites_status_label.setText(self._txt("Список сайтов загружается с GitHub...", "Loading the site list from GitHub..."))
        QTimer.singleShot(0, lambda: self._start_supported_sites_refresh(manual=False))

    def _start_supported_sites_refresh(self, *, manual: bool) -> None:
        if self._supported_sites_refresh_active:
            if manual:
                self.supported_sites_status_label.setText(self._txt("Обновление уже выполняется.", "A refresh is already in progress."))
            return

        self._supported_sites_refresh_active = True
        self.supported_sites_refresh_button.setEnabled(False)
        self.supported_sites_status_label.setText(self._txt("Обновляю список поддерживаемых сайтов...", "Refreshing the supported sites list..."))

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
            self._on_supported_sites_failed(self._txt("Некорректный ответ от GitHub.", "Invalid response from GitHub."), manual)
            return

        self._apply_supported_sites_payload(payload)
        self.supported_sites_status_label.setText(self._txt("Список сайтов актуален.", "The site list is up to date."))
        if manual:
            self.status_message.setText(self._txt("Список поддерживаемых сайтов обновлен", "The supported sites list has been updated"))

    def _on_supported_sites_failed(self, message: str, manual: bool) -> None:
        self._supported_sites_refresh_active = False
        self.supported_sites_refresh_button.setEnabled(True)

        if self.supported_sites_payload is None:
            self.supported_sites_status_label.setText(
                self._txt(
                    "Не удалось загрузить список сайтов. Проверь сеть или попробуй позже.",
                    "Could not load the site list. Check your network connection and try again later.",
                )
            )
        else:
            self.supported_sites_status_label.setText(
                self._txt(
                    "Не удалось обновить список. Показан сохраненный кэш.",
                    "Could not refresh the list. Showing the saved cache.",
                )
            )

        if manual:
            self.status_message.setText(message)

    def _clear_supported_site_details(self) -> None:
        self.site_name_value.setText(self._txt("Выбери сайт из списка.", "Select a site from the list."))
        self.site_url_value.setText("-")
        self.site_capabilities_value.setText("-")
        self.site_auth_value.setText("-")
        self.site_section_value.setText("-")

    def _choose_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self._txt("Выбери папку загрузки", "Choose the download folder"),
            self._destination_text() or self.default_folder_edit.text(),
        )
        if folder:
            self._register_recent_destination(folder)
            self.settings_service.save(self.app_settings)

    def _choose_default_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self._txt("Папка по умолчанию", "Default folder"),
            self.default_folder_edit.text() or str(Path.home() / "Downloads"),
        )
        if folder:
            self.default_folder_edit.setText(folder)

    def _choose_gallery_dl_path(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self._txt("Укажи путь к gallery-dl", "Choose the path to gallery-dl"),
            str(Path.home()),
            self._txt("Исполняемые файлы (*.exe);;Все файлы (*.*)", "Executable files (*.exe);;All files (*.*)"),
        )
        if file_path:
            self.gallery_dl_path_edit.setText(file_path)

    def _choose_cookies_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self._txt("Выбери cookies файл", "Choose a cookies file"),
            str(Path.home()),
            "Text files (*.txt *.json);;All files (*.*)",
        )
        if file_path:
            self.cookies_file_edit.setText(file_path)

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
                    f"{self._txt('Время запуска', 'Started at')}: {task.created_at.strftime('%d.%m.%Y %H:%M:%S')}",
                    f"{self._txt('Режим', 'Mode')}: {task.mode.label(self.language)}",
                    f"{self._txt('Сайт', 'Site')}: {task.site}",
                    f"URL: {task.url}",
                    f"{self._txt('Папка', 'Folder')}: {task.target_folder}",
                    "",
                ]
                log_path.write_text("\n".join(header), encoding="utf-8")
                self._initialized_log_files.add(task.id)
            timestamp = datetime.now().strftime("%H:%M:%S")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{timestamp}] {line}\n")
        except Exception as exc:
            self._failed_log_files.add(task.id)
            self.log_output.appendPlainText(
                self._txt(
                    f"[Система] Не удалось записать лог задачи: {exc}",
                    f"[System] Could not write the task log: {exc}",
                )
            )

    def _finalize_task_log(self, task: DownloadTask) -> None:
        if (
            not task.options.save_log
            or not task.log_file_path
            or task.id in self._finalized_log_files
            or task.id in self._failed_log_files
        ):
            return
        summary = f"{self._txt('Итог', 'Summary')}: {task.status.label(self.language)}"
        if task.last_message:
            summary += f". {task.last_message}"
        self._write_task_log_line(task, summary)
        self._finalized_log_files.add(task.id)

    def _format_size(self, value: int) -> str:
        size = float(value)
        units = ("Б", "КБ", "МБ", "ГБ") if self.language == "ru" else ("B", "KB", "MB", "GB")
        for unit in units:
            if size < 1024 or unit == units[-1]:
                if unit in {"Б", "B"}:
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{int(value)} {units[0]}"

    def _format_speed(self, value: float) -> str:
        if value <= 0:
            return self._txt("0 Б/с", "0 B/s")
        size = value
        units = ("Б/с", "КБ/с", "МБ/с", "ГБ/с") if self.language == "ru" else ("B/s", "KB/s", "MB/s", "GB/s")
        for unit in units:
            if size < 1024 or unit == units[-1]:
                if unit in {"Б/с", "B/s"}:
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{int(value)} {units[0]}"

    def _render_current_task_banner(self, task: DownloadTask | None) -> None:
        if task is None:
            self.current_task_label.setText(self._txt("Нет активной задачи", "No active task"))
            self.current_task_progress.setRange(0, 1)
            self.current_task_progress.setValue(0)
            return

        details = self._current_part_status or task.last_message or task.progress_text
        self.current_task_label.setText(f"{task.mode.label(self.language)}: {task.title}\n{details}")
        self.current_task_progress.setRange(0, 0)

    def _apply_supported_sites_payload(self, payload: SupportedSitesPayload) -> None:
        self.supported_sites_payload = payload
        self.supported_sites_tree.clear()

        section_items: dict[str, QTreeWidgetItem] = {}
        first_site_item: QTreeWidgetItem | None = None

        for site in payload.sites:
            section_name = site.section or DEFAULT_SECTION
            if section_name not in section_items:
                section_label = self._supported_site_section_label(section_name)
                section_item = QTreeWidgetItem([section_label])
                section_item.setFlags(section_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                section_item.setToolTip(0, section_label)
                self.supported_sites_tree.addTopLevelItem(section_item)
                section_items[section_name] = section_item

            item = QTreeWidgetItem([site.name])
            item.setToolTip(0, self._build_supported_site_tooltip(site))
            item.setData(0, Qt.ItemDataRole.UserRole, site)
            section_items[section_name].addChild(item)
            if first_site_item is None:
                first_site_item = item

        if section_items:
            next(iter(section_items.values())).setExpanded(True)

        self.supported_sites_updated_label.setText(
            self._txt("Последнее обновление: ", "Last update: ") + f"{self._format_timestamp(payload.fetched_at)}"
        )
        self._filter_supported_sites(self.supported_sites_search_edit.text())

        if first_site_item is not None and self.supported_sites_tree.currentItem() is None:
            self.supported_sites_tree.setCurrentItem(first_site_item)

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

        message = f"{self._txt('Скачивается', 'Downloading')}: {relative_path} ({self._format_size(stat.st_size)})"
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
