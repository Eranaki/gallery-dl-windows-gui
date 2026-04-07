from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.services.settings_service import SettingsService
from app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("gallery-dl GUI")
    app.setOrganizationName("Local")

    settings = SettingsService()
    window = MainWindow(settings)
    window.show()

    return app.exec()
