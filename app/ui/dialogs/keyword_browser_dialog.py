from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from app.services.naming_service import NamingKeywordEntry, get_group_order
from app.ui.i18n import txt


class ReadOnlyTextDialog(QDialog):
    def __init__(self, language: str, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        layout.addWidget(editor)
        close_button = QPushButton(txt(language, "Закрыть", "Close"))
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)


class KeywordBrowserDialog(QDialog):
    def __init__(
        self,
        *,
        language: str,
        entries: list[NamingKeywordEntry],
        note: str,
        raw_output: str,
        on_insert_directory: Callable[[NamingKeywordEntry], None],
        on_insert_filename: Callable[[NamingKeywordEntry], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.language = language
        self.entries = entries
        self.raw_output = raw_output
        self.on_insert_directory = on_insert_directory
        self.on_insert_filename = on_insert_filename

        self.setWindowTitle(txt(language, "Доступные поля", "Available fields"))
        self.resize(1080, 680)

        layout = QVBoxLayout(self)

        note_label = QLabel(note)
        note_label.setWordWrap(True)
        note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(note_label)

        helper_label = QLabel(
            txt(
                language,
                "Выбери поле в списке ниже. Вставка идет в текущее место курсора в шаблоне.",
                "Select a field below. It will be inserted at the current cursor position in the template.",
            )
        )
        helper_label.setStyleSheet("color: #555;")
        helper_label.setWordWrap(True)
        layout.addWidget(helper_label)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel(txt(language, "Поиск:", "Search:")))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            txt(language, "Например: title, date, filename, user", "Example: title, date, filename, user")
        )
        search_layout.addWidget(self.search_edit, 1)
        layout.addLayout(search_layout)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            (
                txt(language, "Поле", "Field"),
                txt(language, "Пример", "Example"),
                txt(language, "Что означает", "Meaning"),
                txt(language, "Где использовать", "Usage"),
            )
        )
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, 240)
        self.tree.setColumnWidth(1, 220)
        self.tree.setColumnWidth(3, 150)
        self._populate_tree()
        layout.addWidget(self.tree, 1)

        selected_group = QGroupBox(txt(language, "Выбранное поле", "Selected field"))
        selected_layout = QGridLayout(selected_group)
        self.token_value = QLabel(txt(language, "Выбери поле в списке.", "Select a field from the list."))
        self.token_value.setWordWrap(True)
        self.token_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.description_value = QLabel("-")
        self.description_value.setWordWrap(True)
        self.description_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        selected_layout.addWidget(QLabel(txt(language, "Шаблон:", "Template:")), 0, 0)
        selected_layout.addWidget(self.token_value, 0, 1)
        selected_layout.addWidget(QLabel(txt(language, "Пояснение:", "Description:")), 1, 0)
        selected_layout.addWidget(self.description_value, 1, 1)
        layout.addWidget(selected_group)

        buttons_layout = QHBoxLayout()
        self.insert_directory_button = QPushButton(txt(language, "Вставить в папку", "Insert into folder"))
        self.insert_filename_button = QPushButton(txt(language, "Вставить в имя файла", "Insert into file name"))
        self.raw_button = QPushButton(txt(language, "Показать сырой вывод", "Show raw output"))
        self.close_button = QPushButton(txt(language, "Закрыть", "Close"))
        self.insert_directory_button.setEnabled(False)
        self.insert_filename_button.setEnabled(False)
        self.raw_button.setEnabled(bool(raw_output.strip()))
        buttons_layout.addWidget(self.insert_directory_button)
        buttons_layout.addWidget(self.insert_filename_button)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self.raw_button)
        buttons_layout.addWidget(self.close_button)
        layout.addLayout(buttons_layout)

        self.search_edit.textChanged.connect(self._filter_tree)
        self.tree.currentItemChanged.connect(lambda _current, _previous: self._sync_selection())
        self.insert_directory_button.clicked.connect(self._insert_into_directory)
        self.insert_filename_button.clicked.connect(self._insert_into_filename)
        self.raw_button.clicked.connect(self._show_raw_output)
        self.close_button.clicked.connect(self.close)

        self._filter_tree("")
        self._sync_selection()

    def _populate_tree(self) -> None:
        self.tree.clear()
        group_order = get_group_order(self.language)
        grouped: dict[str, list[NamingKeywordEntry]] = {group: [] for group in group_order}
        for entry in self.entries:
            grouped.setdefault(entry.group, []).append(entry)

        for group_name in group_order:
            group_entries = grouped.get(group_name, [])
            if not group_entries:
                continue
            group_item = QTreeWidgetItem([group_name])
            group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            group_item.setFirstColumnSpanned(True)
            self.tree.addTopLevelItem(group_item)

            for entry in group_entries:
                item = QTreeWidgetItem([entry.name, entry.sample, entry.description, entry.usage])
                item.setData(0, Qt.ItemDataRole.UserRole, entry)
                item.setToolTip(0, entry.template)
                item.setToolTip(1, entry.sample)
                item.setToolTip(2, entry.description)
                item.setToolTip(3, entry.usage)
                group_item.addChild(item)

            group_item.setExpanded(group_name in set(group_order[:2]))

    def _filter_tree(self, text: str) -> None:
        query = text.strip().lower()
        first_visible_item: QTreeWidgetItem | None = None

        for index in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(index)
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

        current = self.tree.currentItem()
        if current is not None and current.isHidden():
            self.tree.setCurrentItem(None)

        if first_visible_item is not None and self.tree.currentItem() is None:
            self.tree.setCurrentItem(first_visible_item)

    def _current_entry(self) -> NamingKeywordEntry | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        return entry if isinstance(entry, NamingKeywordEntry) else None

    def _sync_selection(self) -> None:
        entry = self._current_entry()
        has_entry = entry is not None
        self.insert_directory_button.setEnabled(has_entry)
        self.insert_filename_button.setEnabled(has_entry)
        if not has_entry:
            self.token_value.setText(txt(self.language, "Выбери поле в списке.", "Select a field from the list."))
            self.description_value.setText("-")
            return
        self.token_value.setText(entry.template)
        self.description_value.setText(
            f"{entry.description}\n\n{txt(self.language, 'Пример', 'Example')}: {entry.sample}\n"
            f"{txt(self.language, 'Где использовать', 'Usage')}: {entry.usage}"
        )

    def _insert_into_directory(self) -> None:
        entry = self._current_entry()
        if entry is not None:
            self.on_insert_directory(entry)

    def _insert_into_filename(self) -> None:
        entry = self._current_entry()
        if entry is not None:
            self.on_insert_filename(entry)

    def _show_raw_output(self) -> None:
        dialog = ReadOnlyTextDialog(
            self.language,
            txt(self.language, "Сырой вывод gallery-dl", "Raw gallery-dl output"),
            self.raw_output,
            self,
        )
        dialog.exec()
