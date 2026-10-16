"""Workers for the Spotify / Apple Music tabs.

* :class:`ExternalLibraryWorker` lists the user's collections and emits
  ready-to-render :class:`CardVM` instances.
* :class:`ExternalConnectWorker` runs the blocking browser sign-in off the
  GUI thread.

Both use the QObject + moveToThread pattern like the other workers.
"""
import logging
import threading

from PySide6.QtCore import QObject, Signal

from app.integrations.models import PROVIDER_LABEL, ExternalCollection
from app.integrations.registry import client_for
from app.models.card_vm import CardVM

log = logging.getLogger(__name__)


def collection_to_card(coll: ExternalCollection) -> CardVM:
    label = PROVIDER_LABEL[coll.provider]
    parts = [p for p in (coll.creator,) if p]
    if coll.track_count:
        parts.append(f"{coll.track_count} tracks")
    parts.append(label)
    return CardVM(
        kind="album" if coll.kind == "album" else "playlist",
        title=coll.title,
        subtitle=" · ".join(parts),
        url=coll.url,
        cover_url=coll.cover_url,
        is_downloaded=False,
        source=coll.source,
        ident=coll.url,
    )


class ExternalLibraryWorker(QObject):
    card_ready = Signal(object)
    finished = Signal()
    error = Signal(str)

    def __init__(self, provider: str) -> None:
        super().__init__()
        self.provider = provider
        self._interrupted = threading.Event()

    def interrupt(self) -> None:
        self._interrupted.set()

    def run(self) -> None:
        try:
            for coll in client_for(self.provider).iter_library():
                if self._interrupted.is_set():
                    break
                self.card_ready.emit(collection_to_card(coll))
        except Exception as exc:
            log.warning("%s library load failed: %s", self.provider, exc)
            self.error.emit(str(exc))
        self.finished.emit()


class ExternalConnectWorker(QObject):
    connected = Signal(str, str)   # provider, account name
    error = Signal(str, str)       # provider, message
    finished = Signal()

    def __init__(self, provider: str) -> None:
        super().__init__()
        self.provider = provider
        self._cancel = threading.Event()

    def interrupt(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            name = client_for(self.provider).login(cancel=self._cancel)
            self.connected.emit(self.provider, name)
        except Exception as exc:
            log.warning("%s sign-in failed: %s", self.provider, exc)
            self.error.emit(self.provider, str(exc))
        self.finished.emit()
