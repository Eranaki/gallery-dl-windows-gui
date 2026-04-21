from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHeaderView,
)

from app.ui.i18n import txt


class HistoryDialog(QDialog):
    clear_requested = Signal()

    def __init__(self, language: str, parent=None) -> None:
        super().__init__(parent)
        self.language = language
        self.table = QTableWidget(0, 5)
        self.clear_button = QPushButton()
        self.close_button = QPushButton()

        self.resize(1040, 560)
        self._build_ui()
        self.retranslate(language)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._configure_table(self.table)
        layout.addWidget(self.table, 1)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.clear_button)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self.close_button)
        layout.addLayout(buttons_layout)

        self.clear_button.clicked.connect(self.clear_requested.emit)
        self.close_button.clicked.connect(self.close)

    def retranslate(self, language: str) -> None:
        self.language = language
        self.setWindowTitle(txt(language, "История", "History"))
        self.clear_button.setText(txt(language, "Очистить", "Clear"))
        self.close_button.setText(txt(language, "Закрыть", "Close"))
        self.table.setHorizontalHeaderLabels(
            [
                "URL",
                txt(language, "Сайт", "Site"),
                txt(language, "Режим", "Mode"),
                txt(language, "Результат", "Result"),
                txt(language, "Комментарий", "Comment"),
            ]
        )

    def set_rows(self, rows: list[list[str]]) -> None:
        self.table.setRowCount(0)
        for row_index, values in enumerate(rows):
            self.table.insertRow(row_index)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                self.table.setItem(row_index, column, item)

    def clear_rows(self) -> None:
        self.table.setRowCount(0)

    def _configure_table(self, table: QTableWidget) -> None:
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
