"""Topbar extensions popup — the puzzle-piece menu.

Lives next to the address bar.  When the user clicks the puzzle
icon, a small floating panel appears listing the currently
installed extensions with:

  * name + 32-char id (truncated)
  * enable / disable toggle
  * "Options" button (opens the extension's ``options_page`` or
    ``options_ui.page`` in a new tab — Chrome convention)
  * "Remove" button (calls :meth:`ExtensionLoader.uninstall`)
  * "Manage extensions…" link that opens the full dialog

The popup is themed, shadow-dropped, click-outside to close, and
**static** — there is no drag-to-dock and no pinning.  Opening and
closing is the only state change a user can trigger.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt, QObject, QEvent, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
    QSizePolicy, QGraphicsDropShadowEffect, QScrollArea,
)

# Imported lazily inside event handlers — the popup itself does
# not need the debug logger at import time, but every show/hide
# transition is logged so a future crash leaves a trail.
try:
    from debug_log import log as _dl_log
except Exception:  # pragma: no cover - debug_log is always present
    def _dl_log(channel, msg, **fields):
        return None


@dataclass
class ExtensionPopupItem:
    """Lightweight view-model for one row in the popup."""
    extension_id: str
    name: str = ""
    is_enabled: bool = True
    is_loaded: bool = True
    shortcut: str = ""  # e.g. "Ctrl+Shift+Y"; empty when none
    options_url: str = ""  # e.g. "chrome-extension://<id>/dashboard.html"; "" if no options page


def read_extension_shortcut(extension_path: str) -> str:
    """Read the first ``commands`` entry from a manifest.

    Chrome's convention is to surface one global shortcut per
    extension in the chrome://extensions popup (the one with
    ``"global": true`` or, failing that, the first declared one).
    Returns a human-readable string like ``"Ctrl+Shift+Y"`` or
    an empty string if the extension declares no commands.

    The function is intentionally forgiving: malformed JSON, an
    unreadable manifest, or a missing ``commands`` key all yield
    an empty string rather than an exception — the popup is a
    cosmetic surface and must not break the topbar flow.
    """
    if not extension_path:
        return ""
    try:
        import json
        import os
        manifest_path = os.path.join(extension_path, "manifest.json")
        if not os.path.isfile(manifest_path):
            return ""
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        return ""
    commands = manifest.get("commands")
    if not isinstance(commands, dict) or not commands:
        return ""
    # Prefer a global shortcut if any.
    chosen = None
    for _name, spec in commands.items():
        if isinstance(spec, dict) and spec.get("global") is True:
            chosen = spec
            break
    if chosen is None:
        # Fall back to the first command that has a "suggested_key".
        for _name, spec in commands.items():
            if isinstance(spec, dict) and "suggested_key" in spec:
                chosen = spec
                break
    if chosen is None:
        return ""
    suggested = chosen.get("suggested_key", {})
    if not isinstance(suggested, dict):
        return ""
    # Chrome's "default" maps to the platform-appropriate shortcut;
    # "windows"/"mac"/"linux"/"chromeos" override per platform.
    import sys
    platform_key = {
        "win32": "windows",
        "darwin": "mac",
    }.get(sys.platform, "linux")
    keymap = (
        suggested.get(platform_key)
        or suggested.get("default")
        or suggested.get("linux")
        or suggested.get("mac")
        or suggested.get("windows")
        or ""
    )
    if not isinstance(keymap, str) or not keymap:
        return ""
    # Normalise separator and case: Chrome displays "Ctrl+Shift+Y".
    parts = [p for p in keymap.split("+") if p.strip()]
    canon = {
        "ctrl": "Ctrl", "control": "Ctrl",
        "shift": "Shift", "alt": "Alt", "option": "Alt",
        "meta": "Win", "cmd": "Meta", "command": "Meta",
        "search": "Search", "super": "Win",
    }
    pretty = []
    for p in parts:
        if not p:
            continue
        k = p.strip().lower()
        if k in canon:
            pretty.append(canon[k])
        elif len(p) == 1:
            pretty.append(p.upper())
        else:
            pretty.append(p[0].upper() + p[1:].lower())
    return "+".join(pretty)


def read_extension_options_page(extension_path: str) -> str:
    """Read the extension's options page path from its manifest.

    Returns a path string (e.g. ``"dashboard.html"``) suitable for
    appending to a ``chrome-extension://<id>/`` URL, or an empty
    string if the extension has no options page.

    Recognises both MV3 ``options_ui.page`` and MV2 ``options_page``.
    The function is intentionally forgiving: any failure yields an
    empty string.
    """
    if not extension_path:
        return ""
    try:
        import json
        import os
        manifest_path = os.path.join(extension_path, "manifest.json")
        if not os.path.isfile(manifest_path):
            return ""
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        return ""
    # MV3: options_ui.page
    options_ui = manifest.get("options_ui")
    if isinstance(options_ui, dict):
        page = options_ui.get("page")
        if isinstance(page, str) and page:
            return page
    # MV2: options_page
    options_page = manifest.get("options_page")
    if isinstance(options_page, str) and options_page:
        return options_page
    return ""


def build_options_url(extension_id: str, options_path: str) -> str:
    """Build a ``chrome-extension://<id>/<path>`` URL from an id and
    a manifest-relative options path.  Returns ``""`` if either input
    is empty.  Defends against absolute paths leaking into the URL
    by stripping leading slashes and any scheme prefix.
    """
    if not extension_id or not options_path:
        return ""
    # Strip any scheme prefix the manifest might have leaked in
    # (e.g. "https://example.com/page.html" — possible, but we
    # still want to route it through chrome-extension://).
    p = options_path.strip()
    if "://" in p:
        # absolute URL — we can't safely route it through the
        # extension scheme, return empty so the caller skips it.
        return ""
    p = p.lstrip("/")
    if not p:
        return ""
    return f"chrome-extension://{extension_id}/{p}"


class ExtensionsPopupButton(QPushButton):
    """Puzzle-piece button with an enabled-extension count badge.

    Renders a 28x28 puzzle glyph + a small red dot in the top-right
    corner with the number of currently enabled extensions (or a
    plain "•" when the count is between 1 and 9, "9+" past that).
    Hidden when zero.
    """

    BTN_SIZE = 28

    def __init__(self, parent=None):
        super().__init__(parent)
        self._count = 0
        self.setFixedSize(self.BTN_SIZE, self.BTN_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Extensions")
        self.setMouseTracking(True)

        # Hover animation (mirrors NavButton).
        self._hover_progress = 0.0
        from PySide6.QtCore import QVariantAnimation, QEasingCurve
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(150)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_progress)

    def _on_hover_progress(self, value):
        self._hover_progress = value
        self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._start_hover(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._start_hover(False)

    def _start_hover(self, entering):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(1.0 if entering else 0.0)
        self._hover_anim.start()

    def set_count(self, n: int) -> None:
        n = max(0, int(n))
        if n != self._count:
            self._count = n
            self.setToolTip(
                "Extensions" if n == 0
                else f"Extensions — {n} enabled"
            )
            self.update()

    def _theme(self):
        win = self.window()
        if win is not None and hasattr(win, 'theme'):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        hp = self._hover_progress
        is_pressed = self.isDown()

        t = self._theme()
        c = t.colors

        # Hover circle (matches NavButton visual)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        max_r = 12.0
        if is_pressed:
            circle_r = max_r
            bg = QColor(c.surface_pressed)
        else:
            circle_r = max_r * hp
            bg = QColor(c.surface_hover)
        if circle_r > 0.5:
            path = QPainterPath()
            path.addEllipse(QRectF(cx - circle_r, cy - circle_r, circle_r * 2, circle_r * 2))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawPath(path)

        # Icon color: lerp text → accent on hover.
        if is_pressed:
            icon_color = QColor(c.accent)
        else:
            base = QColor(c.text)
            accent = QColor(c.accent)
            r = base.red() + (accent.red() - base.red()) * hp
            g = base.green() + (accent.green() - base.green()) * hp
            b = base.blue() + (accent.blue() - base.blue()) * hp
            a = base.alpha() + (accent.alpha() - base.alpha()) * hp
            icon_color = QColor(int(r), int(g), int(b), int(a))

        pen = QPen(icon_color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        self._draw_puzzle(painter, cx, cy)

        # Badge in the top-right when count > 0.
        if self._count > 0:
            self._draw_badge(painter, t, c)

        painter.end()

    def _draw_puzzle(self, painter, cx, cy):
        """Draw a clean, recognisable puzzle-piece glyph.

        Geometry: a 9x9 square body (in pixels) centred on (cx, cy),
        with one knob on the right edge and one matching notch on
        the bottom edge.  Total bounding box ~ 11x11 px.
        """
        s = 4.5  # half-side of the body square
        k = 1.5  # knob radius (sticking out)
        # Start at the top-left and walk clockwise.
        path = QPainterPath()
        # Top edge: left to right, with a slight inset for the corner.
        path.moveTo(cx - s, cy - s)
        path.lineTo(cx + s, cy - s)
        # Right edge with a knob sticking out.
        path.lineTo(cx + s, cy - 0.5)
        path.arcTo(cx + s, cy - 0.5 - k, k * 2, k * 2, 90, -180)
        path.lineTo(cx + s, cy + s)
        # Bottom edge with a notch cut in.
        path.lineTo(cx + 0.5, cy + s)
        path.arcTo(cx + 0.5 - k, cy + s - k * 2, k * 2, k * 2, 0, 180)
        path.lineTo(cx - s, cy + s)
        # Left edge straight up.
        path.lineTo(cx - s, cy - s)
        path.closeSubpath()
        painter.drawPath(path)

    def _draw_badge(self, painter, theme, colors):
        # Position: top-right corner of the button.
        bw, bh = 14, 14
        bx = self.width() - bw - 1
        by = 1
        bg_rect = QRectF(bx, by, bw, bh)
        painter.setPen(Qt.PenStyle.NoPen)
        # Use accent (not danger) so the badge reads as a count, not
        # a warning.  Chrome uses a neutral grey, but our accent
        # colour is muted enough to serve the same purpose and stays
        # consistent with the rest of the toolbar.
        painter.setBrush(QColor(colors.accent))
        painter.drawEllipse(bg_rect)
        # Number / dot — pick a contrast colour that works on both
        # light and dark accent variants.  ``toolbar_bg`` is the
        # background of the chrome; if the accent is the same hue
        # family it has enough contrast.
        f = QFont()
        f.setPixelSize(8)
        f.setWeight(QFont.Weight.Bold)
        painter.setFont(f)
        painter.setPen(QColor(colors.toolbar_bg))
        text = str(self._count) if self._count < 10 else "9+"
        painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, text)


class ExtensionsPopup(QFrame):
    """Floating panel anchored under a button.

    Signals
    -------
    closeRequested()                  — user clicked outside or pressed Escape
    manageRequested()                 — user clicked "Manage extensions"
    removeRequested(str)              — user clicked the trash icon on a row
    toggleRequested(str, bool)        — user flipped the enable toggle
    optionsRequested(str, str)        — user clicked the "Options" button
                                        (extension_id, chrome-extension URL)

    The popup is purely static: it has no drag handle, no dock
    state, no pin/unpin button.  Showing and hiding are the only
    two visual transitions.
    """

    closeRequested = Signal()
    manageRequested = Signal()
    removeRequested = Signal(str)
    toggleRequested = Signal(str, bool)
    optionsRequested = Signal(str, str)

    PADDING = 10
    ROW_HEIGHT = 44
    MIN_WIDTH = 320
    MAX_HEIGHT = 360

    def __init__(self, parent=None):
        # Use ``Tool`` (not ``Popup``) so the window does NOT
        # auto-close on focus loss.  ``Popup`` windows in Qt
        # are designed for transient menus and get an
        # automatic ``closeEvent`` the moment they lose focus
        # to another window — which on the offscreen QPA plugin
        # (and on real Windows with our frameless title bar)
        # is the same tick that ``show()`` returns, so the
        # popup flashes open for ~5ms and disappears.  ``Tool``
        # is the right type for a floating palette that should
        # stay open until the user explicitly dismisses it.
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("ExtensionsPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumHeight(self.MAX_HEIGHT)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.PADDING, self.PADDING, self.PADDING, self.PADDING)
        outer.setSpacing(8)

        # Static title row — no drag handle, no grip, no close-dock
        # button.  The user has exactly two ways to dismiss the
        # popup: click outside, or press Escape.
        self._title = QLabel("Extensions")
        self._title.setObjectName("ExtensionsPopupTitle")
        outer.addWidget(self._title)

        # Scrollable list of rows
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidgetResizable(True)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._list_host = QWidget()
        self._list_host.setObjectName("ExtensionsPopupList")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_host)
        outer.addWidget(self._scroll, 1)

        # Footer link
        self._manage_btn = QPushButton("Manage extensions…")
        self._manage_btn.setObjectName("ExtensionsPopupManage")
        self._manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._manage_btn.clicked.connect(self.manageRequested)
        outer.addWidget(self._manage_btn)

        # Shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 100))
        self.setGraphicsEffect(shadow)

    # ── Public ──
    def set_extensions(self, items):
        """Replace the list contents.

        ``items`` is an iterable of ``ExtensionPopupItem``-like objects
        with attributes: ``extension_id``, ``name``, ``is_enabled``,
        ``is_loaded``, ``options_url``.
        """
        # Wipe existing rows (keep the trailing stretch)
        while self._list_layout.count() > 1:
            child = self._list_layout.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()

        if not items:
            empty = QLabel("No extensions installed.")
            empty.setObjectName("ExtensionsPopupEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list_layout.insertWidget(0, empty)
            return

        for item in items:
            row = _ExtensionRow(item, self)
            row.removeClicked.connect(self.removeRequested)
            row.toggleClicked.connect(self.toggleRequested)
            row.optionsClicked.connect(self.optionsRequested)
            self._list_layout.insertWidget(
                self._list_layout.count() - 1, row
            )

    def apply_theme(self, theme):
        c = theme.colors
        self.setStyleSheet(f"""
            QFrame#ExtensionsPopup {{
                background-color: {c.toolbar_bg};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: 10px;
            }}
            QLabel#ExtensionsPopupTitle {{
                color: {c.text};
                font-size: 13px;
                font-weight: 600;
                padding: 2px 4px;
                background: transparent;
            }}
            QLabel#ExtensionsPopupEmpty {{
                color: {c.text_tertiary};
                font-size: 12px;
                padding: 24px 8px;
                background: transparent;
            }}
            QWidget#ExtensionsPopupList {{
                background: transparent;
            }}
            QPushButton#ExtensionsPopupManage {{
                background-color: transparent;
                color: {c.accent};
                border: none;
                border-radius: 6px;
                padding: 6px 8px;
                text-align: left;
                font-size: 12px;
            }}
            QPushButton#ExtensionsPopupManage:hover {{
                background-color: {c.surface_hover};
            }}
            QWidget#ExtensionPopupRow {{
                background: transparent;
            }}
            QWidget#ExtensionPopupRow:hover {{
                background-color: {c.surface_hover};
                border-radius: 6px;
            }}
            QLabel#ExtensionPopupRowName {{
                color: {c.text};
                font-size: 12px;
                font-weight: 500;
                background: transparent;
            }}
            QLabel#ExtensionPopupRowId {{
                color: {c.text_tertiary};
                font-size: 10px;
                font-family: 'Consolas', 'Menlo', monospace;
                background: transparent;
            }}
            QPushButton#ExtensionPopupRowToggle {{
                background-color: {c.surface};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                min-width: 52px;
            }}
            QPushButton#ExtensionPopupRowToggle:checked {{
                background-color: {c.accent_muted};
                color: {c.text};
                border-color: {c.accent};
            }}
            QPushButton#ExtensionPopupRowToggle:hover {{
                background-color: {c.surface_hover};
            }}
            QPushButton#ExtensionPopupRowOptions {{
                background-color: transparent;
                color: {c.accent};
                border: 1px solid {c.border};
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                min-width: 28px;
            }}
            QPushButton#ExtensionPopupRowOptions:hover {{
                background-color: {c.surface_hover};
                border-color: {c.accent};
            }}
            QPushButton#ExtensionPopupRowRemove {{
                background-color: transparent;
                color: {c.text_tertiary};
                border: none;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 14px;
                font-weight: bold;
                min-width: 24px;
            }}
            QPushButton#ExtensionPopupRowRemove:hover {{
                background-color: {c.danger};
                color: white;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {c.border_hover};
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {c.text_tertiary};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

    # ── Events ──
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.closeRequested.emit()
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        # A ``Popup``-type window in some Qt builds (and on the
        # ``offscreen`` QPA plugin in particular) gets an
        # automatic ``closeEvent`` when its focus is lost.  The
        # popup is meant to be hidden, not closed — and the
        # closeRequested signal lets the host know about it.  We
        # always ignore the Qt close and route through ``hide``
        # so the host can decide whether to re-show on the next
        # click.
        _dl_log("popup", "closeEvent:ignoring-and-hiding")
        event.ignore()
        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        _dl_log("popup", "showEvent")
        # Click-outside-to-close: install an event filter on the
        # top-level window so any mouse press outside this popup
        # dismisses it.  PySide6's ``installEventFilter`` requires
        # a ``QObject`` subclass — a plain Python class crashes
        # with ``TypeError: wrong argument types`` at the call
        # site.  We use a tiny ``QObject`` subclass instead.
        #
        # CRITICAL: install the filter via a 0-ms timer.  If we
        # install it synchronously, the very ``MouseButtonPress``
        # event that the user just delivered on the puzzle
        # button (the one that triggered the show) is still in
        # the event queue and gets delivered to the BrowserWindow
        # right after ``show()`` returns.  The filter then sees
        # a press "outside the popup" (it's on the button, not
        # on the popup) and immediately calls ``hide()`` — so
        # the popup flashes open for ~5ms and disappears.
        # Deferring to the next event-loop tick consumes the
        # originating click first.
        if getattr(self, "_outside_filter_installed", False):
            return
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._install_outside_filter)

    def _install_outside_filter(self):
        top = self.window()
        if top is None:
            return
        if getattr(self, "_outside_filter_installed", False):
            return
        flt = _OutsideClickFilter(self)
        top.installEventFilter(flt)
        self._outside_filter = flt
        self._outside_filter_installed = True
        _dl_log("popup", "outside-filter-installed",
                on=top.__class__.__name__)

    def hideEvent(self, event):
        super().hideEvent(event)
        _dl_log("popup", "hideEvent")
        # Remove the event filter when the popup hides so the
        # top-level window doesn't accumulate dead filters across
        # many show/hide cycles.
        flt = getattr(self, "_outside_filter", None)
        top = self.window()
        if flt is not None and top is not None:
            try:
                top.removeEventFilter(flt)
                _dl_log("popup", "outside-filter-removed")
            except Exception as e:
                _dl_log("popup", "outside-filter-remove-failed",
                        err=f"{type(e).__name__}: {e}")
            self._outside_filter = None
            self._outside_filter_installed = False


