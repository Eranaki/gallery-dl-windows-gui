from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.services.settings_service import SettingsService
from app.ui.main_window import MainWindow


def run_gallery_dl_proxy(argv: list[str]) -> int:
    from gallery_dl import main as gallery_dl_main

    original_argv = sys.argv[:]
    try:
        sys.argv = [sys.argv[0], *argv]
        return int(gallery_dl_main() or 0)
    finally:
        sys.argv = original_argv


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--internal-gallery-dl":
        return run_gallery_dl_proxy(sys.argv[2:])

    app = QApplication(sys.argv)
    app.setApplicationName("gallery-dl GUI")
    app.setOrganizationName("Local")

    settings = SettingsService()
    window = MainWindow(settings)
    window.show()

    return app.exec()
