import sys
import os
import json
import random
import string
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QUrl, QTimer, Qt, QProcess, QRect, QPoint, QPropertyAnimation, QEasingCurve, QAbstractAnimation, Signal, QSize, QPointF
from PySide6.QtGui import QAction, QActionGroup, QPainter, QColor, QPen, QFont, QPixmap, QLinearGradient, QIcon, QPainterPath, QCursor, QBrush, QRadialGradient
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QToolBar, QLineEdit,
    QPushButton, QProgressBar, QFileDialog, QMenu, QStatusBar,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox, QTabBar,
    QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
    QStyle, QStyleOptionButton, QStackedWidget, QSizePolicy, QSpacerItem, QFrame,
    QGraphicsDropShadowEffect
)
from PySide6.QtWebEngineCore import (
    QWebEngineProfile, QWebEnginePage, QWebEngineSettings
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from config import SettingsManager, SEARCH_ENGINES
from adblocker import (
    AdBlocker, PrivacyRequestInterceptor,
    inject_fake_cookies, block_third_party_cookies
)
from extensions_loader import ExtensionLoader
from themes import get_theme, THEMES, ThemeMode

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SPOOFED_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class IdentityManager:
    PROFILE_DIR = "browser_profiles"

    def __init__(self):
        os.makedirs(self.PROFILE_DIR, exist_ok=True)
        self.current_profile_id = self.load_or_create_profile()

    def load_or_create_profile(self):
        profile_file = os.path.join(self.PROFILE_DIR, "current_profile.json")
        if os.path.exists(profile_file):
            try:
                with open(profile_file, 'r') as f:
                    profile = json.load(f)
                    return profile.get('id')
            except Exception:
                pass
        return self.generate_new_profile()

    def generate_new_profile(self):
        profile_id = self._generate_id()
        profile_data = {
            'id': profile_id,
            'created': datetime.now().isoformat(),
            'last_reset': datetime.now().isoformat(),
            'browser_fingerprint': self._generate_fingerprint(),
            'cookies_injected': False
        }
        profile_file = os.path.join(self.PROFILE_DIR, "current_profile.json")
        with open(profile_file, 'w') as f:
            json.dump(profile_data, f, indent=4)
        print(f"Generated new browser profile: {profile_id}")
        return profile_id

    def reset_identity(self):
        print("Resetting digital identity...")
        old_id = self.current_profile_id
        new_id = self.generate_new_profile()
        self.current_profile_id = new_id
        archive_dir = os.path.join(self.PROFILE_DIR, "archived")
        os.makedirs(archive_dir, exist_ok=True)
        profile_file = os.path.join(self.PROFILE_DIR, "current_profile.json")
        archive_file = os.path.join(archive_dir, f"profile_{old_id}.json")
        try:
            if os.path.exists(profile_file):
                with open(profile_file, 'r') as src:
                    archived_data = json.load(src)
                with open(archive_file, 'w') as dst:
                    json.dump(archived_data, dst, indent=4)
        except Exception:
            pass
        return new_id

    @staticmethod
    def _generate_id():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"profile_{timestamp}_{random_suffix}"

    @staticmethod
    def _generate_fingerprint():
        return {
            'canvas': ''.join(random.choices(string.hexdigits, k=64)).lower(),
            'webgl': ''.join(random.choices(string.hexdigits, k=64)).lower(),
            'audio': ''.join(random.choices(string.hexdigits, k=64)).lower(),
            'font_hash': ''.join(random.choices(string.hexdigits, k=32)).lower(),
            'locale': random.choice(['en-US', 'en-GB', 'en-CA', 'en-AU']),
            'timezone': random.choice(['UTC', 'EST', 'CST', 'MST', 'PST']),
        }

    def get_current_profile_id(self):
        return self.current_profile_id


class WebPage(QWebEnginePage):
    def __init__(self, profile, browser_window, is_private=False, parent=None):
        super().__init__(profile, parent)
        self._browser_window = browser_window
        self._is_private = is_private

    def createWindow(self, window_type):
        new_view = self._browser_window.add_new_tab(private=self._is_private)
        return new_view.page()


class VeilTabBar(QTabBar):
    CLOSE_SIZE = 16

    doubleClickedEmpty = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setElideMode(Qt.TextElideMode.ElideMiddle)
        self._hovered_index = -1
        self._hovered_close = -1
        self._pressed_close = -1
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMovable(True)
        self.setAcceptDrops(True)

    def _theme(self):
        win = self.window()
        if hasattr(win, 'theme'):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        size.setWidth(min(max(size.width(), 100), 220))
        size.setHeight(self._theme().tab_height)
        return size

    def mousePressEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        if event.button() == Qt.MouseButton.MiddleButton:
            for i in range(self.count()):
                if self.tabRect(i).contains(pos):
                    self.tabCloseRequested.emit(i)
                    event.accept()
                    return
        for i in range(self.count()):
            r = self._close_rect(i)
            if r.contains(pos):
                self._pressed_close = i
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        if self._pressed_close >= 0:
            r = self._close_rect(self._pressed_close)
            if r.contains(pos):
                self.tabCloseRequested.emit(self._pressed_close)
            self._pressed_close = -1
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        new_hover = -1
        new_close = -1
        for i in range(self.count()):
            tr = self.tabRect(i)
            if tr.contains(pos):
                new_hover = i
                if self._close_rect(i).contains(pos):
                    new_close = i
                break
        if new_hover != self._hovered_index or new_close != self._hovered_close:
            self._hovered_index = new_hover
            self._hovered_close = new_close
            self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._hovered_index != -1 or self._hovered_close != -1:
            self._hovered_index = -1
            self._hovered_close = -1
            self.update()

    def mouseDoubleClickEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        for i in range(self.count()):
            if self.tabRect(i).contains(pos):
                self.tabCloseRequested.emit(i)
                event.accept()
                return
        self.doubleClickedEmpty.emit()
        event.accept()

    def _close_rect(self, index):
        tab_rect = self.tabRect(index)
        if tab_rect.isNull():
            return tab_rect
        s = self.CLOSE_SIZE
        margin = 6
        return QRect(tab_rect.right() - s - margin,
                     tab_rect.center().y() - s // 2,
                     s, s)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        t = self._theme()
        c = t.colors

        painter.fillRect(self.rect(), QColor(c.toolbar_bg))

        painter.setPen(Qt.PenStyle.NoPen)

        for index in range(self.count()):
            tab_rect = self.tabRect(index)
            if tab_rect.isNull():
                continue

            is_selected = (index == self.currentIndex())
            is_hovered = (index == self._hovered_index)

            if is_selected:
                fill_rect = tab_rect.adjusted(0, 0, 0, 4)
                path = QPainterPath()
                path.addRoundedRect(fill_rect, 8, 8)
                painter.fillPath(path, QColor(c.input_bg))
            elif is_hovered:
                fill_rect = tab_rect.adjusted(3, 3, -3, 2)
                path = QPainterPath()
                path.addRoundedRect(fill_rect, 6, 6)
                painter.fillPath(path, QColor(c.tab_hover))

            text_rect = tab_rect.adjusted(14, 0, -(self.CLOSE_SIZE + 16), 0)
            title = self.tabText(index)
            font = painter.font()
            font.setFamily(t.font_family.split(',')[0].strip("'\" "))
            font.setPixelSize(12)
            if is_selected:
                font.setWeight(QFont.Weight.DemiBold)
                text_color = QColor(c.text)
            elif is_hovered:
                font.setWeight(QFont.Weight.Medium)
                text_color = QColor(c.text)
            else:
                font.setWeight(QFont.Weight.Normal)
                text_color = QColor(c.text_secondary)
            painter.setFont(font)
            painter.setPen(text_color)
            elided = painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideMiddle, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

            if is_hovered or is_selected:
                self._draw_close_button(painter, index, is_selected)

        painter.end()

    def _draw_close_button(self, painter, index, is_selected):
        t = self._theme()
        c = t.colors
        rect = self._close_rect(index)
        cx, cy = rect.center().x(), rect.center().y()

        is_hovered = (index == self._hovered_close)
        is_pressed = (index == self._pressed_close)

        if is_pressed:
            bg = QColor(c.surface_pressed)
        elif is_hovered:
            bg = QColor(c.surface_hover)
        else:
            bg = None

        if bg is not None:
            path = QPainterPath()
            path.addEllipse(rect)
            painter.fillPath(path, bg)

        if is_hovered or is_pressed:
            cross_color = QColor(c.text)
        elif is_selected:
            cross_color = QColor(c.text_secondary)
        else:
            cross_color = QColor(c.text_tertiary)

        pen = QPen(cross_color)
        pen.setWidthF(1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        s = 3.5
        painter.drawLine(cx - s, cy - s, cx + s, cy + s)
        painter.drawLine(cx + s, cy - s, cx - s, cy + s)


class ChromeButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self.setMinimumWidth(28)
        self._icon_size = 16

    def setIconText(self, text):
        self.setText(text)
        font = self.font()
        font.setPixelSize(14)
        self.setFont(font)


class NavButton(ChromeButton):
    def __init__(self, direction, parent=None):
        super().__init__(parent)
        self._direction = direction
        self.setFixedSize(28, 28)
        self._enabled = True

    def setNavigationEnabled(self, enabled):
        if self._enabled != enabled:
            self._enabled = enabled
            self.update()

    def _theme(self):
        win = self.window()
        if hasattr(win, 'theme'):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        t = self._theme()
        c = t.colors

        is_hover = self.underMouse() and self._enabled

        if is_hover:
            hover_path = QPainterPath()
            hover_path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
            painter.fillPath(hover_path, QColor(c.surface_hover))

        if self._enabled:
            color = QColor(c.text) if is_hover else QColor(c.text_secondary)
        else:
            color = QColor(c.text_muted)

        pen = QPen(color, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        cx, cy = self.width() / 2, self.height() / 2
        s = 4.5

        if self._direction == 'back':
            pts = [
                QPointF(cx + 2, cy - s),
                QPointF(cx - s + 2, cy),
                QPointF(cx + 2, cy + s),
            ]
            path = QPainterPath()
            path.moveTo(pts[0])
            path.lineTo(pts[1])
            path.lineTo(pts[2])
            painter.drawPath(path)

        elif self._direction == 'forward':
            pts = [
                QPointF(cx - 2, cy - s),
                QPointF(cx + s - 2, cy),
                QPointF(cx - 2, cy + s),
            ]
            path = QPainterPath()
            path.moveTo(pts[0])
            path.lineTo(pts[1])
            path.lineTo(pts[2])
            painter.drawPath(path)

        elif self._direction == 'reload':
            r = s - 0.5
            path = QPainterPath()
            path.arcMoveTo(cx - r, cy - r, r * 2, r * 2, 50)
            path.arcTo(cx - r, cy - r, r * 2, r * 2, 50, 280)
            painter.drawPath(path)
            a = 3
            ax = cx + r * 0.85
            ay = cy - r * 0.65
            ap = QPainterPath()
            ap.moveTo(ax - a, ay + a * 0.4)
            ap.lineTo(ax + a * 0.3, ay - a)
            ap.lineTo(ax + a, ay + a * 0.5)
            painter.drawPath(ap)

        elif self._direction == 'home':
            path = QPainterPath()
            path.moveTo(cx, cy - s + 1)
            path.lineTo(cx - s, cy)
            path.lineTo(cx - s + 2.5, cy)
            path.lineTo(cx - s + 2.5, cy + s - 1)
            path.lineTo(cx + s - 2.5, cy + s - 1)
            path.lineTo(cx + s - 2.5, cy)
            path.lineTo(cx + s, cy)
            path.closeSubpath()
            painter.drawPath(path)

        painter.end()


class AddressBar(QLineEdit):
    focused = Signal()
    blurred = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrame(False)
        self.setMinimumHeight(34)
        self.setMaximumHeight(34)
        self.setClearButtonEnabled(False)
        self._has_focus = False

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._has_focus = True
        self.focused.emit()
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._has_focus = False
        self.blurred.emit()
        self.update()

    def _theme(self):
        win = self.window()
        if hasattr(win, 'theme'):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def paintEvent(self, event):
        t = self._theme()
        c = t.colors

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._has_focus:
            bg_color = QColor(c.surface_hover)
            border_color = QColor(c.border_focus)
        else:
            bg_color = QColor(c.input_bg)
            border_color = QColor(c.border)

        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, t.radius_full, t.radius_full)
        painter.fillPath(path, bg_color)

        pen = QPen(border_color, 1)
        painter.setPen(pen)
        painter.drawPath(path)

        super().paintEvent(painter)
        painter.end()


class ChromeBar(QWidget):
    newTabRequested = Signal()
    urlSubmitted = Signal(str)
    backRequested = Signal()
    forwardRequested = Signal()
    reloadRequested = Signal()
    homeRequested = Signal()
    menuRequested = Signal(QPoint)
    addTabRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChromeBar")
        self.setFixedHeight(72)
        self._build()

    def _build(self):
        self.tabs_strip = QWidget()
        self.tabs_strip.setObjectName("TabsStrip")
        tabs_layout = QHBoxLayout(self.tabs_strip)
        tabs_layout.setContentsMargins(4, 2, 4, 0)
        tabs_layout.setSpacing(1)

        self.tab_bar_container = QWidget()
        self.tab_bar_layout = QHBoxLayout(self.tab_bar_container)
        self.tab_bar_layout.setContentsMargins(0, 0, 0, 0)
        self.tab_bar_layout.setSpacing(0)
        tabs_layout.addWidget(self.tab_bar_container, 1)

        self.add_tab_btn = ChromeButton()
        self.add_tab_btn.setIconText("+")
        self.add_tab_btn.setToolTip("New Tab (Ctrl+T)")
        self.add_tab_btn.setFixedSize(24, 24)
        self.add_tab_btn.clicked.connect(self.addTabRequested)
        tabs_layout.addWidget(self.add_tab_btn, 0, Qt.AlignmentFlag.AlignBottom)

        self.toolbar = QWidget()
        self.toolbar.setObjectName("Toolbar")
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(4, 4, 4, 4)
        toolbar_layout.setSpacing(3)

        self.back_btn = NavButton('back')
        self.back_btn.setToolTip("Back")
        self.back_btn.clicked.connect(self.backRequested)
        toolbar_layout.addWidget(self.back_btn)

        self.forward_btn = NavButton('forward')
        self.forward_btn.setToolTip("Forward")
        self.forward_btn.clicked.connect(self.forwardRequested)
        toolbar_layout.addWidget(self.forward_btn)

        self.reload_btn = NavButton('reload')
        self.reload_btn.setToolTip("Reload")
        self.reload_btn.clicked.connect(self.reloadRequested)
        toolbar_layout.addWidget(self.reload_btn)

        self.home_btn = NavButton('home')
        self.home_btn.setToolTip("Home")
        self.home_btn.clicked.connect(self.homeRequested)
        toolbar_layout.addWidget(self.home_btn)

        toolbar_layout.addSpacing(1)

        self.address_bar = AddressBar()
        self.address_bar.setPlaceholderText("Search or enter address")
        self.address_bar.returnPressed.connect(self._on_url_submit)
        toolbar_layout.addWidget(self.address_bar, 1)

        toolbar_layout.addSpacing(1)

        self.shield_btn = ChromeButton()
        self.shield_btn.setIconText("\U0001F6E1")
        self.shield_btn.setToolTip("No threats blocked")
        self.shield_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self.shield_btn.setMinimumWidth(30)
        toolbar_layout.addWidget(self.shield_btn)

        self.menu_btn = ChromeButton()
        self.menu_btn.setIconText("\u22EF")
        self.menu_btn.setToolTip("Menu")
        self.menu_btn.clicked.connect(self._on_menu_click)
        toolbar_layout.addWidget(self.menu_btn)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.tabs_strip)
        main_layout.addWidget(self.toolbar)

        self.divider = QFrame()
        self.divider.setFrameShape(QFrame.Shape.HLine)
        self.divider.setFixedHeight(1)
        self.divider.setObjectName("ChromeDivider")
        main_layout.addWidget(self.divider)

    def addTabBar(self, tab_bar):
        self.tab_bar_layout.addWidget(tab_bar)

    def _on_url_submit(self):
        text = self.address_bar.text().strip()
        if text:
            self.urlSubmitted.emit(text)
            self.address_bar.clearFocus()

    def _on_menu_click(self):
        self.menuRequested.emit(self.mapToGlobal(self.menu_btn.rect().bottomLeft()))

    def setUrl(self, url):
        self.address_bar.setText(url)
        if url:
            self.address_bar.clearFocus()

    def setNavigationState(self, can_back, can_forward):
        self.back_btn.setNavigationEnabled(can_back)
        self.forward_btn.setNavigationEnabled(can_forward)

    def setShieldCount(self, n):
        if n == 0:
            self.shield_btn.setText("\U0001F6E1")
            self.shield_btn.setToolTip("No threats blocked")
        else:
            self.shield_btn.setText(f"\U0001F6E1 {n}")
            self.shield_btn.setToolTip(f"{n} ads/trackers blocked")

    def apply_theme(self, theme):
        c = theme.colors
        is_dark = theme.mode == ThemeMode.DARK

        self.setStyleSheet(f"""
            QWidget#ChromeBar {{
                background-color: {c.toolbar_bg};
            }}
            QWidget#TabsStrip {{
                background-color: {c.toolbar_bg};
                border: none;
            }}
            QWidget#Toolbar {{
                background-color: {c.toolbar_bg};
                border: none;
            }}
            QFrame#ChromeDivider {{
                background-color: {c.divider};
                border: none;
                max-height: 1px;
            }}
            QPushButton {{
                background-color: transparent;
                color: {c.text_secondary};
                border: none;
                font-family: {theme.font_family};
                font-size: {theme.font_size};
            }}
            QPushButton:hover {{
                color: {c.text};
            }}
            QPushButton:pressed {{
                color: {c.accent};
            }}
            QLineEdit {{
                background-color: transparent;
                color: {c.text};
                border: none;
                padding: 0 14px;
                font-family: {theme.font_family};
                font-size: {theme.font_size};
                selection-background-color: {c.accent_muted};
            }}
            QLineEdit::placeholder {{
                color: {c.text_tertiary};
            }}
        """)


class BrowserWindow(QMainWindow):
    def __init__(self, settings, extension_loader, default_profile, private_profile, identity_manager):
        super().__init__()
        self.settings = settings
        self.extension_loader = extension_loader
        self.default_profile = default_profile
        self.private_profile = private_profile
        self.identity_manager = identity_manager

        theme_name = self.settings.get("theme")
        self.theme = get_theme(theme_name)

        self.setWindowTitle("Veil")
        self.resize(1280, 800)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.init_chrome()
        self.init_progress_bar()
        self.init_tabs()
        self.init_statusbar()
        self.init_menu()

        self.apply_theme()

        self.add_new_tab(private=False)

    def init_chrome(self):
        self.chrome = ChromeBar()
        self.chrome.addTabRequested.connect(lambda: self.add_new_tab())
        self.chrome.urlSubmitted.connect(self.navigate_to_url)
        self.chrome.backRequested.connect(self.current_tab_back)
        self.chrome.forwardRequested.connect(self.current_tab_forward)
        self.chrome.reloadRequested.connect(self.current_tab_reload)
        self.chrome.homeRequested.connect(self.go_home)
        self.chrome.menuRequested.connect(self.show_menu_at)
        self.main_layout.addWidget(self.chrome)

    def init_progress_bar(self):
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(2)
        self.main_layout.addWidget(self.progress_bar)

    def init_tabs(self):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(False)
        self.tabs.setMovable(True)
        self.tabs.tabBarDoubleClicked.connect(self.on_tab_bar_double_clicked)
        self.tabs.currentChanged.connect(self.on_tab_changed)

        self.tab_bar = VeilTabBar(self.tabs)
        self.tab_bar.tabCloseRequested.connect(self.close_tab)
        self.tab_bar.doubleClickedEmpty.connect(lambda: self.add_new_tab())
        self.tabs.setTabBar(self.tab_bar)
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)

        self.chrome.addTabBar(self.tab_bar)

        self.main_layout.addWidget(self.tabs)

    def init_statusbar(self):
        self.setStatusBar(QStatusBar())
        profile_id = self.identity_manager.get_current_profile_id()
        self.statusBar().showMessage(f"Ready | Profile: {profile_id[:20]}...")

    def init_menu(self):
        self.menu = QMenu(self)
        self.menu.addAction("New Tab", lambda: self.add_new_tab(), "Ctrl+T")
        self.menu.addAction("New Private Tab", self.add_new_private_tab, "Ctrl+Shift+N")
        self.menu.addSeparator()
        self.menu.addAction("Extensions\u2026", self.show_extensions_dialog)
        self.menu.addAction("Reload Extensions", self.reload_extensions)
        self.menu.addAction("Update AdBlock list", self.update_adblock_list)
        self.https_action = self.menu.addAction("HTTPS-Only Mode")
        self.https_action.setCheckable(True)
        self.https_action.setChecked(self.settings.get("https_only"))
        self.https_action.triggered.connect(self.toggle_https_only)
        self.menu.addSeparator()

        self.theme_menu = self.menu.addMenu("Theme")
        self.theme_action_group = QActionGroup(self)
        self.theme_action_group.setExclusive(True)
        current_theme = self.settings.get("theme")
        for tname, tdata in THEMES.items():
            action = QAction(tdata.label, self)
            action.setCheckable(True)
            action.setChecked(tname == current_theme)
            action.triggered.connect(lambda checked, n=tname: self.switch_theme(n))
            self.theme_action_group.addAction(action)
            self.theme_menu.addAction(action)

        self.menu.addSeparator()
        self.menu.addAction("Reset Digital Identity", self.reset_digital_identity)
        self.menu.addAction("Clear Browsing Data\u2026", self.clear_browsing_data)
        self.menu.addSeparator()
        self.menu.addAction("Exit", self.close)

    def apply_theme(self):
        t = self.theme
        c = t.colors
        is_dark = t.mode == ThemeMode.DARK

        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {c.bg};
            }}
            QWidget {{
                background-color: transparent;
                color: {c.text};
                font-family: {t.font_family};
                font-size: {t.font_size};
            }}
            QTabWidget::pane {{
                border: none;
                background-color: {c.bg};
            }}
            QTabBar {{
                background: transparent;
                border: none;
                qproperty-drawBase: 0;
            }}
            QMenu {{
                background-color: {'rgba(20,20,20,0.96)' if is_dark else 'rgba(252,252,252,0.96)'};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {t.radius_lg}px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 7px 20px;
                border-radius: {t.radius_sm}px;
                margin: 1px 4px;
            }}
            QMenu::item:selected {{
                background-color: {c.surface_hover};
                color: {c.text};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {c.divider};
                margin: 4px 12px;
            }}
            QMenu::icon {{
                padding-left: 6px;
            }}
            QDialog {{
                background-color: {c.bg};
                color: {c.text};
            }}
            QListWidget {{
                background-color: {c.surface};
                border: 1px solid {c.border};
                border-radius: {t.radius_md}px;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 8px 12px;
                border-radius: {t.radius_sm}px;
            }}
            QListWidget::item:selected {{
                background-color: {c.surface_hover};
                color: {c.text};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: {t.scrollbar_width}px;
                border: none;
                margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {c.scrollbar};
                border-radius: {t.scrollbar_thumb_radius}px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {c.scrollbar_hover};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: {t.scrollbar_width}px;
                border: none;
                margin: 2px;
            }}
            QScrollBar::handle:horizontal {{
                background: {c.scrollbar};
                border-radius: {t.scrollbar_thumb_radius}px;
                min-width: 30px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {c.scrollbar_hover};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
            QProgressBar {{
                border: none;
                background-color: {c.progress_bg};
                height: {t.progress_height}px;
            }}
            QProgressBar::chunk {{
                background-color: {c.accent};
                border-radius: 0px;
            }}
            QStatusBar {{
                background-color: {c.toolbar_bg};
                color: {c.text_tertiary};
                border-top: 1px solid {c.divider};
                font-size: 11px;
                padding: 3px 12px;
            }}
            QToolTip {{
                background-color: {'rgba(20,20,20,0.96)' if is_dark else 'rgba(252,252,252,0.96)'};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {t.radius_sm}px;
                padding: 5px 10px;
                font-size: 12px;
            }}
            QPushButton {{
                background-color: transparent;
                color: {c.text};
                border: none;
                font-family: {t.font_family};
                font-size: {t.font_size};
            }}
            QPushButton:hover {{
                background-color: {c.surface_hover};
            }}
            QMessageBox {{
                background-color: {c.bg};
                color: {c.text};
            }}
            QMessageBox QLabel {{
                color: {c.text};
            }}
            QMessageBox QPushButton {{
                background-color: {c.surface};
                border: 1px solid {c.border};
                border-radius: {t.radius_sm}px;
                padding: 6px 16px;
                color: {c.text};
                min-width: 60px;
            }}
            QMessageBox QPushButton:hover {{
                background-color: {c.surface_hover};
                border-color: {c.border_hover};
            }}
        """)

        self.chrome.apply_theme(t)
        self.tab_bar.update()
        self.tabs.update()

    def switch_theme(self, name):
        self.settings.set("theme", name)
        self.theme = get_theme(name)
        self.apply_theme()
        for action in self.theme_action_group.actions():
            action.setChecked(action.text() == self.theme.label)
        self.statusBar().showMessage(f"Theme: {self.theme.label}", 2000)

    def add_new_tab(self, url=None, title="New Tab", private=False):
        view = QWebEngineView()

        if private:
            page = WebPage(self.private_profile, self, is_private=True, parent=view)
            view.setProperty("is_private", True)
        else:
            page = WebPage(
                self.default_profile, self,
                is_private=False, parent=view
            )
            view.setProperty("is_private", False)

        view.setPage(page)

        s = page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.ReadingFromCanvasEnabled, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly, True)

        if url is None:
            url = QUrl(Path(SCRIPT_DIR, "start_page.html").as_uri())
            if private:
                url.setQuery("mode=incognito")

        view.load(url)

        index = self.tabs.addTab(view, title)
        if private:
            self.tabs.setTabText(index, f"\U0001f512 {title}")
        self.tabs.setCurrentIndex(index)

        view.titleChanged.connect(lambda t, v=view: self.update_tab_title(v, t))
        view.urlChanged.connect(lambda u, v=view: self.update_tab_url(v, u))
        view.loadProgress.connect(lambda p, v=view: self.update_tab_progress(v, p))
        view.loadFinished.connect(lambda f, v=view: self.on_load_finished(v, f))
        page.linkHovered.connect(self.on_link_hovered)
        view.installEventFilter(self)

        return view

    def add_new_private_tab(self):
        self.add_new_tab(private=True)

    def close_tab(self, index):
        if self.tabs.count() > 1:
            view = self.tabs.widget(index)
            view.setPage(None)
            self.tabs.removeTab(index)
            view.deleteLater()
        else:
            self.close()

    def on_tab_bar_double_clicked(self, index):
        if index == -1:
            self.add_new_tab()
        else:
            self.close_tab(index)

    def on_tab_changed(self, index):
        if index < 0:
            return
        view = self.tabs.widget(index)
        if view:
            self.update_navigation_buttons(view)
            url = view.url().toString()
            if url.startswith("file://") and "start_page.html" in url:
                self.chrome.setUrl("")
            else:
                self.chrome.setUrl(url)

    def update_tab_title(self, view, title):
        index = self.tabs.indexOf(view)
        if index != -1:
            is_private = view.property("is_private")
            clean_title = title if title else "New Tab"
            if is_private:
                self.tabs.setTabText(index, f"\U0001f512 {clean_title}")
            else:
                self.tabs.setTabText(index, clean_title)

    def update_tab_url(self, view, url):
        url_str = url.toString()
        if "start_page.html" in url_str and "engine=" in url_str:
            try:
                fragment = url.fragment()
                payload = fragment.split("engine=", 1)[1]
                name, enc_url = payload.split("|", 1)
                from urllib.parse import unquote
                self.set_search_engine(unquote(name), unquote(enc_url))
                view.page().runJavaScript(
                    "if (history.replaceState) {"
                    "  history.replaceState(null, '', window.location.pathname + window.location.search);"
                    "}"
                )
                return
            except Exception:
                pass

        if view == self.tabs.currentWidget():
            if url_str.startswith("file://") and "start_page.html" in url_str:
                self.chrome.setUrl("")
            else:
                self.chrome.setUrl(url_str)
            self.update_navigation_buttons(view)

    def update_tab_progress(self, view, progress):
        view.setProperty("progress", progress)
        if view == self.tabs.currentWidget():
            self.progress_bar.setValue(progress)
            if progress < 100 and self.theme.progress_visible:
                self.progress_bar.show()
            else:
                self.progress_bar.hide()

    def on_load_finished(self, view, success):
        view.setProperty("progress", 100)
        if view == self.tabs.currentWidget():
            self.progress_bar.hide()
            self.update_navigation_buttons(view)

    def update_navigation_buttons(self, view):
        self.chrome.setNavigationState(view.history().canGoBack(), view.history().canGoForward())

    def current_tab_back(self):
        view = self.tabs.currentWidget()
        if view:
            view.back()

    def current_tab_forward(self):
        view = self.tabs.currentWidget()
        if view:
            view.forward()

    def current_tab_reload(self):
        view = self.tabs.currentWidget()
        if view:
            view.reload()

    def go_home(self):
        view = self.tabs.currentWidget()
        if view:
            is_private = view.property("is_private")
            url = QUrl(Path(SCRIPT_DIR, "start_page.html").as_uri())
            if is_private:
                url.setQuery("mode=incognito")
            view.load(url)

    def navigate_to_url(self, text):
        text = text.strip()
        if not text:
            return
        view = self.tabs.currentWidget()
        if not view:
            return
        if text.startswith("http://") or text.startswith("https://") or text.startswith("file://"):
            url = QUrl(text)
        elif "." in text and " " not in text:
            url = QUrl("https://" + text)
        else:
            search_url = self.settings.get("search_engine")
            url = QUrl(search_url + text)
        view.load(url)

    def on_link_hovered(self, url_str):
        self.statusBar().showMessage(url_str)

    def show_menu_at(self, pos):
        self.menu.exec(pos)

    def eventFilter(self, obj, event):
        if event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_F5:
                self.current_tab_reload()
                return True
        return super().eventFilter(obj, event)

    def show_extensions_dialog(self):
        manager = self.default_profile.extensionManager()
        installed = manager.extensions()
        t = self.theme
        c = t.colors
        is_dark = t.mode == ThemeMode.DARK

        dlg = QDialog(self)
        dlg.setWindowTitle("Extensions")
        dlg.resize(440, 340)
        dlg.setStyleSheet(f"""
            QDialog {{
                background-color: {c.bg};
                color: {c.text};
                font-family: {t.font_family};
            }}
            QListWidget {{
                background-color: {c.surface};
                border: 1px solid {c.border};
                border-radius: {t.radius_md}px;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 10px 12px;
                border-radius: {t.radius_sm}px;
            }}
            QListWidget::item:selected {{
                background-color: {c.surface_hover};
                color: {c.text};
            }}
            QPushButton {{
                background-color: {c.surface};
                border: 1px solid {c.border};
                border-radius: {t.radius_sm}px;
                padding: 7px 16px;
                color: {c.text};
                min-width: 60px;
            }}
            QPushButton:hover {{
                background-color: {c.surface_hover};
                border-color: {c.border_hover};
            }}
            QLabel {{
                color: {c.text_secondary};
            }}
        """)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header = QLabel(
            f"<b style='font-size:14px'>Extensions</b><br>"
            f"<span style='color:{c.text_secondary}; font-size:11px'>"
            f"{self.extension_loader.support_reason()}</span>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        list_widget = QListWidget()
        for ext in installed:
            try:
                item = QListWidgetItem(f"{ext.name()}  \u2014  {ext.id()}")
            except Exception:
                item = QListWidgetItem(str(ext.id()))
            list_widget.addItem(item)
        if installed is None or len(installed) == 0:
            placeholder = QListWidgetItem("(no extensions loaded)")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            list_widget.addItem(placeholder)
        layout.addWidget(list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        reload_btn = buttons.addButton("Reload", QDialogButtonBox.ButtonRole.ActionRole)
        reload_btn.clicked.connect(self.reload_extensions)
        buttons.rejected.connect(dlg.close)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    def reload_extensions(self):
        self.extension_loader.install_all()
        self.statusBar().showMessage("Reloading extensions\u2026", 3000)

    def on_extension_status(self, msg):
        self.statusBar().showMessage(msg, 4000)
        n = self.extension_loader.count()
        suffix = f" \u2014 {n} extension{'s' if n != 1 else ''} loaded" if n else ""
        profile_id = self.identity_manager.get_current_profile_id()
        self.statusBar().showMessage(f"Ready{suffix} | Profile: {profile_id[:20]}\u2026", 0)

    def on_extension_count(self, n):
        suffix = f"{n} extension{'s' if n != 1 else ''} loaded" if n else "no extensions"
        profile_id = self.identity_manager.get_current_profile_id()
        self.statusBar().showMessage(f"Ready \u2014 {suffix} | Profile: {profile_id[:20]}\u2026", 0)

    def toggle_https_only(self, checked):
        self.settings.set("https_only", checked)
        state = "enabled" if checked else "disabled"
        self.statusBar().showMessage(f"HTTPS-Only mode {state}", 3000)

    def update_adblock_list(self):
        self.statusBar().showMessage("Updating ad-block list\u2026", 0)
        def _on_done(ok, total, error=None):
            if ok:
                self.statusBar().showMessage(
                    f"Ad-block list updated: {total:,} entries", 4000
                )
            else:
                self.statusBar().showMessage(
                    f"Ad-block list update failed: {error}", 5000
                )
        self.adblocker.update_from_url(on_done=_on_done)

    def on_adblock_count_changed(self, n):
        self.chrome.setShieldCount(n)

    def set_search_engine(self, name, url):
        self.settings.set("search_engine_name", name)
        self.settings.set("search_engine", url)
        self.statusBar().showMessage(f"Search engine set to {name}", 3000)

    def reset_digital_identity(self):
        t = self.theme
        c = t.colors
        is_dark = t.mode == ThemeMode.DARK

        confirm = QMessageBox.question(
            self, "Reset Digital Identity",
            "This will:\n  Clear all cookies, site data, and cache\n  "
            "Reset browser fingerprint\n  Generate fresh browsing profile\n  "
            "Inject new fake tracking cookies\n\n"
            "All tabs will be closed. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.default_profile.clearHttpCache()
            self.default_profile.cookieStore().deleteAllCookies()
            self.private_profile.clearHttpCache()
            self.private_profile.cookieStore().deleteAllCookies()
            new_id = self.identity_manager.reset_identity()
            inject_fake_cookies(self.default_profile)
            inject_fake_cookies(self.private_profile)
            while self.tabs.count() > 0:
                self.close_tab(0)
            self.add_new_tab(private=False)
            self.statusBar().showMessage(
                f"Digital identity reset! New profile: {new_id[:20]}...", 5000
            )

    def clear_browsing_data(self):
        confirm = QMessageBox.question(
            self, "Clear Browsing Data",
            "Are you sure you want to delete all cache, cookies, and website data?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.default_profile.clearHttpCache()
            self.default_profile.cookieStore().deleteAllCookies()
            self.private_profile.clearHttpCache()
            self.private_profile.cookieStore().deleteAllCookies()
            self.statusBar().showMessage("All browsing data cleared.", 4000)

    def on_download_requested(self, download_item):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save File", download_item.suggestedFileName()
        )
        if path:
            download_item.setDownloadDirectory(os.path.dirname(path))
            download_item.setDownloadFileName(os.path.basename(path))
            download_item.accept()
            self.statusBar().showMessage(
                f"Downloading {download_item.suggestedFileName()}...", 5000
            )
            download_item.isFinishedChanged.connect(
                lambda: self.statusBar().showMessage(
                    f"Download complete: {download_item.suggestedFileName()}", 5000
                )
            )


class DevAutoReloader:
    def __init__(self, window):
        self.window = window
        self._watch_exts = (".py", ".html", ".qss")
        self._watch_dirs = {SCRIPT_DIR}
        self._mtimes = {}
        self._scan()
        self._timer = QTimer(window)
        self._timer.timeout.connect(self._tick)
        self._timer.start(800)

    def _scan(self):
        for d in self._watch_dirs:
            if not os.path.isdir(d):
                continue
            for root, _dirs, files in os.walk(d):
                for f in files:
                    if f.endswith(self._watch_exts):
                        path = os.path.join(root, f)
                        try:
                            self._mtimes[path] = os.path.getmtime(path)
                        except OSError:
                            pass

    def _tick(self):
        for path, old_mtime in list(self._mtimes.items()):
            try:
                new_mtime = os.path.getmtime(path)
                if new_mtime != old_mtime:
                    print(f"[dev] Change detected: {os.path.basename(path)}")
                    self._restart()
                    return
            except OSError:
                pass

    def _restart(self):
        self._timer.stop()
        QProcess.startDetached(sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:])
        QApplication.quit()


def apply_performance_settings(settings):
    settings.setAttribute(settings.WebAttribute.PluginsEnabled, False)
    settings.setAttribute(settings.WebAttribute.PdfViewerEnabled, False)
    settings.setAttribute(settings.WebAttribute.FullScreenSupportEnabled, False)
    settings.setAttribute(settings.WebAttribute.ScreenCaptureEnabled, False)
    settings.setAttribute(settings.WebAttribute.WebGLEnabled, False)
    settings.setAttribute(settings.WebAttribute.Accelerated2dCanvasEnabled, False)
    settings.setAttribute(settings.WebAttribute.HyperlinkAuditingEnabled, False)
    settings.setAttribute(settings.WebAttribute.ReadingFromCanvasEnabled, False)
    settings.setAttribute(settings.WebAttribute.WebRTCPublicInterfacesOnly, True)
    settings.setAttribute(settings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
    settings.setAttribute(settings.WebAttribute.LocalContentCanAccessFileUrls, False)
    settings.setAttribute(settings.WebAttribute.XSSAuditingEnabled, True)
    settings.setAttribute(settings.WebAttribute.ScrollAnimatorEnabled, False)
    settings.setAttribute(settings.WebAttribute.ErrorPageEnabled, True)
    settings.setAttribute(settings.WebAttribute.DnsPrefetchEnabled, True)
    settings.setAttribute(settings.WebAttribute.JavascriptCanOpenWindows, True)
    settings.setAttribute(settings.WebAttribute.JavascriptCanAccessClipboard, False)


def main():
    if "--dev" in sys.argv:
        sys.argv.remove("--dev")

    settings = SettingsManager()
    app = QApplication(sys.argv)

    identity_manager = IdentityManager()
    adblocker = AdBlocker(settings)
    interceptor = PrivacyRequestInterceptor(adblocker, settings)

    default_profile = QWebEngineProfile("veil-default", app)
    default_profile.setUrlRequestInterceptor(interceptor)
    default_profile.setHttpUserAgent(SPOOFED_UA)
    default_profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    default_profile.setHttpCacheMaximumSize(0)
    default_profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    )
    default_profile.setSpellCheckEnabled(False)
    default_profile.setHttpAcceptLanguage("en-US,en;q=0.9")
    block_third_party_cookies(default_profile)
    inject_fake_cookies(default_profile)
    apply_performance_settings(default_profile.settings())

    private_profile = QWebEngineProfile("", app)
    private_profile.setUrlRequestInterceptor(interceptor)
    private_profile.setHttpUserAgent(SPOOFED_UA)
    private_profile.setSpellCheckEnabled(False)
    private_profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    private_profile.setHttpCacheMaximumSize(0)
    private_profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    )
    private_profile.setHttpAcceptLanguage("en-US,en;q=0.9")
    block_third_party_cookies(private_profile)
    apply_performance_settings(private_profile.settings())

    extension_loader = ExtensionLoader(settings, profile=default_profile)

    window = BrowserWindow(settings, extension_loader, default_profile, private_profile, identity_manager)
    window.adblocker = adblocker
    adblocker.count_changed.connect(window.on_adblock_count_changed)
    extension_loader.status_changed.connect(window.on_extension_status)
    extension_loader.count_changed.connect(window.on_extension_count)
    extension_loader.install_all()

    default_profile.downloadRequested.connect(window.on_download_requested)
    private_profile.downloadRequested.connect(window.on_download_requested)

    window.show()

    if "--dev" in sys.argv:
        DevAutoReloader(window)
        print("[dev] Auto-reload active \u2014 watching for file changes...")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