class _OutsideClickFilter(QObject):
    """Filter that closes the popup on any mouse press outside it.

    Must be a ``QObject`` subclass — PySide6's ``installEventFilter``
    rejects plain Python objects.  Parent-less so it lives on the
    heap until the popup removes the filter (in ``hideEvent``).
    """

    def __init__(self, popup):
        super().__init__()
        self._popup = popup

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if not self._popup.geometry().contains(event.globalPosition().toPoint()):
                self._popup.closeRequested.emit()
                self._popup.hide()
        return False  # never consume the event


class _ExtensionRow(QWidget):
    """One extension entry inside the popup.

    Three actions per row:

      * "On" / "Off" toggle — emits :attr:`toggleClicked`
      * "Options" button    — emits :attr:`optionsClicked` with a
        ``chrome-extension://<id>/<page>`` URL.  Hidden if the
        extension manifest declares no options page.
      * trash icon (X)      — emits :attr:`removeClicked`
    """

    removeClicked = Signal(str)
    toggleClicked = Signal(str, bool)
    optionsClicked = Signal(str, str)

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.setObjectName("ExtensionPopupRow")
        self.setFixedHeight(ExtensionsPopup.ROW_HEIGHT)
        self._item = item
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # Tooltip: name + shortcut hint (Chrome shows the shortcut
        # in the row tooltip of chrome://extensions).
        full_name = self._item.name or self._item.extension_id
        if self._item.shortcut:
            self.setToolTip(f"{full_name}\nShortcut: {self._item.shortcut}")
        else:
            self.setToolTip(full_name)

        # Name + (id / shortcut) stack
        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(1)
        name = self._item.name or self._item.extension_id
        if len(name) > 28:
            name = name[:27] + "\u2026"
        self._name_label = QLabel(name)
        self._name_label.setObjectName("ExtensionPopupRowName")
        labels.addWidget(self._name_label)

        sub_text = self._item.extension_id
        if len(sub_text) > 16:
            sub_text = sub_text[:14] + "…"
        if self._item.shortcut:
            sub_text = f"{sub_text}  \u00b7  {self._item.shortcut}"
        self._id_label = QLabel(sub_text)
        self._id_label.setObjectName("ExtensionPopupRowId")
        labels.addWidget(self._id_label)
        layout.addLayout(labels, 1)

        # Options button — hidden if the extension has no
        # options page declared in its manifest.
        self._options = QPushButton("\u2699")  # gear glyph
        self._options.setObjectName("ExtensionPopupRowOptions")
        self._options.setToolTip("Open this extension's options page")
        self._options.setCursor(Qt.CursorShape.PointingHandCursor)
        self._options.setVisible(bool(self._item.options_url))
        self._options.clicked.connect(self._on_options_clicked)
        layout.addWidget(self._options)

        # Enable toggle
        self._toggle = QPushButton("On" if self._item.is_enabled else "Off")
        self._toggle.setObjectName("ExtensionPopupRowToggle")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(bool(self._item.is_enabled))
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.toggled.connect(self._on_toggle)
        layout.addWidget(self._toggle)

        # Remove button
        self._remove = QPushButton("\u2715")
        self._remove.setObjectName("ExtensionPopupRowRemove")
        self._remove.setToolTip("Uninstall this extension")
        self._remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove.clicked.connect(
            lambda: self.removeClicked.emit(self._item.extension_id)
        )
        layout.addWidget(self._remove)

    def _on_toggle(self, checked: bool):
        self._toggle.setText("On" if checked else "Off")
        self.toggleClicked.emit(self._item.extension_id, checked)

    def _on_options_clicked(self):
        url = self._item.options_url or ""
        if not url:
            return
        self.optionsClicked.emit(self._item.extension_id, url)
