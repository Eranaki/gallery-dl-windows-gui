from __future__ import annotations

import sys
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer, Qt, Signal
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QMessageBox, QStatusBar, QTableWidgetItem

from app.models.supported_sites import SupportedSitesPayload
from app.models.task import DownloadTask, TaskMode, TaskOptions, TaskStatus
from app.services.gallery_dl_runner import GalleryDlRunner
from app.services.naming_service import (
    NamingKeywordEntry,
    build_common_keyword_entries,
    build_path_preview,
    parse_gallery_dl_keywords,
)
from app.services.settings_service import AppSettings, SettingsService
from app.services.supported_sites_service import SupportedSitesService
from app.ui.dialogs.history_dialog import HistoryDialog
from app.ui.dialogs.keyword_browser_dialog import KeywordBrowserDialog
from app.ui.docks.advanced_settings_dock import ADVANCED_SETTINGS_DOCK_WIDTH, AdvancedSettingsDock
from app.ui.docks.supported_sites_dock import SupportedSitesDock
from app.ui.i18n import txt
from app.ui.sections.downloads_section import DownloadsSection
from app.ui.widgets.url_input import extract_clipboard_urls_text


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
        self.history_records: list[list[str]] = []
        self._initialized_log_files: set[str] = set()
        self._finalized_log_files: set[str] = set()
        self._failed_log_files: set[str] = set()
        self._current_task_id: str | None = None
        self._current_part_status: str = ""
        self._current_part_path: str = ""
        self._current_part_size: int = 0
        self._current_part_timestamp: float = 0.0
        self._supported_sites_refresh_active = False
        self._supported_sites_thread: threading.Thread | None = None
        self.history_dialog: HistoryDialog | None = None

        self._download_poll_timer = QTimer(self)
        self._download_poll_timer.setInterval(1500)
        self._download_poll_timer.timeout.connect(self._poll_active_download_progress)

        self.setWindowTitle(self._txt("gallery-dl GUI", "gallery-dl GUI"))
        self.resize(1360, 860)

        self._build_ui()
        self._wire_signals()

        self.supported_sites_loaded.connect(self._on_supported_sites_loaded)
        self.supported_sites_failed.connect(self._on_supported_sites_failed)
        self._initialize_supported_sites()

    def _txt(self, ru: str, en: str) -> str:
        return txt(self.language, ru, en)

    def _build_ui(self) -> None:
        self.downloads_section = DownloadsSection(self.language, self.app_settings, self)
        self.setCentralWidget(self.downloads_section)

        self.advanced_dock = AdvancedSettingsDock(self.language, self.app_settings, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.advanced_dock)
        self.advanced_dock.hide()

        self.supported_sites_dock = SupportedSitesDock(self.language, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.supported_sites_dock)
        self.supported_sites_dock.hide()

        self.tabifyDockWidget(self.advanced_dock, self.supported_sites_dock)
        self.menuBar().hide()

        statusbar = QStatusBar()
        self.setStatusBar(statusbar)
        self.status_message = QLabel(self._txt("Готово", "Ready"))
        statusbar.addPermanentWidget(self.status_message)

    def _wire_signals(self) -> None:
        section = self.downloads_section
        advanced = self.advanced_dock

        section.paste_button.clicked.connect(self._paste_urls)
        section.clear_button.clicked.connect(section.urls_edit.clear)
        section.destination_button.clicked.connect(self._choose_destination)
        section.check_button.clicked.connect(lambda: self._queue_tasks(TaskMode.CHECK))
        section.download_button.clicked.connect(lambda: self._queue_tasks(TaskMode.DOWNLOAD))
        section.cancel_button.clicked.connect(self.runner.stop_current)
        section.supported_sites_button.clicked.connect(self._toggle_supported_sites)
        section.history_button.clicked.connect(self._open_history_window)
        section.advanced_button.clicked.connect(self._toggle_advanced)
        section.log_toggle_button.clicked.connect(self._toggle_log_panel)
        section.language_combo.currentIndexChanged.connect(self._change_language)

        advanced.gallery_dl_path_button.clicked.connect(self._choose_gallery_dl_path)
        advanced.default_folder_button.clicked.connect(self._choose_default_folder)
        advanced.save_settings_button.clicked.connect(self._save_settings)
        advanced.cookies_file_button.clicked.connect(self._choose_cookies_file)

        section.naming_preset_combo.currentIndexChanged.connect(self._apply_naming_preset)
        section.naming_fields_button.clicked.connect(self._show_available_fields)
        section.save_log_check.toggled.connect(self._persist_save_log_preference)
        section.use_original_filenames_check.toggled.connect(self._sync_filename_state)
        section.include_all_files_check.toggled.connect(section.sync_file_type_controls)
        section.include_custom_extensions_check.toggled.connect(section.sync_custom_extensions_state)
        section.naming_filename_quick_edit.textChanged.connect(self._sync_filename_template_from_quick)
        advanced.filename_template_edit.textChanged.connect(self._sync_filename_template_from_advanced)

        preview_signals = (
            section.urls_edit.textChanged,
            section.destination_edit.editTextChanged,
            section.naming_directory_edit.textChanged,
            section.naming_filename_quick_edit.textChanged,
            section.use_original_filenames_check.toggled,
            section.path_compatibility_combo.currentIndexChanged,
            section.organize_by_site_check.toggled,
            advanced.base_directory_edit.textChanged,
            advanced.path_restrict_edit.textChanged,
            advanced.path_replace_edit.textChanged,
            advanced.path_remove_edit.textChanged,
            advanced.path_strip_edit.textChanged,
        )
        for signal in preview_signals:
            signal.connect(self._update_naming_preview)

        self.supported_sites_dock.refresh_requested.connect(lambda: self._start_supported_sites_refresh(manual=True))

        self.runner.task_changed.connect(self._upsert_task)
        self.runner.task_output.connect(self._append_log)
        self.runner.queue_state_changed.connect(self._update_queue_state)
        self.runner.current_task_changed.connect(self._update_current_task_banner)

        self.downloads_section.sync_file_type_controls()
        self._sync_filename_state()
        self._update_naming_preview()

    def _load_recent_destinations(self) -> None:
        self.downloads_section.set_recent_destinations(
            self.app_settings.recent_destinations,
            self.app_settings.default_download_dir,
        )

    def _destination_text(self) -> str:
        return self.downloads_section.destination_text()

    def _set_destination_text(self, value: str) -> None:
        self.downloads_section.set_destination_text(value)

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

    def _toggle_advanced(self) -> None:
        visible = self.advanced_dock.isVisible()
        self.advanced_dock.setVisible(not visible)
        if not visible:
            self.advanced_dock.raise_()
            QTimer.singleShot(
                0,
                lambda: self.resizeDocks(
                    [self.advanced_dock],
                    [ADVANCED_SETTINGS_DOCK_WIDTH],
                    Qt.Orientation.Horizontal,
                ),
            )

    def _toggle_supported_sites(self) -> None:
        visible = self.supported_sites_dock.isVisible()
        self.supported_sites_dock.setVisible(not visible)
        if not visible:
            self.supported_sites_dock.raise_()

    def _toggle_log_panel(self) -> None:
        self.downloads_section.set_log_visible(not self.downloads_section.is_log_visible())

    def _persist_save_log_preference(self, checked: bool) -> None:
        self.app_settings = replace(self.app_settings, save_logs_by_default=checked, language=self.language)
        try:
            self.settings_service.save(self.app_settings)
        except Exception:
            pass

    def _open_history_window(self) -> None:
        if self.history_dialog is None:
            self.history_dialog = HistoryDialog(self.language, self)
            self.history_dialog.clear_requested.connect(self._clear_history_records)
        self.history_dialog.set_rows(self.history_records)
        self.history_dialog.show()
        self.history_dialog.raise_()
        self.history_dialog.activateWindow()

    def _clear_history_records(self) -> None:
        self.history_records.clear()
        self.history_rows.clear()
        if self.history_dialog is not None:
            self.history_dialog.clear_rows()
        self.status_message.setText(self._txt("История очищена", "History cleared"))

    def _sync_filename_state(self) -> None:
        use_original = self.downloads_section.use_original_filenames_check.isChecked()
        self.downloads_section.sync_filename_state()
        self.advanced_dock.filename_template_edit.setEnabled(not use_original)
        self._update_naming_preview()

    def _sync_filename_template_from_quick(self, value: str) -> None:
        if self.advanced_dock.filename_template_edit.text() != value:
            self.advanced_dock.filename_template_edit.blockSignals(True)
            self.advanced_dock.filename_template_edit.setText(value)
            self.advanced_dock.filename_template_edit.blockSignals(False)
        self._update_naming_preview()

    def _sync_filename_template_from_advanced(self, value: str) -> None:
        if self.downloads_section.naming_filename_quick_edit.text() != value:
            self.downloads_section.naming_filename_quick_edit.blockSignals(True)
            self.downloads_section.naming_filename_quick_edit.setText(value)
            self.downloads_section.naming_filename_quick_edit.blockSignals(False)
        self._update_naming_preview()

    def _apply_naming_preset(self, _index: int | None = None) -> None:
        description = self.downloads_section.apply_current_preset()
        if description is None:
            return
        self._sync_filename_template_from_quick(self.downloads_section.naming_filename_quick_edit.text())
        self.status_message.setText(description)
        self._update_naming_preview()

    def _paste_urls(self) -> None:
        text = extract_clipboard_urls_text()
        if not text:
            return
        current = self.downloads_section.urls_edit.toPlainText().strip()
        self.downloads_section.urls_edit.setPlainText(current + "\n" + text if current else text)

    def _save_settings(self) -> None:
        section_state = self.downloads_section.snapshot()
        advanced_state = self.advanced_dock.snapshot()

        self.app_settings = AppSettings(
            language=self.language,
            gallery_dl_path=advanced_state.gallery_dl_path,
            default_download_dir=advanced_state.default_folder or str(Path.home() / "Downloads"),
            recent_destinations=list(self.app_settings.recent_destinations),
            last_cookies_browser=advanced_state.cookies_from_browser,
            save_logs_by_default=section_state.save_log,
            include_all_files=section_state.include_all_files,
            include_images=section_state.include_images,
            include_videos=section_state.include_videos,
            include_archives=section_state.include_archives,
            custom_extensions=section_state.custom_extensions,
            naming_base_directory=advanced_state.base_directory,
            naming_directory_template=section_state.naming_directory_template,
            naming_filename_template=section_state.naming_filename_template,
            naming_use_original_filenames=section_state.use_original_filenames,
            naming_path_compatibility_mode=section_state.path_compatibility_mode,
            naming_path_restrict=advanced_state.path_restrict,
            naming_path_replace=advanced_state.path_replace,
            naming_path_remove=advanced_state.path_remove,
            naming_path_strip=advanced_state.path_strip,
        )
        self.settings_service.save(self.app_settings)
        self.runner.set_gallery_dl_path(self.app_settings.gallery_dl_path)
        if not self._destination_text():
            self._set_destination_text(self.app_settings.default_download_dir)
        self.status_message.setText(self._txt("Настройки сохранены", "Settings saved"))

    def _queue_tasks(self, mode: TaskMode) -> None:
        section_state = self.downloads_section.snapshot()
        urls = section_state.urls
        if not urls:
            QMessageBox.information(
                self,
                self._txt("Нет ссылок", "No URLs"),
                self._txt("Добавь хотя бы одну ссылку.", "Add at least one URL."),
            )
            return

        destination = section_state.destination or self.app_settings.default_download_dir
        if not destination:
            QMessageBox.warning(
                self,
                self._txt("Нет папки", "No folder selected"),
                self._txt("Укажи папку сохранения.", "Choose a destination folder."),
            )
            return

        if not self.downloads_section.has_selected_file_types():
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
        section_state = self.downloads_section.snapshot()
        advanced_state = self.advanced_dock.snapshot()
        return TaskOptions(
            destination=destination,
            organize_by_site=section_state.organize_by_site,
            only_new=section_state.only_new,
            save_log=section_state.save_log,
            include_all_files=section_state.include_all_files,
            include_images=section_state.include_images,
            include_videos=section_state.include_videos,
            include_archives=section_state.include_archives,
            custom_extensions=section_state.custom_extensions,
            base_directory=advanced_state.base_directory,
            directory_template=section_state.naming_directory_template,
            range_text=section_state.range_text,
            date_before=advanced_state.date_before,
            date_after=advanced_state.date_after,
            filesize_min=advanced_state.filesize_min,
            filesize_max=advanced_state.filesize_max,
            username=advanced_state.username,
            password=advanced_state.password,
            cookies_file=advanced_state.cookies_file,
            cookies_from_browser=advanced_state.cookies_from_browser,
            filename_template=section_state.naming_filename_template,
            use_original_filenames=section_state.use_original_filenames,
            path_compatibility_mode=section_state.path_compatibility_mode,
            path_restrict=advanced_state.path_restrict,
            path_replace=advanced_state.path_replace,
            path_remove=advanced_state.path_remove,
            path_strip=advanced_state.path_strip,
            write_metadata=advanced_state.write_metadata,
            write_info_json=advanced_state.write_info_json,
            write_tags=advanced_state.write_tags,
            archive_format=advanced_state.archive_format,
            ugoira_format=advanced_state.ugoira_format,
            proxy_url=advanced_state.proxy_url,
            retries=advanced_state.retries,
            timeout=advanced_state.timeout,
        )

    def _upsert_task(self, task: DownloadTask) -> None:
        self.tasks[task.id] = task
        table = self.downloads_section.queue_table
        if task.id not in self.task_rows:
            row = table.rowCount()
            table.insertRow(row)
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
            item = table.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                table.setItem(row, column, item)
            item.setText(value)

        if task.id == self._current_task_id:
            if task.status is not TaskStatus.RUNNING or task.mode is not TaskMode.DOWNLOAD:
                self._current_part_status = ""
            self._render_current_task_banner(task)

        if task.status in {TaskStatus.SUCCESS, TaskStatus.ERROR, TaskStatus.CANCELLED}:
            self._upsert_history(task)

    def _upsert_history(self, task: DownloadTask) -> None:
        values = [
            task.title,
            task.site,
            task.mode.label(self.language),
            task.status.label(self.language),
            task.last_message,
        ]
        if task.id not in self.history_rows:
            self.history_rows[task.id] = len(self.history_records)
            self.history_records.append(values)
        else:
            self.history_records[self.history_rows[task.id]] = values
        if self.history_dialog is not None:
            self.history_dialog.set_rows(self.history_records)
        self._finalize_task_log(task)

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
        self.downloads_section.log_output.appendPlainText(line)
        if task is not None:
            self._write_task_log_line(task, line)

    def _update_queue_state(self, busy: bool) -> None:
        self.downloads_section.set_cancel_enabled(busy)

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
        selected = str(self.downloads_section.language_combo.currentData() or "ru")
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
        urls = self.downloads_section.snapshot().urls
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
        dialog = KeywordBrowserDialog(
            language=self.language,
            entries=entries,
            note=note,
            raw_output=raw_output,
            on_insert_directory=self._insert_keyword_into_directory,
            on_insert_filename=self._insert_keyword_into_filename,
            parent=self,
        )
        dialog.exec()

    def _insert_keyword_into_directory(self, entry: NamingKeywordEntry) -> None:
        self.downloads_section.naming_directory_edit.insert(entry.template)
        self.downloads_section.naming_directory_edit.setFocus()
        self.status_message.setText(
            self._txt(
                f"В шаблон папок вставлено {entry.template}",
                f"Inserted {entry.template} into the folder template",
            )
        )

    def _insert_keyword_into_filename(self, entry: NamingKeywordEntry) -> None:
        if self.downloads_section.use_original_filenames_check.isChecked():
            self.downloads_section.use_original_filenames_check.setChecked(False)
        self.downloads_section.naming_filename_quick_edit.insert(entry.template)
        self.downloads_section.naming_filename_quick_edit.setFocus()
        self.status_message.setText(
            self._txt(
                f"В шаблон имени файла вставлено {entry.template}",
                f"Inserted {entry.template} into the filename template",
            )
        )

    def _update_naming_preview(self) -> None:
        url = self.downloads_section.snapshot().urls[0] if self.downloads_section.snapshot().urls else ""
        section_state = self.downloads_section.snapshot()
        advanced_state = self.advanced_dock.snapshot()
        preview, error = build_path_preview(
            destination=section_state.destination or self.app_settings.default_download_dir,
            url=url,
            directory_template=section_state.naming_directory_template,
            filename_template=section_state.naming_filename_template,
            use_original_filenames=section_state.use_original_filenames,
            path_compatibility_mode=advanced_state.path_restrict or section_state.path_compatibility_mode,
            organize_by_site=section_state.organize_by_site,
            base_directory=advanced_state.base_directory,
            path_replace=advanced_state.path_replace,
            path_remove=advanced_state.path_remove,
            path_strip=advanced_state.path_strip,
        )
        if error:
            self.downloads_section.set_naming_preview(
                self._txt(f"Ошибка preview: {error}", f"Preview error: {error}"),
                is_error=True,
            )
            return

        self.downloads_section.set_naming_preview(preview or "-", is_error=False)

    def _initialize_supported_sites(self) -> None:
        cached = self.supported_sites_service.load_cached()
        if cached is not None:
            self.supported_sites_dock.set_payload(cached)
            self.supported_sites_dock.set_status(
                self._txt("Показан кэшированный список сайтов.", "Showing the cached site list.")
            )
            if self.supported_sites_service.needs_refresh(cached):
                QTimer.singleShot(0, lambda: self._start_supported_sites_refresh(manual=False))
            return

        self.supported_sites_dock.set_status(
            self._txt("Список сайтов загружается с GitHub...", "Loading the site list from GitHub...")
        )
        QTimer.singleShot(0, lambda: self._start_supported_sites_refresh(manual=False))

    def _start_supported_sites_refresh(self, *, manual: bool) -> None:
        if self._supported_sites_refresh_active:
            if manual:
                self.supported_sites_dock.set_status(
                    self._txt("Обновление уже выполняется.", "A refresh is already in progress.")
                )
            return

        self._supported_sites_refresh_active = True
        self.supported_sites_dock.set_refresh_active(True)
        self.supported_sites_dock.set_status(
            self._txt(
                "Обновляю список поддерживаемых сайтов...",
                "Refreshing the supported sites list...",
            )
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
        self.supported_sites_dock.set_refresh_active(False)

        if not isinstance(payload, SupportedSitesPayload):
            self._on_supported_sites_failed(self._txt("Некорректный ответ от GitHub.", "Invalid response from GitHub."), manual)
            return

        self.supported_sites_dock.set_payload(payload)
        self.supported_sites_dock.set_status(self._txt("Список сайтов актуален.", "The site list is up to date."))
        if manual:
            self.status_message.setText(
                self._txt(
                    "Список поддерживаемых сайтов обновлен",
                    "The supported sites list has been updated",
                )
            )

    def _on_supported_sites_failed(self, message: str, manual: bool) -> None:
        self._supported_sites_refresh_active = False
        self.supported_sites_dock.set_refresh_active(False)

        if self.supported_sites_dock.payload is None:
            self.supported_sites_dock.set_status(
                self._txt(
                    "Не удалось загрузить список сайтов. Проверь сеть или попробуй позже.",
                    "Could not load the site list. Check your network connection and try again later.",
                )
            )
        else:
            self.supported_sites_dock.set_status(
                self._txt(
                    "Не удалось обновить список. Показан сохраненный кэш.",
                    "Could not refresh the list. Showing the saved cache.",
                )
            )

        if manual:
            self.status_message.setText(message)

    def _choose_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self._txt("Выбери папку загрузки", "Choose the download folder"),
            self._destination_text() or self.advanced_dock.default_folder_edit.text(),
        )
        if folder:
            self._register_recent_destination(folder)
            self.settings_service.save(self.app_settings)

    def _choose_default_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self._txt("Папка по умолчанию", "Default folder"),
            self.advanced_dock.default_folder_edit.text() or str(Path.home() / "Downloads"),
        )
        if folder:
            self.advanced_dock.default_folder_edit.setText(folder)

    def _choose_gallery_dl_path(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self._txt("Укажи путь к gallery-dl", "Choose the path to gallery-dl"),
            str(Path.home()),
            self._txt("Исполняемые файлы (*.exe);;Все файлы (*.*)", "Executable files (*.exe);;All files (*.*)"),
        )
        if file_path:
            self.advanced_dock.gallery_dl_path_edit.setText(file_path)

    def _choose_cookies_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self._txt("Выбери cookies файл", "Choose a cookies file"),
            str(Path.home()),
            "Text files (*.txt *.json);;All files (*.*)",
        )
        if file_path:
            self.advanced_dock.cookies_file_edit.setText(file_path)

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
            self.downloads_section.log_output.appendPlainText(
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
            self.downloads_section.set_current_task_banner(None)
            return

        details = self._current_part_status or task.last_message or task.progress_text
        self.downloads_section.set_current_task_banner(f"{task.mode.label(self.language)}: {task.title}\n{details}")

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
            item = self.downloads_section.queue_table.item(row, 5)
            if item is None:
                item = QTableWidgetItem()
                self.downloads_section.queue_table.setItem(row, 5, item)
            item.setText(message)
        self._render_current_task_banner(task)
