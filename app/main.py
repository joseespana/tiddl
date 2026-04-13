"""
tiddl GUI — entry point.

Run with:
    python -m app.main
or:
    python app/main.py
"""
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("tiddl")

    from app.api_client import is_authenticated
    from app.auth_window import AuthWindow
    from app.main_window import MainWindow

    if not is_authenticated():
        auth_dlg = AuthWindow()
        if auth_dlg.exec() != AuthWindow.DialogCode.Accepted:
            sys.exit(0)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
