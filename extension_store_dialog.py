"""In-app dialog for searching the Chrome Web Store and installing
extensions directly, without using the CWS web UI (which rejects
non-Chrome user-agents with "Item currently unavailable") and
without a Google login.

The dialog never loads ``chromewebstore.google.com/detail/<id>`` —
that page is gated and would just show the error. Instead it relies
on the search-results HTML (which is server-rendered and works from
any browser) and the CWS CRX download API (which serves public
binaries with just a Chrome User-Agent).

If a user really wants the full detail page in a tab, they can
click "View on Web" — and accept the "Item currently unavailable"
error. The install path itself does NOT depend on the web UI.

Why we use ``urllib`` (not ``QNetworkAccessManager``) for the search
----------------------------------------------------------------
When QNAM is used to fetch ``chromewebstore.google.com/search/...``,
Google serves the ``consent.google.com`` cookie-consent page instead
of the search results. The page parses as 0 cards, and the user sees
"No extensions found" for every query. ``urllib`` is not affected
(the same code path works in ``extension_store.py`` via
``ChromeWebStore.search``). So we run the search on a worker
``QThread`` that uses ``urllib`` under the hood, keeping the GUI
responsive without resorting to QNAM.

The threading pattern is in :meth:`ExtensionStoreDialog._start_search_thread`
and is documented at length there. Do not refactor it lightly — the
deleteLater / event-loop interaction is subtle and was the source
of a real bug during development.
"""

import re

from PySide6.QtCore import Qt, QObject, QSettings, Signal, QThread, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QProgressBar, QMessageBox,
    QDialogButtonBox,
)

from extension_store import (
    ExtensionInfo, ExtensionStore, ChromeWebStore, SearchResultParser,
    CRX_DETAIL_URL,
)

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


class _SearchWorker(QObject):
    """Run ``ExtensionStore.search`` on a worker QThread.

    The QObject is moved onto a QThread, so its ``finished_ok`` and
    ``finished_err`` signals are queued back to the GUI thread.
    Following the same pattern as :class:`extension_store._InstallWorker`
    avoids the "QThread destroyed while still running" race that
    happens when a QThread subclass is destroyed from the GUI thread
    while its ``run()`` is still in progress.
    """

    finished_ok = Signal(list)
    finished_err = Signal(str)

    def __init__(self, store: ExtensionStore, query: str, limit: int = 20):
        super().__init__()
        self._store = store
        self._query = query
        self._limit = limit

    def run(self):
        try:
            results = self._store.search(self._query, limit=self._limit)
        except Exception as e:
            self.finished_err.emit(f"{type(e).__name__}: {e}")
            return
        self.finished_ok.emit(results)


