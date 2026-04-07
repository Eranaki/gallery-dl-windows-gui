from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
from app.models.task import DownloadTask, MediaScope, TaskMode, TaskOptions, TaskStatus
from app.services.gallery_dl_runner import GalleryDlRunner
from app.services.settings_service import AppSettings, SettingsService
from app.services.supported_sites_service import DEFAULT_SECTION, SupportedSitesService


SUPPORTED_SITES_DOCK_WIDTH = 440


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
        self.supported_sites_payload: SupportedSitesPayload | None = None
        self._supported_sites_refresh_active = False
        self._supported_sites_thread: threading.Thread | None = None

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

        self.urls_edit = QTextEdit()
        self.urls_edit.setPlaceholderText(
            "\u0412\u0441\u0442\u0430\u0432\u044c \u043e\u0434\u043d\u0443 \u0438\u043b\u0438 \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e "
            "\u0441\u0441\u044b\u043b\u043e\u043a. \u041a\u0430\u0436\u0434\u0430\u044f \u0441\u0441\u044b\u043b\u043a\u0430 \u0441 \u043d\u043e\u0432\u043e\u0439 "
            "\u0441\u0442\u0440\u043e\u043a\u0438."
        )
        self.urls_edit.setMinimumHeight(120)
        top_layout.addWidget(self.urls_edit)

        path_layout = QHBoxLayout()
        self.destination_edit = QLineEdit(self.app_settings.default_download_dir)
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
        self.media_scope_combo = QComboBox()
        self.media_scope_combo.addItems([scope.label for scope in MediaScope])
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("\u041d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: 1:20 \u0438\u043b\u0438 5-30")

        quick_layout.addWidget(self.only_new_check, 0, 0)
        quick_layout.addWidget(self.organize_by_site_check, 0, 1)
        quick_layout.addWidget(QLabel("\u0422\u0438\u043f \u0444\u0430\u0439\u043b\u043e\u0432:"), 1, 0)
        quick_layout.addWidget(self.media_scope_combo, 1, 1)
        quick_layout.addWidget(QLabel("\u0414\u0438\u0430\u043f\u0430\u0437\u043e\u043d:"), 1, 2)
        quick_layout.addWidget(self.range_edit, 1, 3)
        top_layout.addLayout(quick_layout)

        actions_layout = QHBoxLayout()
        self.paste_button = QPushButton("\u0412\u0441\u0442\u0430\u0432\u0438\u0442\u044c")
        self.check_button = QPushButton("\u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c")
        self.download_button = QPushButton("\u0421\u043a\u0430\u0447\u0430\u0442\u044c")
        self.clear_button = QPushButton("\u041e\u0447\u0438\u0441\u0442\u0438\u0442\u044c")
        self.supported_sites_button = QPushButton("\u041f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u043c\u044b\u0435 \u0441\u0430\u0439\u0442\u044b")
        self.advanced_button = QPushButton("\u0415\u0449\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438")
        actions_layout.addWidget(self.paste_button)
        actions_layout.addWidget(self.check_button)
        actions_layout.addWidget(self.download_button)
        actions_layout.addWidget(self.clear_button)
        actions_layout.addStretch(1)
        actions_layout.addWidget(self.supported_sites_button)
        actions_layout.addWidget(self.advanced_button)
        top_layout.addLayout(actions_layout)

        self.current_task_label = QLabel("\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438")
        self.current_task_progress = QProgressBar()
        self.current_task_progress.setRange(0, 1)
        self.current_task_progress.setValue(0)
        self.stop_button = QPushButton("\u041e\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c \u0442\u0435\u043a\u0443\u0449\u0443\u044e")
        self.stop_button.setEnabled(False)

        status_layout = QHBoxLayout()
        status_layout.addWidget(self.current_task_label, 1)
        status_layout.addWidget(self.current_task_progress, 2)
        status_layout.addWidget(self.stop_button)
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
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

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
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
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
        self.filename_template_edit = QLineEdit()
        self.filename_template_edit.setPlaceholderText("{filename}.{extension}")
        naming_form.addRow("\u0428\u0430\u0431\u043b\u043e\u043d \u0438\u043c\u0435\u043d\u0438:", self.filename_template_edit)

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
        toggle_supported_sites = QAction("\u041f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u043c\u044b\u0435 \u0441\u0430\u0439\u0442\u044b", self)
        toggle_supported_sites.triggered.connect(self._toggle_supported_sites)
        self.menuBar().addAction(toggle_supported_sites)

        toggle_advanced = QAction("\u0414\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438", self)
        toggle_advanced.triggered.connect(self._toggle_advanced)
        self.menuBar().addAction(toggle_advanced)

    def _wire_signals(self) -> None:
        self.paste_button.clicked.connect(self._paste_urls)
        self.clear_button.clicked.connect(self.urls_edit.clear)
        self.destination_button.clicked.connect(self._choose_destination)
        self.check_button.clicked.connect(lambda: self._queue_tasks(TaskMode.CHECK))
        self.download_button.clicked.connect(lambda: self._queue_tasks(TaskMode.DOWNLOAD))
        self.supported_sites_button.clicked.connect(self._toggle_supported_sites)
        self.advanced_button.clicked.connect(self._toggle_advanced)
        self.stop_button.clicked.connect(self.runner.stop_current)
        self.log_toggle_button.clicked.connect(self._toggle_log_panel)

        self.gallery_dl_path_button.clicked.connect(self._choose_gallery_dl_path)
        self.default_folder_button.clicked.connect(self._choose_default_folder)
        self.save_settings_button.clicked.connect(self._save_settings)
        self.cookies_file_button.clicked.connect(self._choose_cookies_file)

        self.supported_sites_search_edit.textChanged.connect(self._filter_supported_sites)
        self.supported_sites_tree.currentItemChanged.connect(self._on_supported_site_selected)
        self.supported_sites_refresh_button.clicked.connect(lambda: self._start_supported_sites_refresh(manual=True))

        self.runner.task_changed.connect(self._upsert_task)
        self.runner.task_output.connect(self._append_log)
        self.runner.queue_state_changed.connect(self._update_queue_state)
        self.runner.current_task_changed.connect(self._update_current_task_banner)

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
        text = QGuiApplication.clipboard().text().strip()
        if not text:
            return
        current = self.urls_edit.toPlainText().strip()
        self.urls_edit.setPlainText(current + "\n" + text if current else text)

    def _choose_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "\u0412\u044b\u0431\u0435\u0440\u0438 \u043f\u0430\u043f\u043a\u0443 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438",
            self.destination_edit.text() or self.default_folder_edit.text(),
        )
        if folder:
            self.destination_edit.setText(folder)

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
            last_cookies_browser=self.browser_cookies_edit.text().strip(),
        )
        self.settings_service.save(self.app_settings)
        self.runner.set_gallery_dl_path(self.app_settings.gallery_dl_path)
        if not self.destination_edit.text().strip():
            self.destination_edit.setText(self.app_settings.default_download_dir)
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

        destination = self.destination_edit.text().strip() or self.app_settings.default_download_dir
        if not destination:
            QMessageBox.warning(
                self,
                "\u041d\u0435\u0442 \u043f\u0430\u043f\u043a\u0438",
                "\u0423\u043a\u0430\u0436\u0438 \u043f\u0430\u043f\u043a\u0443 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f.",
            )
            return

        options = self._collect_task_options(destination)
        tasks = [DownloadTask(url=url, mode=mode, options=options) for url in urls]
        self.runner.enqueue(tasks)
        self.status_message.setText(f"\u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e \u0437\u0430\u0434\u0430\u0447: {len(tasks)}")

    def _collect_task_options(self, destination: str) -> TaskOptions:
        media_scope = list(MediaScope)[self.media_scope_combo.currentIndex()]
        archive_map = {0: "none", 1: "zip", 2: "cbz"}
        ugoira_map = {0: "none", 1: "webm", 2: "mp4", 3: "gif", 4: "copy", 5: "zip"}
        return TaskOptions(
            destination=destination,
            organize_by_site=self.organize_by_site_check.isChecked(),
            only_new=self.only_new_check.isChecked(),
            media_scope=media_scope,
            range_text=self.range_edit.text().strip(),
            date_after=self.date_after_edit.text().strip(),
            username=self.username_edit.text().strip(),
            password=self.password_edit.text(),
            cookies_file=self.cookies_file_edit.text().strip(),
            cookies_from_browser=self.browser_cookies_edit.text().strip(),
            filename_template=self.filename_template_edit.text().strip(),
            write_metadata=self.write_metadata_check.isChecked(),
            write_info_json=self.write_info_json_check.isChecked(),
            write_tags=self.write_tags_check.isChecked(),
            archive_format=archive_map[self.archive_combo.currentIndex()],
            ugoira_format=ugoira_map[self.ugoira_combo.currentIndex()],
            proxy_url=self.proxy_edit.text().strip(),
            retries=self.retries_edit.text().strip(),
            timeout=self.timeout_edit.text().strip(),
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

    def _append_log(self, task_id: str, message: str, stream: str) -> None:
        task = self.tasks.get(task_id)
        prefix = ""
        if task is not None:
            prefix = f"[{task.mode.label} | {task.site}] "
        if stream == "stderr":
            prefix += "ERR "
        elif stream == "meta":
            prefix += "CMD "
        self.log_output.appendPlainText(prefix + message)

    def _update_queue_state(self, busy: bool) -> None:
        self.stop_button.setEnabled(busy)

    def _update_current_task_banner(self, task: DownloadTask | None) -> None:
        if task is None:
            self.current_task_label.setText("\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438")
            self.current_task_progress.setRange(0, 1)
            self.current_task_progress.setValue(0)
            return

        self.current_task_label.setText(f"{task.mode.label}: {task.title}")
        self.current_task_progress.setRange(0, 0)
