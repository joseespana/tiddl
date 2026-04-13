"""
Thin wrapper — wires MainView + MainPresenter together.

``app/main.py`` still imports ``MainWindow`` so this shim keeps the
public interface stable.
"""
from app.views.main_view import MainView
from app.presenters.main_presenter import MainPresenter


class MainWindow(MainView):
    """Application entry-point window.

    Inherits all UI from :class:`~app.views.main_view.MainView` and
    creates a :class:`~app.presenters.main_presenter.MainPresenter`
    that owns all business logic.
    """

    def __init__(self) -> None:
        super().__init__()
        self._presenter = MainPresenter(self)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._presenter.on_close()
        super().closeEvent(event)