class ExtensionStoreDialog(QDialog):
    """Modal dialog with a search bar, result list, and install button.

    The detail pane is populated from the search result metadata
    only — the CWS detail page is never loaded, so the dialog works
    even when Google blocks the browser from the CWS web UI.
    """

    installed = Signal(str)
    open_in_browser = Signal(str)

    SEARCH_DEBOUNCE_MS = 350
    SEARCH_RESULT_LIMIT = 20
    SETTINGS_ORG = "lossless"
    SETTINGS_APP = "extension_store_dialog"

    def __init__(self, store: ExtensionStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._results: list = []
        self._search_thread: QThread | None = None
        self._search_worker: _SearchWorker | None = None
        self._current_info: ExtensionInfo | None = None

        self.setWindowTitle("Extension Store")
        self.setModal(True)
        self._build_ui()
        self._restore_state()
        self._wire()

    # ── UI ───────────────────────────────────────────────────────────
    def _theme(self):
        win = self.window()
        if hasattr(win, "theme"):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)
        self._build_header(outer)
        self._build_search_row(outer)
        self._build_body(outer)
        self._build_status_bar(outer)
        self._build_button_box(outer)
        self._apply_theme()

    def _build_header(self, outer: QVBoxLayout) -> None:
        t = self._theme()
        c = t.colors
        header = QLabel("<b style='font-size:15px'>Extension Store</b>")
        header.setStyleSheet(f"color: {c.text};")
        outer.addWidget(header)
        subtitle = QLabel(
            "Search the Chrome Web Store and install extensions directly \u2014 "
            "no Google account required. The CWS web UI is bypassed; the "
            "browser talks to the CRX download API directly."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {c.text_secondary}; font-size: 11px;")
        outer.addWidget(subtitle)

    def _build_search_row(self, outer: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search extensions\u2026")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumHeight(32)
        row.addWidget(self.search_input, 1)
        outer.addLayout(row)

    def _build_body(self, outer: QVBoxLayout) -> None:
        t = self._theme()
        c = t.colors

        body = QHBoxLayout()
        body.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(6)
        self.results_list = QListWidget()
        self.results_list.setMinimumWidth(260)
        self.results_list.setUniformItemSizes(True)
        left.addWidget(self.results_list, 1)

        self.empty_label = QLabel("")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet(
            f"color: {c.text_secondary}; font-size: 12px; padding: 24px;"
        )
        self.empty_label.setVisible(False)
        left.addWidget(self.empty_label)

        right = QVBoxLayout()
        right.setSpacing(8)

        self.detail_name = QLabel("Select an extension")
        self.detail_name.setStyleSheet(
            f"color: {c.text}; font-size: 14px; font-weight: 600;"
        )
        self.detail_name.setWordWrap(True)
        right.addWidget(self.detail_name)

        self.detail_meta = QLabel("")
        self.detail_meta.setStyleSheet(f"color: {c.text_secondary}; font-size: 11px;")
        self.detail_meta.setWordWrap(True)
        right.addWidget(self.detail_meta)

        self.detail_desc = QLabel(
            "Pick something from the list on the left to see what it does."
        )
        self.detail_desc.setWordWrap(True)
        self.detail_desc.setStyleSheet(f"color: {c.text}; font-size: 12px;")
        self.detail_desc.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        right.addWidget(self.detail_desc, 1)

        button_row = QHBoxLayout()
        self.open_web_btn = QPushButton("View on Web")
        self.open_web_btn.setEnabled(False)
        self.open_web_btn.setToolTip(
            "The Chrome Web Store detail page is not available in this browser."
        )
        self.open_web_btn.clicked.connect(self._on_open_web)
        button_row.addWidget(self.open_web_btn)

        self.install_btn = QPushButton("Install")
        self.install_btn.setEnabled(False)
        self.install_btn.setMinimumHeight(34)
        self.install_btn.setDefault(True)
        button_row.addWidget(self.install_btn, 1)
        right.addLayout(button_row)

        body.addLayout(left, 1)
        body.addLayout(right, 2)
        outer.addLayout(body, 1)

    def _build_status_bar(self, outer: QVBoxLayout) -> None:
        t = self._theme()
        c = t.colors
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress.setTextVisible(True)
        outer.addWidget(self.progress)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            f"color: {c.text_secondary}; font-size: 11px;"
        )
        outer.addWidget(self.status_label)

    def _set_searching(self, on: bool, query: str = "") -> None:
        if on:
            self.progress.setRange(0, 0)  # indeterminate
            self.progress.setVisible(True)
            self.status_label.setText(f"Searching CWS for \u201c{query}\u201d\u2026")
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setVisible(False)

    def _build_button_box(self, outer: QVBoxLayout) -> None:
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        outer.addWidget(buttons)

    def _apply_theme(self):
        t = self._theme()
        c = t.colors
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {c.bg};
                color: {c.text};
                font-family: {t.font_family};
            }}
            QLineEdit {{
                background-color: {c.input_bg};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {t.radius_sm}px;
                padding: 6px 10px;
                selection-background-color: {c.accent_muted};
            }}
            QLineEdit:focus {{
                border-color: {c.border_focus};
            }}
            QListWidget {{
                background-color: {c.surface};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {t.radius_md}px;
                padding: 4px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 8px 10px;
                border-radius: {t.radius_sm}px;
            }}
            QListWidget::item:selected {{
                background-color: {c.surface_hover};
                color: {c.text};
            }}
            QListWidget::item:hover {{
                background-color: {c.surface_hover};
            }}
            QPushButton {{
                background-color: {c.surface};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {t.radius_sm}px;
                padding: 7px 16px;
                min-width: 80px;
            }}
            QPushButton:hover {{
                background-color: {c.surface_hover};
                border-color: {c.border_hover};
            }}
            QPushButton:pressed {{
                background-color: {c.surface_pressed};
            }}
            QPushButton:disabled {{
                color: {c.text_muted};
                background-color: {c.surface};
            }}
            QProgressBar {{
                background-color: {c.progress_bg};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {t.radius_sm}px;
                text-align: center;
                height: 14px;
            }}
            QProgressBar::chunk {{
                background-color: {c.accent};
                border-radius: {t.radius_sm}px;
            }}
        """)

    def _wire(self):
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._run_search)
        self.results_list.currentRowChanged.connect(self._on_row_changed)
        self.results_list.itemActivated.connect(self._on_item_activated)
        self.install_btn.clicked.connect(self._on_install_clicked)
        self.store.download_progress.connect(self._on_progress)
        self.store.install_complete.connect(self._on_install_complete)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(self.SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._run_search)

    # ── Search ───────────────────────────────────────────────────────
    def _on_search_changed(self, text: str):
        if len(text.strip()) < 2:
            return
        self._search_timer.start()

    def _start_search_thread(self, query: str) -> None:
        """Spin up a QThread that runs ``_SearchWorker.run`` and tears
        itself down on completion.

        Threading pattern:

        * ``_SearchWorker`` is a plain :class:`QObject`, not a
          :class:`QThread` subclass. We ``moveToThread`` it onto a
          bare ``QThread``. The thread's default ``run()`` would
          just call ``exec()`` — but we never ``exec()`` the thread,
          so it just runs the worker's ``run()`` and exits naturally.
        * The worker emits ``finished_ok`` / ``finished_err`` which
          are connected with ``QueuedConnection`` so the slots run on
          the main (dialog) thread, which is the only thread that
          may touch widgets.
        * The thread's ``finished`` signal triggers ``deleteLater``
          for both the worker and the thread itself.  The events
          are posted to the main thread's queue and processed
          during the next event loop iteration.
        """
        # Don't overwrite a still-running search thread; let it
        # finish and clean up via its own deleteLater pipeline.
        old_thread = self._search_thread
        if old_thread is not None and old_thread.isRunning():
            return

        self._search_thread = QThread()
        self._search_worker = _SearchWorker(
            self.store, query, limit=self.SEARCH_RESULT_LIMIT
        )
        self._search_worker.moveToThread(self._search_thread)
        self._search_thread.started.connect(self._search_worker.run)
        self._search_worker.finished_ok.connect(
            self._on_search_ok, Qt.ConnectionType.QueuedConnection,
        )
        self._search_worker.finished_err.connect(
            self._on_search_err, Qt.ConnectionType.QueuedConnection,
        )
        self._search_thread.finished.connect(self._search_worker.deleteLater)
        self._search_thread.finished.connect(self._search_thread.deleteLater)
        self._search_thread.finished.connect(self._on_search_thread_done)
        self._search_thread.start()

    def _on_search_thread_done(self):
        # Clear the references only after the thread has actually
        # finished and the queued deleteLater events have been
        # processed. Setting them to None mid-flight caused a
        # "QThread destroyed while still running" race.
        if self.sender() is self._search_thread:
            self._search_thread = None
            self._search_worker = None

    def _run_search(self):
        query = self.search_input.text().strip()
        if not query:
            return
        self._cancel_pending_search()
        self._set_searching(True, query)
        self.empty_label.setVisible(False)
        self.results_list.clear()
        self._results.clear()
        self._set_detail_placeholder()
        self._start_search_thread(query)

    def _on_search_ok(self, results: list):
        self._set_searching(False)
        if not results:
            query = self.search_input.text().strip()
            self.status_label.setText("No extensions found.")
            self.empty_label.setText(
                f"No extensions matched \u201c{query}\u201d.\n\n"
                "Try a different search term \u2014 e.g. just the name, "
                "or the publisher."
            )
            self.empty_label.setVisible(True)
            return
        self.empty_label.setVisible(False)
        for ext_id, name, rating, description in results:
            info = ExtensionInfo(
                extension_id=ext_id,
                name=name,
                rating=rating,
                description=description or "",
                store_url=CRX_DETAIL_URL.format(ext_id=ext_id),
            )
            self._results.append(info)
            label = f"{name}  \u2605 {rating:.1f}" if rating > 0 else name
            self.results_list.addItem(QListWidgetItem(label))
        self.status_label.setText(f"{len(self._results)} result(s).")
        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)

    def _on_search_err(self, message: str):
        self._set_searching(False)
        self.empty_label.setText(f"Search failed:\n{message}")
        self.empty_label.setVisible(True)
        self.status_label.setText(f"Search failed: {message}")

    def _cancel_pending_search(self):
        """Join the in-flight search thread, if any, with a 2-second
        timeout. We deliberately do NOT call ``thread.quit()`` here:
        the worker does not run an event loop, so ``quit()`` would be
        a no-op. The thread will exit on its own as soon as the
        worker's ``run()`` returns; we just wait for it.
        """
        thread = self._search_thread
        if thread is not None and thread.isRunning():
            thread.wait(2000)
        # Don't clear ``_search_thread`` / ``_search_worker`` here —
        # the next event-loop tick will fire ``finished`` and call
        # ``deleteLater`` on both.  Clearing them now would race with
        # that deletion and trip the "QThread destroyed while still
        # running" diagnostic.

    # ── Detail ───────────────────────────────────────────────────────
    def _on_row_changed(self, row: int):
        if row < 0 or row >= len(self._results):
            self._set_detail_placeholder()
            return
        info = self._results[row]
        self._current_info = info
        self.detail_name.setText(info.name or info.extension_id)
        meta_parts = []
        if info.author:
            meta_parts.append(f"by {info.author}")
        if info.rating > 0:
            meta_parts.append(f"\u2605 {info.rating:.1f}")
        self.detail_meta.setText("  \u00b7  ".join(meta_parts))
        self.detail_desc.setText(
            info.description
            or "Click \u2018View on Web\u2019 to read the full description on the "
               "Chrome Web Store, or \u2018Install\u2019 to download the CRX directly."
        )
        self.install_btn.setEnabled(True)
        self.install_btn.setText("Install")
        self.open_web_btn.setEnabled(bool(info.store_url))

    def _on_item_activated(self, item):
        if self.install_btn.isEnabled():
            self._on_install_clicked()

    def _set_detail_placeholder(self):
        self._current_info = None
        self.detail_name.setText("Select an extension")
        self.detail_meta.setText("")
        self.detail_desc.setText(
            "Pick something from the list on the left to see what it does."
        )
        self.install_btn.setEnabled(False)
        self.install_btn.setText("Install")
        self.open_web_btn.setEnabled(False)

    # ── Persistence ─────────────────────────────────────────────────
    def _settings(self) -> QSettings:
        return QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)

    def _restore_state(self) -> None:
        s = self._settings()
        size = s.value("geometry/size")
        if size is not None:
            self.resize(size)
        else:
            self.resize(640, 480)
        last = s.value("search/last_query", "", type=str) or ""
        if last:
            self.search_input.setText(last)

    def _save_state(self) -> None:
        s = self._settings()
        s.setValue("geometry/size", self.size())
        s.setValue("search/last_query", self.search_input.text())
        s.sync()

    # ── Install / View on Web ────────────────────────────────────────
    def _on_install_clicked(self):
        if not self._current_info:
            return
        ext_id = self._current_info.extension_id
        self.install_btn.setEnabled(False)
        self.install_btn.setText("Installing\u2026")
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status_label.setText(f"Installing {self._current_info.name}\u2026")
        self.store.install_extension(ext_id)

    def _on_open_web(self):
        if not self._current_info or not self._current_info.store_url:
            return
        self.open_in_browser.emit(self._current_info.store_url)

    def _on_progress(self, message: str, percent: int):
        self.progress.setVisible(True)
        self.progress.setValue(percent)
        self.status_label.setText(message)

    def _on_install_complete(self, extension_id: str, ok: bool, message: str):
        self.progress.setValue(100 if ok else self.progress.value())
        self.status_label.setText(message)
        if ok:
            if self._current_info and self._current_info.extension_id == extension_id:
                self.install_btn.setText("Installed")
            self.installed.emit(extension_id)
            QTimer.singleShot(1500, lambda: self.progress.setVisible(False))
        else:
            self.install_btn.setEnabled(True)
            self.install_btn.setText("Install")
            QMessageBox.warning(self, "Install failed", message)
            self.progress.setVisible(False)

    # ── Cleanup ──────────────────────────────────────────────────────
    def closeEvent(self, event):
        self._save_state()
        self._cancel_pending_search()
        self.store.cancel_all()
        super().closeEvent(event)
