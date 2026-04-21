from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.services.naming_service import get_naming_presets, get_preset_by_id
from app.services.settings_service import AppSettings
from app.ui.i18n import txt
from app.ui.widgets.url_input import UrlInputTextEdit


ARCHIVE_EXTENSIONS_HINT = ".zip, .rar, .7z, .tar, .gz, .bz2, .xz, .tgz, .tbz2, .txz, .cbz, .cbr, .cb7, .cbt, .zst ..."


@dataclass(slots=True)
class DownloadsSectionState:
    urls: list[str]
    destination: str
    only_new: bool
    organize_by_site: bool
    save_log: bool
    include_all_files: bool
    include_images: bool
    include_videos: bool
    include_archives: bool
    custom_extensions: str
    range_text: str
    naming_directory_template: str
    naming_filename_template: str
    use_original_filenames: bool
    path_compatibility_mode: str


class DownloadsSection(QWidget):
    def __init__(self, language: str, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.language = language
        self.settings = settings
        self._log_visible = True

        self.urls_edit = UrlInputTextEdit()
        self.paste_button = QPushButton()
        self.clear_button = QPushButton()
        self.destination_edit = QComboBox()
        self.destination_button = QPushButton()
        self.only_new_check = QCheckBox()
        self.organize_by_site_check = QCheckBox()
        self.save_log_check = QCheckBox()
        self.include_all_files_check = QCheckBox()
        self.include_images_check = QCheckBox()
        self.include_videos_check = QCheckBox()
        self.include_archives_check = QCheckBox()
        self.include_custom_extensions_check = QCheckBox()
        self.custom_extensions_edit = QLineEdit(settings.custom_extensions)
        self.range_edit = QLineEdit()

        self.naming_preset_combo = QComboBox()
        self.naming_fields_button = QPushButton()
        self.naming_directory_edit = QLineEdit(settings.naming_directory_template)
        self.naming_filename_quick_edit = QLineEdit(settings.naming_filename_template)
        self.use_original_filenames_check = QCheckBox()
        self.path_compatibility_combo = QComboBox()
        self.naming_preview_label = QLabel("-")

        self.check_button = QPushButton()
        self.download_button = QPushButton()
        self.cancel_button = QPushButton()
        self.supported_sites_button = QPushButton()
        self.history_button = QPushButton()
        self.advanced_button = QPushButton()
        self.language_label = QLabel()
        self.language_combo = QComboBox()

        self.current_task_label = QLabel()
        self.current_task_progress = QProgressBar()
        self.queue_table = QTableWidget(0, 6)
        self.log_panel = QWidget()
        self.log_toggle_button = QToolButton()
        self.log_output = QPlainTextEdit()

        self._build_ui()
        self.retranslate(language)
        self.set_recent_destinations(settings.recent_destinations[:10], settings.default_download_dir)
        self._apply_settings(settings)
        self.sync_file_type_controls()
        self.sync_filename_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top_card = QFrame()
        top_card.setFrameShape(QFrame.Shape.StyledPanel)
        top_layout = QVBoxLayout(top_card)
        top_layout.setSpacing(10)

        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        top_layout.addWidget(self.title_label)

        urls_layout = QHBoxLayout()
        self.urls_edit.setMinimumHeight(120)
        urls_layout.addWidget(self.urls_edit, 1)

        url_buttons_layout = QVBoxLayout()
        url_buttons_layout.setSpacing(8)
        for button in (self.paste_button, self.clear_button):
            button.setMinimumHeight(36)
            button.setMinimumWidth(120)
            url_buttons_layout.addWidget(button)
        url_buttons_layout.addStretch(1)
        urls_layout.addLayout(url_buttons_layout)
        top_layout.addLayout(urls_layout)

        path_layout = QHBoxLayout()
        self.destination_edit.setEditable(True)
        self.destination_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.destination_edit.setMinimumContentsLength(40)
        self.folder_label = QLabel()
        path_layout.addWidget(self.folder_label)
        path_layout.addWidget(self.destination_edit, 1)
        path_layout.addWidget(self.destination_button)
        top_layout.addLayout(path_layout)

        quick_layout = QGridLayout()
        self.file_types_label = QLabel()
        self.range_label = QLabel()
        self.only_new_check.setChecked(True)
        self.organize_by_site_check.setChecked(True)
        self.save_log_check.setChecked(self.settings.save_logs_by_default)
        self.include_all_files_check.setChecked(self.settings.include_all_files)
        self.include_images_check.setChecked(self.settings.include_images)
        self.include_videos_check.setChecked(self.settings.include_videos)
        self.include_archives_check.setChecked(self.settings.include_archives)
        if self.settings.custom_extensions.strip():
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
        quick_layout.addWidget(self.file_types_label, 1, 0)
        quick_layout.addWidget(file_types_widget, 1, 1, 1, 3)
        quick_layout.addWidget(self.range_label, 2, 0)
        quick_layout.addWidget(self.range_edit, 2, 1, 1, 3)
        top_layout.addLayout(quick_layout)

        naming_group = QGroupBox()
        naming_layout = QGridLayout(naming_group)
        self.naming_template_label = QLabel()
        self.folder_structure_label = QLabel()
        self.filename_label = QLabel()
        self.compatibility_label = QLabel()
        self.preview_title_label = QLabel()

        self.path_compatibility_combo.addItem("", "auto")
        self.path_compatibility_combo.addItem("Windows-safe", "windows")
        self.path_compatibility_combo.addItem("ASCII-safe", "ascii")
        self.naming_preview_label.setWordWrap(True)
        self.naming_preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        naming_layout.addWidget(self.naming_template_label, 0, 0)
        naming_layout.addWidget(self.naming_preset_combo, 0, 1)
        naming_layout.addWidget(self.naming_fields_button, 0, 2)
        naming_layout.addWidget(self.folder_structure_label, 1, 0)
        naming_layout.addWidget(self.naming_directory_edit, 1, 1, 1, 3)
        naming_layout.addWidget(self.filename_label, 2, 0)
        naming_layout.addWidget(self.naming_filename_quick_edit, 2, 1, 1, 3)
        naming_layout.addWidget(self.use_original_filenames_check, 3, 0, 1, 2)
        naming_layout.addWidget(self.compatibility_label, 3, 2)
        naming_layout.addWidget(self.path_compatibility_combo, 3, 3)
        naming_layout.addWidget(self.preview_title_label, 4, 0)
        naming_layout.addWidget(self.naming_preview_label, 4, 1, 1, 3)
        self.naming_group = naming_group
        top_layout.addWidget(naming_group)

        primary_actions_layout = QHBoxLayout()
        self.cancel_button.setEnabled(False)
        self.language_combo.addItem("Русский", "ru")
        self.language_combo.addItem("English", "en")
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

        self.current_task_label.setWordWrap(True)
        self.current_task_progress.setRange(0, 1)
        self.current_task_progress.setValue(0)
        status_layout = QHBoxLayout()
        status_layout.addWidget(self.current_task_label, 1)
        status_layout.addWidget(self.current_task_progress, 2)
        top_layout.addLayout(status_layout)
        root.addWidget(top_card)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
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

        log_layout = QVBoxLayout(self.log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_header = QHBoxLayout()
        self.log_title = QLabel()
        self.log_title.setStyleSheet("font-weight: 600;")
        log_header.addWidget(self.log_title)
        log_header.addStretch(1)
        log_header.addWidget(self.log_toggle_button)
        log_layout.addLayout(log_header)
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_output)

        self.splitter.addWidget(self.queue_table)
        self.splitter.addWidget(self.log_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        root.addWidget(self.splitter, 1)

    def retranslate(self, language: str) -> None:
        self.language = language
        self.title_label.setText(txt(language, "Новая задача", "New task"))
        self.urls_edit.setPlaceholderText(
            txt(
                language,
                "Вставь одну или несколько ссылок. Каждая ссылка с новой строки.",
                "Paste one or more URLs. Put each URL on a new line.",
            )
        )
        self.paste_button.setText(txt(language, "Вставить", "Paste"))
        self.clear_button.setText(txt(language, "Очистить", "Clear"))
        self.folder_label.setText(txt(language, "Папка:", "Folder:"))
        self.destination_button.setText(txt(language, "Обзор", "Browse"))
        self.only_new_check.setText(txt(language, "Только новое", "Only new"))
        self.organize_by_site_check.setText(txt(language, "Создавать папки по сайту", "Create folders by site"))
        self.save_log_check.setText(txt(language, "Сохранять лог в файл", "Save log to file"))
        self.save_log_check.setToolTip(
            txt(
                language,
                "Для этой задачи будет создан отдельный log-файл в папке gallery-dl-logs.",
                "This task will create a separate log file in the gallery-dl-logs folder.",
            )
        )
        self.include_all_files_check.setText(txt(language, "Всё", "All"))
        self.include_images_check.setText(txt(language, "Изображения", "Images"))
        self.include_videos_check.setText(txt(language, "Видео", "Videos"))
        self.include_archives_check.setText(txt(language, "Архивы", "Archives"))
        self.include_archives_check.setToolTip(
            txt(language, "Основные архивные расширения: ", "Common archive extensions: ")
            + ARCHIVE_EXTENSIONS_HINT
        )
        self.include_custom_extensions_check.setText(txt(language, "Свое", "Custom"))
        self.custom_extensions_edit.setPlaceholderText(".psd, .epub, .pdf")
        self.range_edit.setPlaceholderText("5, 1-20, 1:20, 1:24:3")
        self.range_edit.setToolTip(
            txt(
                language,
                "Позволяет скачать только часть файлов по порядковым номерам.",
                "Lets you download only part of the files by their index numbers.",
            )
        )
        self.file_types_label.setText(txt(language, "Типы файлов:", "File types:"))
        self.range_label.setText(txt(language, "Какие элементы скачивать:", "Which items to download:"))

        self.naming_group.setTitle(txt(language, "Именование", "Naming"))
        self.path_compatibility_combo.setItemText(0, txt(language, "Авто", "Auto"))
        self.naming_preset_combo.blockSignals(True)
        self.naming_preset_combo.clear()
        self.naming_preset_combo.addItem(txt(language, "Шаблон не выбран", "No template selected"), "")
        for preset in get_naming_presets(language):
            self.naming_preset_combo.addItem(preset.label, preset.id)
        self.naming_preset_combo.blockSignals(False)
        self.naming_fields_button.setText(txt(language, "Доступные поля", "Available fields"))
        self.naming_template_label.setText(txt(language, "Шаблон:", "Template:"))
        self.folder_structure_label.setText(txt(language, "Структура папок:", "Folder structure:"))
        self.filename_label.setText(txt(language, "Имя файла:", "File name:"))
        self.compatibility_label.setText(txt(language, "Совместимость имен:", "Name compatibility:"))
        self.preview_title_label.setText(txt(language, "Предпросмотр пути:", "Path preview:"))
        self.naming_directory_edit.setPlaceholderText(
            txt(
                language,
                "Пусто = как у gallery-dl, например: {category}/{user[id]}",
                "Empty = use gallery-dl default, for example: {category}/{user[id]}",
            )
        )
        self.use_original_filenames_check.setText(
            txt(language, "Использовать оригинальные имена", "Use original names")
        )
        if not self.use_original_filenames_check.isChecked():
            self.naming_filename_quick_edit.setPlaceholderText(
                txt(
                    language,
                    "Пусто = как у gallery-dl, например: {title}.{extension}",
                    "Empty = use gallery-dl default, for example: {title}.{extension}",
                )
            )

        self.check_button.setText(txt(language, "Проверить", "Check"))
        self.download_button.setText(txt(language, "Скачать", "Download"))
        self.cancel_button.setText(txt(language, "Отменить", "Cancel"))
        self.supported_sites_button.setText(txt(language, "Поддерживаемые сайты", "Supported sites"))
        self.history_button.setText(txt(language, "История", "History"))
        self.advanced_button.setText(txt(language, "Еще настройки", "More settings"))
        self.language_label.setText(txt(language, "Язык:", "Language:"))

        self.current_task_label.setText(txt(language, "Нет активной задачи", "No active task"))
        self.queue_table.setHorizontalHeaderLabels(
            [
                "URL",
                txt(language, "Сайт", "Site"),
                txt(language, "Режим", "Mode"),
                txt(language, "Статус", "Status"),
                txt(language, "Папка", "Folder"),
                txt(language, "Последнее сообщение", "Last message"),
            ]
        )
        self.log_title.setText(txt(language, "Журнал", "Log"))
        self.log_toggle_button.setText(
            txt(language, "Скрыть журнал", "Hide log")
            if self._log_visible
            else txt(language, "Показать журнал", "Show log")
        )
        self._set_combo_value(self.language_combo, language)
        self._set_combo_value(
            self.path_compatibility_combo,
            self.settings.naming_path_compatibility_mode or "auto",
        )

    def _apply_settings(self, settings: AppSettings) -> None:
        self.use_original_filenames_check.setChecked(settings.naming_use_original_filenames)

    def _set_combo_value(self, combo: QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def set_recent_destinations(self, recent_destinations: list[str], fallback: str) -> None:
        self.destination_edit.blockSignals(True)
        self.destination_edit.clear()
        for path in recent_destinations[:10]:
            self.destination_edit.addItem(path)
        self.destination_edit.blockSignals(False)
        self.destination_edit.setCurrentText(recent_destinations[0] if recent_destinations else fallback)

    def destination_text(self) -> str:
        return self.destination_edit.currentText().strip()

    def set_destination_text(self, value: str) -> None:
        self.destination_edit.setCurrentText(value)

    def selected_naming_compatibility(self) -> str:
        return str(self.path_compatibility_combo.currentData() or "auto")

    def sync_filename_state(self) -> None:
        use_original = self.use_original_filenames_check.isChecked()
        self.naming_filename_quick_edit.setEnabled(not use_original)
        if use_original:
            self.naming_filename_quick_edit.setPlaceholderText(
                txt(
                    self.language,
                    "Выключено: используются оригинальные имена",
                    "Disabled: original names are used",
                )
            )
        else:
            self.naming_filename_quick_edit.setPlaceholderText(
                txt(
                    self.language,
                    "Пусто = как у gallery-dl, например: {title}.{extension}",
                    "Empty = use gallery-dl default, for example: {title}.{extension}",
                )
            )

    def sync_custom_extensions_state(self) -> None:
        if self.include_all_files_check.isChecked():
            self.custom_extensions_edit.setEnabled(False)
            return
        self.custom_extensions_edit.setEnabled(self.include_custom_extensions_check.isChecked())

    def sync_file_type_controls(self) -> None:
        include_all = self.include_all_files_check.isChecked()
        for checkbox in (
            self.include_images_check,
            self.include_videos_check,
            self.include_archives_check,
            self.include_custom_extensions_check,
        ):
            checkbox.setEnabled(not include_all)
        self.sync_custom_extensions_state()

    def apply_current_preset(self) -> str | None:
        preset_id = str(self.naming_preset_combo.currentData() or "")
        preset = get_preset_by_id(preset_id, self.language)
        if preset is None:
            return None
        self.naming_directory_edit.setText(preset.directory_template)
        self.naming_filename_quick_edit.setText(preset.filename_template)
        self.use_original_filenames_check.setChecked(preset.use_original_filenames)
        return preset.description

    def has_selected_file_types(self) -> bool:
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

    def snapshot(self) -> DownloadsSectionState:
        return DownloadsSectionState(
            urls=[line.strip() for line in self.urls_edit.toPlainText().splitlines() if line.strip()],
            destination=self.destination_text(),
            only_new=self.only_new_check.isChecked(),
            organize_by_site=self.organize_by_site_check.isChecked(),
            save_log=self.save_log_check.isChecked(),
            include_all_files=self.include_all_files_check.isChecked(),
            include_images=self.include_images_check.isChecked(),
            include_videos=self.include_videos_check.isChecked(),
            include_archives=self.include_archives_check.isChecked(),
            custom_extensions=self.custom_extensions_edit.text().strip()
            if self.include_custom_extensions_check.isChecked()
            else "",
            range_text=self.range_edit.text().strip(),
            naming_directory_template=self.naming_directory_edit.text().strip(),
            naming_filename_template=self.naming_filename_quick_edit.text().strip(),
            use_original_filenames=self.use_original_filenames_check.isChecked(),
            path_compatibility_mode=self.selected_naming_compatibility(),
        )

    def set_naming_preview(self, text_value: str, *, is_error: bool) -> None:
        self.naming_preview_label.setText(text_value)
        self.naming_preview_label.setStyleSheet("color: #b00020;" if is_error else "")

    def set_log_visible(self, visible: bool) -> None:
        self._log_visible = visible
        self.log_output.setVisible(visible)
        self.log_toggle_button.setText(
            txt(self.language, "Показать журнал", "Show log")
            if not visible
            else txt(self.language, "Скрыть журнал", "Hide log")
        )

    def is_log_visible(self) -> bool:
        return self._log_visible

    def set_cancel_enabled(self, enabled: bool) -> None:
        self.cancel_button.setEnabled(enabled)

    def set_current_task_banner(self, text_value: str | None) -> None:
        if not text_value:
            self.current_task_label.setText(txt(self.language, "Нет активной задачи", "No active task"))
            self.current_task_progress.setRange(0, 1)
            self.current_task_progress.setValue(0)
            return
        self.current_task_label.setText(text_value)
        self.current_task_progress.setRange(0, 0)
