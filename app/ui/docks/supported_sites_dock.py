from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.supported_sites import SupportedSiteEntry, SupportedSitesPayload
from app.services.supported_sites_service import DEFAULT_SECTION
from app.ui.i18n import txt


SUPPORTED_SITES_DOCK_WIDTH = 440


class SupportedSitesDock(QDockWidget):
    refresh_requested = Signal()

    def __init__(self, language: str, parent=None) -> None:
        super().__init__(parent)
        self.language = language
        self.payload: SupportedSitesPayload | None = None

        self.source_label = QLabel()
        self.search_edit = QLineEdit()
        self.refresh_button = QPushButton()
        self.tree = QTreeWidget()
        self.site_name_value = QLabel()
        self.site_url_value = QLabel()
        self.site_capabilities_value = QLabel()
        self.site_auth_value = QLabel()
        self.site_section_value = QLabel()
        self.updated_label = QLabel()
        self.status_label = QLabel()

        self._build_ui()
        self.retranslate(language)

    def _build_ui(self) -> None:
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.setMinimumWidth(SUPPORTED_SITES_DOCK_WIDTH)
        self.setMaximumWidth(SUPPORTED_SITES_DOCK_WIDTH)

        container = QWidget()
        layout = QVBoxLayout(container)

        self.source_label.setOpenExternalLinks(True)
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self.search_edit, 1)
        controls_layout.addWidget(self.refresh_button)
        layout.addLayout(controls_layout)

        self.tree.setHeaderHidden(True)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree, 1)

        details_group = QGroupBox()
        details_form = QFormLayout(details_group)
        self.site_name_label = QLabel()
        self.site_url_label = QLabel("URL:")
        self.site_capabilities_label = QLabel()
        self.site_auth_label = QLabel()
        self.site_section_label = QLabel()
        self.site_name_value.setWordWrap(True)
        self.site_url_value.setOpenExternalLinks(True)
        self.site_url_value.setWordWrap(True)
        self.site_capabilities_value.setWordWrap(True)
        self.site_auth_value.setWordWrap(True)
        self.site_section_value.setWordWrap(True)
        details_form.addRow(self.site_name_label, self.site_name_value)
        details_form.addRow(self.site_url_label, self.site_url_value)
        details_form.addRow(self.site_capabilities_label, self.site_capabilities_value)
        details_form.addRow(self.site_auth_label, self.site_auth_value)
        details_form.addRow(self.site_section_label, self.site_section_value)
        self.details_group = details_group
        layout.addWidget(details_group)

        footer_layout = QVBoxLayout()
        self.status_label.setWordWrap(True)
        footer_layout.addWidget(self.updated_label)
        footer_layout.addWidget(self.status_label)
        layout.addLayout(footer_layout)

        self.setWidget(container)

        self.search_edit.textChanged.connect(self._filter_sites)
        self.tree.currentItemChanged.connect(self._on_site_selected)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)

    def retranslate(self, language: str) -> None:
        self.language = language
        self.setWindowTitle(txt(language, "Поддерживаемые сайты", "Supported sites"))
        self.source_label.setText(
            '<a href="https://github.com/mikf/gallery-dl/blob/master/docs/supportedsites.md">'
            + txt(
                language,
                "Источник: supportedsites.md из gallery-dl на GitHub",
                "Source: gallery-dl supportedsites.md on GitHub",
            )
            + "</a>"
        )
        self.search_edit.setPlaceholderText(
            txt(
                language,
                "Поиск по сайту, URL или возможностям",
                "Search by site, URL, or capabilities",
            )
        )
        self.refresh_button.setText(txt(language, "Обновить", "Refresh"))
        self.details_group.setTitle(txt(language, "Подробности", "Details"))
        self.site_name_label.setText(txt(language, "Сайт:", "Site:"))
        self.site_capabilities_label.setText(txt(language, "Возможности:", "Capabilities:"))
        self.site_auth_label.setText(txt(language, "Авторизация:", "Authentication:"))
        self.site_section_label.setText(txt(language, "Секция:", "Section:"))
        self.clear_details()
        self.updated_label.setText(txt(language, "Последнее обновление: -", "Last update: -"))
        if not self.status_label.text():
            self.status_label.setText(
                txt(language, "Список сайтов еще не загружен.", "The site list has not been loaded yet.")
            )
        if self.payload is not None:
            self.set_payload(self.payload)

    def clear_details(self) -> None:
        self.site_name_value.setText(txt(self.language, "Выбери сайт из списка.", "Select a site from the list."))
        self.site_url_value.setText("-")
        self.site_capabilities_value.setText("-")
        self.site_auth_value.setText("-")
        self.site_section_value.setText("-")

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def set_refresh_active(self, active: bool) -> None:
        self.refresh_button.setEnabled(not active)

    def set_payload(self, payload: SupportedSitesPayload) -> None:
        self.payload = payload
        self.tree.clear()

        section_items: dict[str, QTreeWidgetItem] = {}
        first_site_item: QTreeWidgetItem | None = None

        for site in payload.sites:
            section_name = site.section or DEFAULT_SECTION
            if section_name not in section_items:
                section_label = self._section_label(section_name)
                section_item = QTreeWidgetItem([section_label])
                section_item.setFlags(section_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                section_item.setToolTip(0, section_label)
                self.tree.addTopLevelItem(section_item)
                section_items[section_name] = section_item

            item = QTreeWidgetItem([site.name])
            item.setToolTip(0, self._build_tooltip(site))
            item.setData(0, Qt.ItemDataRole.UserRole, site)
            section_items[section_name].addChild(item)
            if first_site_item is None:
                first_site_item = item

        if section_items:
            next(iter(section_items.values())).setExpanded(True)

        self.updated_label.setText(
            txt(self.language, "Последнее обновление: ", "Last update: ") + self._format_timestamp(payload.fetched_at)
        )
        self._filter_sites(self.search_edit.text())
        if first_site_item is not None and self.tree.currentItem() is None:
            self.tree.setCurrentItem(first_site_item)

    def _filter_sites(self, text: str) -> None:
        query = text.strip().lower()
        first_visible_item: QTreeWidgetItem | None = None

        for index in range(self.tree.topLevelItemCount()):
            section_item = self.tree.topLevelItem(index)
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

        current = self.tree.currentItem()
        if current is not None and current.isHidden():
            self.tree.setCurrentItem(None)

        if first_visible_item is not None and self.tree.currentItem() is None:
            self.tree.setCurrentItem(first_visible_item)
        elif first_visible_item is None:
            self.clear_details()

    def _on_site_selected(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            self.clear_details()
            return

        entry = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(entry, SupportedSiteEntry):
            self.clear_details()
            return

        self.site_name_value.setText(entry.name or "-")
        self.site_url_value.setText(f'<a href="{entry.url}">{entry.url}</a>' if entry.url else "-")
        self.site_capabilities_value.setText(entry.capabilities or "-")
        self.site_auth_value.setText(entry.auth or "-")
        self.site_section_value.setText(self._section_label(entry.section))

    def _section_label(self, section: str) -> str:
        normalized = (section or DEFAULT_SECTION).strip()
        if normalized == DEFAULT_SECTION:
            return txt(self.language, "Основные сайты", "Main sites")
        return normalized

    def _build_tooltip(self, entry: SupportedSiteEntry) -> str:
        lines = [
            f"{txt(self.language, 'Сайт', 'Site')}: {entry.name or '-'}",
            f"URL: {entry.url or '-'}",
            f"{txt(self.language, 'Возможности', 'Capabilities')}: {entry.capabilities or '-'}",
            f"{txt(self.language, 'Авторизация', 'Authentication')}: {entry.auth or '-'}",
        ]
        section_label = self._section_label(entry.section)
        if section_label != txt(self.language, "Основные сайты", "Main sites"):
            lines.append(f"{txt(self.language, 'Секция', 'Section')}: {section_label}")
        return "\n".join(lines)

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
