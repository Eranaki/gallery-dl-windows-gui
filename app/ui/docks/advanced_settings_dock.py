from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.services.settings_service import AppSettings
from app.ui.i18n import txt


ADVANCED_SETTINGS_DOCK_WIDTH = 520


@dataclass(slots=True)
class AdvancedSettingsState:
    username: str
    password: str
    cookies_file: str
    cookies_from_browser: str
    date_before: str
    date_after: str
    filesize_min: str
    filesize_max: str
    write_metadata: bool
    write_info_json: bool
    write_tags: bool
    archive_format: str
    ugoira_format: str
    gallery_dl_path: str
    default_folder: str
    proxy_url: str
    retries: str
    timeout: str
    filename_template: str
    base_directory: str
    path_restrict: str
    path_replace: str
    path_remove: str
    path_strip: str


class AdvancedSettingsDock(QDockWidget):
    def __init__(self, language: str, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.language = language

        self.username_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.cookies_file_edit = QLineEdit()
        self.cookies_file_button = QPushButton()
        self.browser_cookies_edit = QLineEdit(settings.last_cookies_browser)

        self.date_before_edit = QLineEdit()
        self.date_after_edit = QLineEdit()
        self.filesize_min_edit = QLineEdit()
        self.filesize_max_edit = QLineEdit()

        self.write_metadata_check = QCheckBox()
        self.write_info_json_check = QCheckBox()
        self.write_tags_check = QCheckBox()
        self.archive_combo = QComboBox()
        self.ugoira_combo = QComboBox()

        self.gallery_dl_path_edit = QLineEdit(settings.gallery_dl_path)
        self.gallery_dl_path_button = QPushButton()
        self.default_folder_edit = QLineEdit(settings.default_download_dir)
        self.default_folder_button = QPushButton()
        self.proxy_edit = QLineEdit()
        self.retries_edit = QLineEdit()
        self.timeout_edit = QLineEdit()

        self.filename_template_edit = QLineEdit(settings.naming_filename_template)
        self.base_directory_edit = QLineEdit(settings.naming_base_directory)
        self.path_restrict_edit = QLineEdit(settings.naming_path_restrict)
        self.path_replace_edit = QLineEdit(settings.naming_path_replace)
        self.path_remove_edit = QLineEdit(settings.naming_path_remove)
        self.path_strip_edit = QLineEdit(settings.naming_path_strip)

        self.save_settings_button = QPushButton()

        self._build_ui()
        self.retranslate(language)

    def _build_ui(self) -> None:
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.setMinimumWidth(ADVANCED_SETTINGS_DOCK_WIDTH)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        cookies_widget = QWidget()
        cookies_layout = QHBoxLayout(cookies_widget)
        cookies_layout.setContentsMargins(0, 0, 0, 0)
        cookies_layout.addWidget(self.cookies_file_edit, 1)
        cookies_layout.addWidget(self.cookies_file_button)

        auth_content = QWidget()
        auth_form = QFormLayout(auth_content)
        self.username_label = QLabel()
        self.password_label = QLabel()
        self.cookies_file_label = QLabel()
        self.browser_cookies_label = QLabel()
        auth_form.addRow(self.username_label, self.username_edit)
        auth_form.addRow(self.password_label, self.password_edit)
        auth_form.addRow(self.cookies_file_label, cookies_widget)
        auth_form.addRow(self.browser_cookies_label, self.browser_cookies_edit)

        filters_content = QWidget()
        filters_form = QFormLayout(filters_content)
        self.date_before_label = QLabel()
        self.date_after_label = QLabel()
        self.filesize_min_label = QLabel()
        self.filesize_max_label = QLabel()
        filters_form.addRow(self.date_before_label, self.date_before_edit)
        filters_form.addRow(self.date_after_label, self.date_after_edit)
        filters_form.addRow(self.filesize_min_label, self.filesize_min_edit)
        filters_form.addRow(self.filesize_max_label, self.filesize_max_edit)

        post_content = QWidget()
        post_form = QFormLayout(post_content)
        post_form.addRow(self.write_metadata_check)
        post_form.addRow(self.write_info_json_check)
        post_form.addRow(self.write_tags_check)
        self.archive_label = QLabel()
        self.ugoira_label = QLabel()
        post_form.addRow(self.archive_label, self.archive_combo)
        post_form.addRow(self.ugoira_label, self.ugoira_combo)

        path_widget = QWidget()
        path_layout = QHBoxLayout(path_widget)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.addWidget(self.gallery_dl_path_edit, 1)
        path_layout.addWidget(self.gallery_dl_path_button)

        folder_widget = QWidget()
        folder_layout = QHBoxLayout(folder_widget)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(self.default_folder_edit, 1)
        folder_layout.addWidget(self.default_folder_button)

        app_network_content = QWidget()
        app_network_form = QFormLayout(app_network_content)
        self.gallery_dl_path_label = QLabel()
        self.default_folder_label = QLabel()
        self.proxy_label = QLabel()
        self.retries_label = QLabel()
        self.timeout_label = QLabel()
        app_network_form.addRow(self.gallery_dl_path_label, path_widget)
        app_network_form.addRow(self.default_folder_label, folder_widget)
        app_network_form.addRow(self.proxy_label, self.proxy_edit)
        app_network_form.addRow(self.retries_label, self.retries_edit)
        app_network_form.addRow(self.timeout_label, self.timeout_edit)

        expert_content = QWidget()
        expert_form = QFormLayout(expert_content)
        self.filename_template_label = QLabel()
        self.base_directory_label = QLabel()
        self.path_restrict_label = QLabel()
        self.path_replace_label = QLabel()
        self.path_remove_label = QLabel()
        self.path_strip_label = QLabel()
        expert_form.addRow(self.filename_template_label, self.filename_template_edit)
        expert_form.addRow(self.base_directory_label, self.base_directory_edit)
        expert_form.addRow(self.path_restrict_label, self.path_restrict_edit)
        expert_form.addRow(self.path_replace_label, self.path_replace_edit)
        expert_form.addRow(self.path_remove_label, self.path_remove_edit)
        expert_form.addRow(self.path_strip_label, self.path_strip_edit)

        self.auth_section = self._create_collapsible_section("", auth_content, expanded=False)
        self.filters_section = self._create_collapsible_section("", filters_content, expanded=True)
        self.post_section = self._create_collapsible_section("", post_content, expanded=True)
        self.app_network_section = self._create_collapsible_section("", app_network_content, expanded=True)
        self.expert_section = self._create_collapsible_section("", expert_content, expanded=False)

        layout.addWidget(self.auth_section)
        layout.addWidget(self.filters_section)
        layout.addWidget(self.post_section)
        layout.addWidget(self.app_network_section)
        layout.addWidget(self.expert_section)
        layout.addWidget(self.save_settings_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

        scroll.setWidget(container)
        self.setWidget(scroll)

        self.browser_cookies_edit.setPlaceholderText("firefox, chrome, edge...")
        self.date_before_edit.setPlaceholderText("2026-12-31")
        self.date_after_edit.setPlaceholderText("2026-01-01")
        self.filesize_min_edit.setPlaceholderText("500k")
        self.filesize_max_edit.setPlaceholderText("2.5M")
        self.filename_template_edit.setPlaceholderText("{filename}.{extension}")
        self.path_restrict_edit.setPlaceholderText("auto / windows / ascii")
        self.path_replace_edit.setPlaceholderText("_")
        self.path_remove_edit.setPlaceholderText("\\x00-\\x1f\\x7f")
        self.path_strip_edit.setPlaceholderText(". ")

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
        wrapper.toggle_button = toggle  # type: ignore[attr-defined]
        return wrapper

    def retranslate(self, language: str) -> None:
        self.language = language
        self.setWindowTitle(txt(language, "Дополнительные настройки", "Additional settings"))
        self.username_label.setText(txt(language, "Логин:", "Username:"))
        self.password_label.setText(txt(language, "Пароль:", "Password:"))
        self.cookies_file_label.setText(txt(language, "Cookies файл:", "Cookies file:"))
        self.browser_cookies_label.setText(txt(language, "Cookies из браузера:", "Cookies from browser:"))
        self.date_before_label.setText(txt(language, "Дата до:", "Date before:"))
        self.date_after_label.setText(txt(language, "Дата после:", "Date after:"))
        self.filesize_min_label.setText(txt(language, "Размер от:", "Size from:"))
        self.filesize_max_label.setText(txt(language, "Размер до:", "Size to:"))
        self.cookies_file_button.setText(txt(language, "Обзор", "Browse"))
        self.gallery_dl_path_button.setText(txt(language, "Обзор", "Browse"))
        self.default_folder_button.setText(txt(language, "Обзор", "Browse"))
        self.write_metadata_check.setText(txt(language, "Сохранять metadata (.json)", "Save metadata (.json)"))
        self.write_info_json_check.setText(txt(language, "Сохранять info.json", "Save info.json"))
        self.write_tags_check.setText(txt(language, "Сохранять теги", "Save tags"))
        self.archive_label.setText(txt(language, "Упаковать:", "Pack into:"))
        self.ugoira_label.setText("Ugoira:")
        self.archive_combo.clear()
        self.archive_combo.addItems([txt(language, "Нет", "None"), "ZIP", "CBZ"])
        self.ugoira_combo.clear()
        self.ugoira_combo.addItems([txt(language, "Нет", "None"), "WEBM", "MP4", "GIF", "Copy", "ZIP"])
        self.gallery_dl_path_label.setText(txt(language, "Путь к gallery-dl:", "Path to gallery-dl:"))
        self.default_folder_label.setText(txt(language, "Папка по умолчанию:", "Default folder:"))
        self.proxy_label.setText(txt(language, "Прокси:", "Proxy:"))
        self.retries_label.setText(txt(language, "Повторы:", "Retries:"))
        self.timeout_label.setText(txt(language, "Таймаут:", "Timeout:"))
        self.filename_template_label.setText(txt(language, "Сырой шаблон имени:", "Raw filename template:"))
        self.base_directory_label.setText("Base directory:")
        self.path_restrict_label.setText("Path restrict:")
        self.path_replace_label.setText("Path replace:")
        self.path_remove_label.setText("Path remove:")
        self.path_strip_label.setText("Path strip:")
        self.base_directory_edit.setPlaceholderText(
            txt(
                language,
                "Пусто = брать основную папку загрузки",
                "Empty = use the main download folder",
            )
        )
        self.save_settings_button.setText(txt(language, "Сохранить настройки", "Save settings"))

        self.auth_section.toggle_button.setText(txt(language, "Доступ", "Access"))  # type: ignore[attr-defined]
        self.filters_section.toggle_button.setText(txt(language, "Фильтры", "Filters"))  # type: ignore[attr-defined]
        self.post_section.toggle_button.setText(txt(language, "После загрузки", "After download"))  # type: ignore[attr-defined]
        self.app_network_section.toggle_button.setText(txt(language, "Приложение и сеть", "App and network"))  # type: ignore[attr-defined]
        self.expert_section.toggle_button.setText(txt(language, "Для опытных", "Expert"))  # type: ignore[attr-defined]

    def snapshot(self) -> AdvancedSettingsState:
        archive_map = {0: "none", 1: "zip", 2: "cbz"}
        ugoira_map = {0: "none", 1: "webm", 2: "mp4", 3: "gif", 4: "copy", 5: "zip"}
        return AdvancedSettingsState(
            username=self.username_edit.text().strip(),
            password=self.password_edit.text(),
            cookies_file=self.cookies_file_edit.text().strip(),
            cookies_from_browser=self.browser_cookies_edit.text().strip(),
            date_before=self.date_before_edit.text().strip(),
            date_after=self.date_after_edit.text().strip(),
            filesize_min=self.filesize_min_edit.text().strip(),
            filesize_max=self.filesize_max_edit.text().strip(),
            write_metadata=self.write_metadata_check.isChecked(),
            write_info_json=self.write_info_json_check.isChecked(),
            write_tags=self.write_tags_check.isChecked(),
            archive_format=archive_map[self.archive_combo.currentIndex()],
            ugoira_format=ugoira_map[self.ugoira_combo.currentIndex()],
            gallery_dl_path=self.gallery_dl_path_edit.text().strip() or "gallery-dl",
            default_folder=self.default_folder_edit.text().strip(),
            proxy_url=self.proxy_edit.text().strip(),
            retries=self.retries_edit.text().strip(),
            timeout=self.timeout_edit.text().strip(),
            filename_template=self.filename_template_edit.text().strip(),
            base_directory=self.base_directory_edit.text().strip(),
            path_restrict=self.path_restrict_edit.text().strip(),
            path_replace=self.path_replace_edit.text(),
            path_remove=self.path_remove_edit.text(),
            path_strip=self.path_strip_edit.text(),
        )
