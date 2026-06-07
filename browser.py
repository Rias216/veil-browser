import sys
import os
import json
import random
import string
from pathlib import Path
from datetime import datetime
import platform

from PySide6.QtCore import (
    QUrl, QTimer, Qt, QProcess, QRect, QRectF, QPoint,
    QPropertyAnimation, QVariantAnimation, QEasingCurve, QAbstractAnimation,
    Signal, QSize, QPointF
)
from PySide6.QtGui import (
    QAction, QActionGroup, QPainter, QColor, QPen, QFont,
    QPixmap, QLinearGradient, QIcon, QPainterPath, QCursor,
    QBrush, QRadialGradient, QFontMetrics
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QToolBar, QLineEdit,
    QPushButton, QProgressBar, QFileDialog, QMenu, QStatusBar,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox,
    QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
    QStyle, QStyleOptionButton, QStackedWidget, QSizePolicy,
    QSpacerItem, QFrame, QGraphicsDropShadowEffect,
    QStyledItemDelegate
)
from PySide6.QtGui import QPalette
from PySide6.QtWebEngineCore import (
    QWebEngineProfile, QWebEnginePage, QWebEngineSettings
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkRequest, QNetworkReply
)

# Windows native window management (resize, snap, shadow)
if platform.system() == "Windows":
    import ctypes
    from ctypes import wintypes

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


def _resolve_font(family_string, pixel_size=13):
    font = QFont()
    families = [f.strip().strip("'\"") for f in family_string.split(",")]
    font.setFamilies(families)
    font.setPixelSize(pixel_size)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


# ── Identity Manager ─────────────────────────────────────────────────
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


# ── WebPage ──────────────────────────────────────────────────────────
class WebPage(QWebEnginePage):
    def __init__(self, profile, browser_window, is_private=False, parent=None):
        super().__init__(profile, parent)
        self._browser_window = browser_window
        self._is_private = is_private

    def createWindow(self, window_type):
        new_view = self._browser_window.add_new_tab(private=self._is_private)
        return new_view.page()


# ── ChromeTabBar ───────────────────────────────────────────────────
class ChromeTabBar(QWidget):
    """Chrome-like tab bar — fully custom painted with favicons, loading
    spinners, hover/close animations, drag-reorder, scroll overflow,
    middle-click close, and keyboard navigation."""

    TAB_HEIGHT = 30
    MIN_TAB_WIDTH = 72
    MAX_TAB_WIDTH = 200
    TAB_RADIUS = 7
    CLOSE_SIZE = 14
    FAVICON_SIZE = 14
    BTM_DIVIDER = 1
    SCROLL_BTN_W = 22
    SCROLL_BTN_MARGIN = 2
    NEW_TAB_BTN_W = 28

    AUDIO_SIZE = 10

    LEFT_PAD = 6
    RIGHT_PAD = 6
    FAV_GAP = 4
    CLOSE_GAP = 3

    tabCloseRequested = Signal(int)
    tabSelected = Signal(int)
    newTabRequested = Signal()
    tabMoved = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChromeTabBar")
        self.setFixedHeight(self.TAB_HEIGHT)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._tabs = []
        self._current = -1
        self._hovered = -1
        self._hovered_close = -1
        self._pressed_close = -1
        self._scroll_offset = 0
        self._tab_rects = []
        self._total_width = 0
        self._has_overflow = False
        self._hover_scroll_left = False
        self._hover_scroll_right = False
        self._hover_new_tab = False

        self._drag_idx = -1
        self._drag_start_x = 0
        self._is_dragging = False

        self._hover_fade = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(150)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_step)

        self._close_fade = 0.0
        self._close_anim = QVariantAnimation(self)
        self._close_anim.setDuration(150)
        self._close_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._close_anim.valueChanged.connect(self._on_close_step)

        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(30)
        self._spinner_timer.timeout.connect(self._spin_step)

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(100)
        self._scroll_timer.timeout.connect(self._scroll_tick)

    def _theme(self):
        win = self.window()
        if hasattr(win, 'theme'):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def _on_hover_step(self, v):
        self._hover_fade = v
        if self._hovered >= 0 and self._hovered < len(self._tab_rects):
            self.update(self._tab_rects[self._hovered])

    def _on_close_step(self, v):
        self._close_fade = v
        if self._hovered_close >= 0:
            self.update(self._close_rect(self._hovered_close))
        elif self._hovered >= 0:
            self.update(self._close_rect(self._hovered))

    def _spin_step(self):
        self._spinner_angle = (self._spinner_angle + 30) % 360
        for i in range(len(self._tabs)):
            p = self._tabs[i].get("progress", -1)
            if 0 <= p < 100:
                self._repaint_tab(i)

    # ── Tab data management ──

    def addTab(self, index=None, title="", icon=None):
        data = {"title": title or "New Tab", "icon": icon, "progress": -1, "is_private": False, "is_playing": False}
        if index is None or index >= len(self._tabs):
            self._tabs.append(data)
            new_idx = len(self._tabs) - 1
        else:
            self._tabs.insert(index, data)
            new_idx = index
        self._recalc()
        return new_idx

    def removeTab(self, index):
        if 0 <= index < len(self._tabs):
            self._tabs.pop(index)
            old = self._current
            if self._current >= len(self._tabs):
                self._current = len(self._tabs) - 1 if len(self._tabs) > 0 else -1
            self._recalc()
            if self._current != old and self._current >= 0:
                self.tabSelected.emit(self._current)

    def setTabText(self, index, text):
        if 0 <= index < len(self._tabs):
            old_w = self._get_tab_width(self._tabs[index]["title"])
            new_w = self._get_tab_width(text)
            self._tabs[index]["title"] = text
            if abs(new_w - old_w) > 2:
                self._recalc()
            else:
                self.update()

    def _get_tab_width(self, title):
        fm = QFontMetrics(self._tab_font())
        text_w = fm.horizontalAdvance(title)
        audio_space = self.AUDIO_SIZE + self.FAV_GAP
        natural = (self.LEFT_PAD + self.FAVICON_SIZE + self.FAV_GAP + text_w
                   + audio_space + self.CLOSE_GAP + self.CLOSE_SIZE + self.RIGHT_PAD)
        return max(self.MIN_TAB_WIDTH, min(self.MAX_TAB_WIDTH, natural))

    def tabText(self, index):
        if 0 <= index < len(self._tabs):
            return self._tabs[index]["title"]
        return ""

    def setTabIcon(self, index, icon):
        if 0 <= index < len(self._tabs):
            self._tabs[index]["icon"] = icon
            self.update()

    def setTabProgress(self, index, progress):
        if 0 <= index < len(self._tabs):
            self._tabs[index]["progress"] = progress
            self.update()
            self._update_spinner_timer()

    def _update_spinner_timer(self):
        any_loading = any(0 <= t.get("progress", -1) < 100 for t in self._tabs)
        if any_loading and not self._spinner_timer.isActive():
            self._spinner_timer.start()
        elif not any_loading and self._spinner_timer.isActive():
            self._spinner_timer.stop()

    def setPrivate(self, index, private):
        if 0 <= index < len(self._tabs):
            self._tabs[index]["is_private"] = private
            self.update()

    def setCurrentIndex(self, index):
        if index != self._current:
            old = self._current
            self._current = index
            if old >= 0:
                self._repaint_tab(old)
            if index >= 0:
                self._repaint_tab(index)
            self.tabSelected.emit(index)

    def currentIndex(self):
        return self._current

    def count(self):
        return len(self._tabs)

    def invalidate_layout(self):
        self._recalc()

    # ── Layout ──

    def _tab_btn_area(self):
        return (self.SCROLL_BTN_W * 2 + 4) if self._has_overflow else 0

    def _new_tab_btn_area(self):
        return self.NEW_TAB_BTN_W + 2

    def _available_width(self):
        return max(50, self.width() - self._tab_btn_area() - self._new_tab_btn_area())

    def _recalc(self):
        w = self.width()
        self._tab_rects = []
        tab_count = len(self._tabs)
        if tab_count == 0:
            self._total_width = 0
            self._has_overflow = False
            self.update()
            return

        natural_widths = [self._get_tab_width(t["title"]) for t in self._tabs]
        total_natural = sum(natural_widths)

        available_if_not = max(50, w - self._new_tab_btn_area())
        self._has_overflow = total_natural > available_if_not

        if self._has_overflow:
            for nw in natural_widths:
                cw = max(self.MIN_TAB_WIDTH, min(self.MAX_TAB_WIDTH, nw))
                self._tab_rects.append(QRect(0, 0, cw, self.TAB_HEIGHT))
            self._total_width = sum(r.width() for r in self._tab_rects)
        else:
            available = self._available_width()
            extra = max(0, available - total_natural)
            per_tab = extra // tab_count
            x = 0
            for nw in natural_widths:
                cw = min(self.MAX_TAB_WIDTH, max(self.MIN_TAB_WIDTH, nw) + per_tab)
                self._tab_rects.append(QRect(x, 0, cw, self.TAB_HEIGHT))
                x += cw
            self._total_width = x

        self._clamp_scroll()
        self.update()

    def _clamp_scroll(self):
        if not self._has_overflow:
            self._scroll_offset = 0
            return
        max_off = max(0, self._total_width - self._available_width())
        self._scroll_offset = max(0, min(self._scroll_offset, max_off))

    def _tab_font(self, selected=False):
        t = self._theme()
        font = _resolve_font(t.font_family, 12)
        font.setWeight(QFont.Weight.Medium if selected else QFont.Weight.Normal)
        return font

    def _tab_at(self, pos):
        ox = self._tab_start_x()
        for i, r in enumerate(self._tab_rects):
            tr = QRect(r.x() + ox - self._scroll_offset, r.y(), r.width(), r.height())
            if tr.contains(pos):
                return i
        return -1

    def _tab_rect_at(self, index):
        if 0 <= index < len(self._tab_rects):
            r = self._tab_rects[index]
            ox = self._tab_start_x()
            return QRect(r.x() + ox - self._scroll_offset, r.y(), r.width(), r.height())
        return QRect()

    def _tab_start_x(self):
        return self.SCROLL_BTN_W * 2 + 4 if self._has_overflow else 0

    def _scroll_left_rect(self):
        s = self.SCROLL_BTN_W
        return QRect(2, (self.TAB_HEIGHT - s) // 2, s, s)

    def _scroll_right_rect(self):
        s = self.SCROLL_BTN_W
        return QRect(2 + s + 2, (self.TAB_HEIGHT - s) // 2, s, s)

    def _new_tab_rect(self):
        right_edge = self.width()
        size = self.NEW_TAB_BTN_W
        return QRect(right_edge - size - 2, (self.TAB_HEIGHT - size) // 2,
                     size, size)

    def _close_rect(self, index):
        tr = self._tab_rect_at(index)
        if tr:
            return QRect(tr.right() - self.CLOSE_SIZE - self.RIGHT_PAD,
                         tr.y() + (tr.height() - self.CLOSE_SIZE) // 2,
                         self.CLOSE_SIZE, self.CLOSE_SIZE)
        return QRect()

    def _favicon_rect(self, index):
        tr = self._tab_rect_at(index)
        if tr:
            return QRect(tr.x() + self.LEFT_PAD,
                         tr.y() + (tr.height() - self.FAVICON_SIZE) // 2,
                         self.FAVICON_SIZE, self.FAVICON_SIZE)
        return QRect()

    def _repaint_tab(self, idx):
        if 0 <= idx < len(self._tab_rects):
            self.update(self._tab_rect_at(idx))

    def is_drag_area(self, global_pos):
        local = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local):
            return False
        if self._tab_at(local) >= 0:
            return False
        if self._new_tab_rect().contains(local):
            return False
        if self._has_overflow:
            if self._scroll_left_rect().contains(local) or self._scroll_right_rect().contains(local):
                return False
        return True

    # ── Events ──

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recalc()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Tab and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            nxt = (self._current + 1) % len(self._tabs) if self._tabs else 0
            self.setCurrentIndex(nxt)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Tab and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            prv = (self._current - 1) % len(self._tabs) if self._tabs else 0
            self.setCurrentIndex(prv)
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        idx = self._tab_at(pos)
        old_hover = self._hovered
        old_close = self._hovered_close
        new_close = -1

        if idx >= 0:
            if self._close_rect(idx).contains(pos):
                new_close = idx

        self._hovered = idx
        self._hovered_close = new_close

        if idx != old_hover:
            if old_hover >= 0 and old_hover != self._current:
                self._start_hover_anim(0.0)
            if idx >= 0 and idx != self._current:
                self._start_hover_anim(1.0)
            self._repaint_tab(old_hover)
            self._repaint_tab(idx)
        elif new_close != old_close and idx >= 0:
            self._start_close_anim(1.0 if new_close >= 0 else 0.0)
            self.update(self._close_rect(idx))

        if old_close >= 0 and new_close < 0:
            self._start_close_anim(0.0)
            self.update(self._close_rect(old_close))

        scroll_arrow = False
        if self._has_overflow:
            sr = self._scroll_right_rect()
            sl = self._scroll_left_rect()
            new_sl = sl.contains(pos) and self._scroll_offset > 0
            new_sr = sr.contains(pos) and self._scroll_offset < max(0, self._total_width - (self.width() - self.SCROLL_BTN_W * 2 - self.NEW_TAB_BTN_W))
            if new_sl != self._hover_scroll_left or new_sr != self._hover_scroll_right:
                self._hover_scroll_left = new_sl
                self._hover_scroll_right = new_sr
                scroll_arrow = True
                self.update(sl)
                self.update(sr)
                if new_sl or new_sr:
                    if not self._scroll_timer.isActive():
                        self._scroll_timer.start()
                else:
                    self._scroll_timer.stop()

        ntr = self._new_tab_rect()
        new_nt = ntr.contains(pos)
        if new_nt != self._hover_new_tab:
            self._hover_new_tab = new_nt
            self.update(ntr)

        if not scroll_arrow and not new_nt:
            if self._scroll_timer.isActive() and not (self._hover_scroll_left or self._hover_scroll_right):
                self._scroll_timer.stop()

        if self._drag_idx >= 0 and event.buttons() == Qt.MouseButton.LeftButton:
            if not self._is_dragging:
                dx = abs(pos.x() - self._drag_start_x)
                if dx >= 8:
                    self._is_dragging = True
            if self._is_dragging:
                target = self._tab_at(pos)
                if target >= 0 and target != self._drag_idx:
                    self._move_tab(self._drag_idx, target)
                    self._drag_idx = target

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        idx = self._tab_at(pos)
        if event.button() == Qt.MouseButton.LeftButton:
            if self._new_tab_rect().contains(pos):
                self.newTabRequested.emit()
                event.accept()
                return
            if self._has_overflow:
                if self._scroll_left_rect().contains(pos) and self._scroll_offset > 0:
                    self._scroll_offset = max(0, self._scroll_offset - 40)
                    self.update()
                    return
                if self._scroll_right_rect().contains(pos):
                    max_off = max(0, self._total_width - (self.width() - self.SCROLL_BTN_W * 2 - self.NEW_TAB_BTN_W))
                    self._scroll_offset = min(max_off, self._scroll_offset + 40)
                    self.update()
                    return
            if idx >= 0:
                if self._close_rect(idx).contains(pos):
                    self._pressed_close = idx
                    self.update(self._close_rect(idx))
                else:
                    self._drag_idx = idx
                    self._drag_start_x = pos.x()
                    self._is_dragging = False
                    self.setCurrentIndex(idx)
        elif event.button() == Qt.MouseButton.MiddleButton:
            if idx >= 0:
                self.tabCloseRequested.emit(idx)

    def mouseReleaseEvent(self, event):
        if self._pressed_close >= 0:
            pos = event.position().toPoint()
            if self._close_rect(self._pressed_close).contains(pos):
                self.tabCloseRequested.emit(self._pressed_close)
            self._pressed_close = -1
            self.update()
        self._drag_idx = -1
        self._is_dragging = False

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            idx = self._tab_at(pos)
            if idx < 0:
                ntr = self._new_tab_rect()
                if not ntr.contains(pos):
                    self.newTabRequested.emit()
            else:
                self.tabCloseRequested.emit(idx)

    def leaveEvent(self, event):
        old = self._hovered
        old_close = self._hovered_close
        self._hovered = -1
        self._hovered_close = -1
        self._hover_scroll_left = False
        self._hover_scroll_right = False
        self._hover_new_tab = False
        self._scroll_timer.stop()
        if old >= 0 and old != self._current:
            self._start_hover_anim(0.0)
        if old_close >= 0:
            self._start_close_anim(0.0)
        self._repaint_tab(old)
        self.update()

    def _scroll_tick(self):
        if self._hover_scroll_left and self._scroll_offset > 0:
            self._scroll_offset = max(0, self._scroll_offset - 20)
            self.update()
        elif self._hover_scroll_right:
            max_off = max(0, self._total_width - (self.width() - self.SCROLL_BTN_W * 2 - self.NEW_TAB_BTN_W))
            if self._scroll_offset < max_off:
                self._scroll_offset = min(max_off, self._scroll_offset + 20)
                self.update()
            else:
                self._scroll_timer.stop()
        else:
            self._scroll_timer.stop()

    def _start_hover_anim(self, target):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_fade)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    def _start_close_anim(self, target):
        self._close_anim.stop()
        self._close_anim.setStartValue(self._close_fade)
        self._close_anim.setEndValue(target)
        self._close_anim.start()

    def _move_tab(self, fr, to):
        if fr == to:
            return
        tab = self._tabs.pop(fr)
        self._tabs.insert(to, tab)
        if self._current == fr:
            self._current = to
        elif fr < self._current <= to:
            self._current -= 1
        elif to <= self._current < fr:
            self._current += 1
        self._recalc()
        self.tabMoved.emit(fr, to)

    # ── Painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        t = self._theme()
        c = t.colors
        h = self.height()
        w = self.width()
        r = self.TAB_RADIUS

        painter.fillRect(0, 0, w, h, QColor(c.tab_bar_bg))
        painter.fillRect(0, h - self.BTM_DIVIDER, w, self.BTM_DIVIDER, QColor(c.divider))

        if self._has_overflow:
            self._draw_scroll_buttons(painter, t, c)

        for i in range(len(self._tabs)):
            if i != self._current:
                self._draw_tab(painter, i, False, t, c, r)

        if 0 <= self._current < len(self._tabs):
            self._draw_tab(painter, self._current, True, t, c, r)

        self._draw_new_tab_button(painter, t, c)

        painter.end()

    def _draw_scroll_buttons(self, painter, t, c):
        lr = self._scroll_left_rect()
        rr = self._scroll_right_rect()

        for rect, hovered, is_left in [(lr, self._hover_scroll_left, True), (rr, self._hover_scroll_right, False)]:
            bg = QColor(c.surface_hover)
            bg.setAlphaF(bg.alphaF() * (0.7 if hovered else 0.0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(bg))
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect), 5, 5)
            painter.drawPath(path)
            col = QColor(c.text)
            col.setAlphaF(col.alphaF() * (0.7 if hovered else 0.35))
            pen = QPen(col, 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            cx, cy = rect.center().x(), rect.center().y()
            s = 4.0
            if is_left:
                painter.drawLine(QPointF(cx + s, cy - s), QPointF(cx - s, cy))
                painter.drawLine(QPointF(cx + s, cy + s), QPointF(cx - s, cy))
            else:
                painter.drawLine(QPointF(cx - s, cy - s), QPointF(cx + s, cy))
                painter.drawLine(QPointF(cx - s, cy + s), QPointF(cx + s, cy))

    def _draw_new_tab_button(self, painter, t, c):
        rect = self._new_tab_rect()
        bg = QColor(c.surface_hover)
        bg.setAlphaF(bg.alphaF() * (0.8 if self._hover_new_tab else 0.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg))
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 6, 6)
        painter.drawPath(path)
        col = QColor(c.text)
        col.setAlphaF(col.alphaF() * (0.9 if self._hover_new_tab else 0.45))
        pen = QPen(col, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = rect.center().x(), rect.center().y()
        s = 5
        painter.drawLine(QPointF(cx - s, cy), QPointF(cx + s, cy))
        painter.drawLine(QPointF(cx, cy - s), QPointF(cx, cy + s))

    def _draw_tab(self, painter, index, is_active, t, c, r):
        tr = self._tab_rect_at(index)
        if tr.isNull():
            return

        data = self._tabs[index]
        is_hovered = index == self._hovered and not is_active

        if is_active:
            fill_rect = QRectF(tr).adjusted(0, 0, 0, self.BTM_DIVIDER + 1)
            path = QPainterPath()
            path.addRoundedRect(fill_rect, r, r)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(path, QColor(c.tab_active))

            gradient = QLinearGradient(0, tr.top(), 0, tr.bottom())
            highlight = QColor(c.tab_active).lighter(108)
            highlight.setAlphaF(0.35)
            gradient.setColorAt(0.0, highlight)
            gradient.setColorAt(1.0, QColor(c.tab_active))
            painter.fillPath(path, QBrush(gradient))

            x, y, x2, y2 = tr.x(), tr.y(), tr.right(), tr.bottom()
            rr = min(r, tr.width() / 2.0, tr.height() / 2.0)
            bp = QPainterPath()
            bp.moveTo(x, y2)
            bp.lineTo(x, y + rr)
            bp.quadTo(x, y, x + rr, y)
            bp.lineTo(x2 - rr, y)
            bp.quadTo(x2, y, x2, y + rr)
            bp.lineTo(x2, y2)
            painter.setPen(QPen(QColor(c.tab_active_border), 0.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(bp)
        else:
            fill_rect = QRectF(tr)
            path = QPainterPath()
            path.addRoundedRect(fill_rect, r, r)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(path, QColor(c.tab_inactive_bg))

            if is_hovered and self._hover_fade > 0.01:
                hc = QColor(c.tab_hover)
                hc.setAlphaF(hc.alphaF() * self._hover_fade)
                painter.fillPath(path, hc)

            if index + 1 < len(self._tabs) and index + 1 != self._current:
                x = tr.right()
                dc = QColor(c.text)
                dc.setAlphaF(0.12)
                painter.setPen(QPen(dc, 0.5))
                painter.drawLine(QPointF(x, tr.top() + 4), QPointF(x, tr.bottom() - 4))

        fr = self._favicon_rect(index)
        progress = data.get("progress", -1)

        if 0 <= progress < 100:
            self._draw_spinner(painter, fr, progress, t, c)
        elif data["icon"] is not None and not data["icon"].isNull():
            pixmap = data["icon"].pixmap(self.FAVICON_SIZE, self.FAVICON_SIZE)
            if not pixmap.isNull():
                painter.drawPixmap(fr, pixmap)

        if data.get("is_private"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(c.lock_secure)))
            painter.drawEllipse(QRectF(fr).adjusted(4, 4, -4, -4))

        text_x = fr.right() + self.FAV_GAP
        if data.get("is_playing"):
            audio_r = QRect(text_x, tr.y() + (tr.height() - self.AUDIO_SIZE) // 2,
                            self.AUDIO_SIZE, self.AUDIO_SIZE)
            self._draw_audio_indicator(painter, audio_r, t, c)
            text_x = audio_r.right() + self.FAV_GAP

        show_close = is_active or index == self._hovered or (self._hovered_close >= 0 and index == self._hovered_close)
        font = self._tab_font(is_active)
        painter.setFont(font)
        painter.setPen(QColor(c.text if is_active else c.text_secondary))

        text_x = fr.right() + self.FAV_GAP
        text_right = (self._close_rect(index).left() - self.CLOSE_GAP) if show_close else (tr.right() - self.RIGHT_PAD)
        text_w = max(0, text_right - text_x)

        if text_w > 0:
            text_rect = QRect(int(text_x), tr.top() + 1, int(text_w), tr.height() - 2)
            title = data["title"]
            if title:
                fm = QFontMetrics(font)
                elided = fm.elidedText(title, Qt.TextElideMode.ElideRight, text_w)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided or "")

        if show_close:
            self._draw_close(painter, index, t, c)

    def _draw_audio_indicator(self, painter, rect, t, c):
        cx, cy = rect.center().x(), rect.center().y()
        s = 4.0
        col = QColor(c.text)
        col.setAlphaF(0.7)
        pen = QPen(col, 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        speaker = QPainterPath()
        speaker.moveTo(cx - s + 1, cy - 1.5)
        speaker.lineTo(cx - 1, cy - 1.5)
        speaker.lineTo(cx + 1.5, cy - s + 1)
        speaker.lineTo(cx + 1.5, cy + s - 1)
        speaker.lineTo(cx - 1, cy + 1.5)
        speaker.lineTo(cx - s + 1, cy + 1.5)
        speaker.closeSubpath()
        painter.drawPath(speaker)

        painter.drawArc(QRectF(cx + 1.5, cy - s + 1, s - 1, (s - 1) * 2), -60 * 16, 120 * 16)
        painter.drawArc(QRectF(cx + 1.5 + 2, cy - s + 1 - 1, s - 1, (s - 1) * 2 + 2), -60 * 16, 120 * 16)

    def _draw_spinner(self, painter, rect, progress, t, c):
        cx, cy = rect.center().x(), rect.center().y()
        radius = 7.0
        pen = QPen(QColor(c.accent), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        start_angle = (self._spinner_angle - 90) * 16
        span = int(max(1, min(270, progress * 3.6 * 16)))
        painter.drawArc(QRectF(cx - radius, cy - radius, radius * 2, radius * 2),
                        start_angle, span)

    def _draw_close(self, painter, index, t, c):
        rect = self._close_rect(index)
        cx, cy = rect.center().x(), rect.center().y()
        is_hover = index == self._hovered_close
        is_pressed = index == self._pressed_close
        is_active = index == self._current

        if is_hover or is_pressed:
            fade = self._close_fade if not is_pressed else 1.0
            bg = QColor(c.surface_hover if not is_pressed else c.surface_pressed)
            bg.setAlphaF(bg.alphaF() * fade)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(bg))
            painter.drawEllipse(QPointF(cx, cy), 8.0, 8.0)

        col = QColor(c.text)
        if is_hover:
            fade = max(self._close_fade, 0.15)
            alpha = 0.5 + (1.0 - 0.5) * fade
        elif is_active:
            alpha = 0.35
        else:
            alpha = 0.12
        col.setAlphaF(alpha)
        if alpha > 0.01:
            pen = QPen(col, 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            s = 3.2
            painter.drawLine(QPointF(cx - s, cy - s), QPointF(cx + s, cy + s))
            painter.drawLine(QPointF(cx + s, cy - s), QPointF(cx - s, cy + s))


# ── ChromeButton ─────────────────────────────────────────────────────
class ChromeButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self.setMinimumWidth(28)

    def setIconText(self, text):
        self.setText(text)
        font = self.font()
        font.setPixelSize(14)
        self.setFont(font)


# ── AdBlockButton ────────────────────────────────────────────────────
class AdBlockButton(QPushButton):
    """Custom adblock shield button — draws a crisp shield icon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(ChromeBar.BTN_SIZE, ChromeBar.BTN_SIZE)
        self._count = 0
        self.setMouseTracking(True)

        # ── Hover animation ──
        self._hover_progress = 0.0
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

    def setCount(self, n):
        self._count = n
        self.update()

    def _theme(self):
        win = self.window()
        if hasattr(win, 'theme'):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        t = self._theme()
        c = t.colors
        hp = self._hover_progress

        # Background circle on hover — animated
        if hp > 0.01:
            hover_bg = QColor(c.surface_hover)
            hover_bg.setAlphaF(hover_bg.alphaF() * hp)
            hover_path = QPainterPath()
            hover_path.addRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 6, 6)
            painter.fillPath(hover_path, hover_bg)

        # Draw shield
        cx = self.width() / 2.0
        cy = self.height() / 2.0 + 1.0
        s = 8.0

        # Smooth shield outline — curved sides with top dip and bottom point
        shield = QPainterPath()
        shield.moveTo(cx, cy - s + 2)
        shield.quadTo(cx - s + 2, cy - s + 0.5, cx - s, cy - s + 2)
        shield.quadTo(cx - s - 1.5, cy, cx, cy + s)
        shield.quadTo(cx + s + 1.5, cy, cx + s, cy - s + 2)
        shield.quadTo(cx + s - 2, cy - s + 0.5, cx, cy - s + 2)
        shield.closeSubpath()

        # Color: greenish when blocking, neutral when idle
        if self._count > 0:
            color = QColor(52, 199, 89)
        else:
            color = QColor(c.text_secondary)

        pen = QPen(color, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(shield)

        # Checkmark inside shield when blocking
        if self._count > 0:
            painter.setPen(QPen(QColor(52, 199, 89), 1.5))
            check = QPainterPath()
            check.moveTo(cx - 2, cy + 0.5)
            check.lineTo(cx - 0.5, cy + 2)
            check.lineTo(cx + 2.5, cy - 1.5)
            painter.drawPath(check)

        # Count badge
        if self._count > 0:
            badge_text = str(self._count) if self._count < 100 else "99+"
            font = QFont("Segoe UI" if platform.system() == "Windows" else "SF Pro Display")
            font.setPixelSize(8)
            font.setWeight(QFont.Weight.Bold)
            painter.setFont(font)
            painter.setPen(QColor(c.text_tertiary))
            painter.drawText(QRectF(cx + s - 2, cy - s - 1, 14, 10),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             badge_text)

        painter.end()


# ── NavButton ────────────────────────────────────────────────────────
class NavButton(QPushButton):
    """Navigation button (back/forward/reload/home) with smooth circular hover."""

    BTN_SIZE = 28

    def __init__(self, direction, parent=None):
        super().__init__(parent)
        self._direction = direction
        self.setFixedSize(self.BTN_SIZE, self.BTN_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_enabled = True
        self.setMouseTracking(True)

        # ── Hover animation ──
        self._hover_progress = 0.0
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

    def setNavigationEnabled(self, enabled):
        if self._nav_enabled != enabled:
            self._nav_enabled = enabled
            self.update()

    def _theme(self):
        win = self.window()
        if hasattr(win, 'theme'):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        t = self._theme()
        c = t.colors

        is_pressed = self.isDown() and self._nav_enabled
        hp = self._hover_progress  # 0.0 → 1.0 when hovering

        # Circular hover/pressed background
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
            circle_path = QPainterPath()
            circle_path.addEllipse(QRectF(cx - circle_r, cy - circle_r, circle_r * 2, circle_r * 2))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(bg))
            painter.drawPath(circle_path)

        # Icon color — lerp between text and accent based on hover + press
        if not self._nav_enabled:
            icon_color = QColor(c.text_muted)
        elif is_pressed:
            icon_color = QColor(c.accent)
        else:
            # Lerp text → accent by hover progress
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

        self._draw_icon(painter, cx, cy)

        painter.end()

    def _draw_icon(self, painter, cx, cy):
        d = self._direction
        s = 4.5

        if d == 'back':
            path = QPainterPath()
            path.moveTo(cx + 2.5, cy - s)
            path.lineTo(cx - s + 2.5, cy)
            path.lineTo(cx + 2.5, cy + s)
            painter.drawPath(path)

        elif d == 'forward':
            path = QPainterPath()
            path.moveTo(cx - 2.5, cy - s)
            path.lineTo(cx + s - 2.5, cy)
            path.lineTo(cx - 2.5, cy + s)
            painter.drawPath(path)

        elif d == 'reload':
            r = s - 0.5
            arc_path = QPainterPath()
            arc_path.arcMoveTo(cx - r, cy - r, r * 2, r * 2, 55)
            arc_path.arcTo(cx - r, cy - r, r * 2, r * 2, 55, 275)
            painter.drawPath(arc_path)
            # Arrowhead
            a = 3.0
            tip_angle = 55.0
            import math
            rad = math.radians(tip_angle)
            ax = cx + r * math.cos(rad)
            ay = cy - r * math.sin(rad)
            arrow = QPainterPath()
            arrow.moveTo(ax - a * 0.6, ay - a)
            arrow.lineTo(ax + a * 0.8, ay + a * 0.2)
            arrow.lineTo(ax - a * 0.4, ay + a * 0.9)
            painter.drawPath(arrow)

        elif d == 'home':
            # Roof
            roof = QPainterPath()
            roof.moveTo(cx, cy - s + 0.5)
            roof.lineTo(cx - s + 0.5, cy)
            roof.lineTo(cx + s - 0.5, cy)
            roof.closeSubpath()
            painter.drawPath(roof)
            # Walls and door
            body = QPainterPath()
            body.moveTo(cx - s + 2.5, cy)
            body.lineTo(cx - s + 2.5, cy + s - 0.5)
            body.lineTo(cx + s - 2.5, cy + s - 0.5)
            body.lineTo(cx + s - 2.5, cy)
            painter.drawPath(body)
            # Door
            door = QPainterPath()
            door.moveTo(cx - 1.2, cy + s - 0.5)
            door.lineTo(cx - 1.2, cy + 1.5)
            door.arcTo(QRectF(cx - 1.2, cy + 1.5 - 1.2, 2.4, 2.4), 180, -180)
            door.lineTo(cx + 1.2, cy + s - 0.5)
            painter.drawPath(door)


# ── SuggestionDelegate ──────────────────────────────────────────────
class SuggestionDelegate(QStyledItemDelegate):
    """Paints suggestion items with a magnifying-glass icon via QPainter."""

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            painter.restore()
            return

        # Strip leading icon prefix if present
        display = text
        if display and len(display) > 2 and display[0] in ('\u2315', '\U0001f50d'):
            display = display[2:] if display[1:2] == ' ' else display[1:]

        # Selection/hover background
        if option.state & QStyle.StateFlag.State_MouseOver or option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(option.palette.color(QPalette.ColorRole.Highlight)))
            painter.drawRoundedRect(QRectF(option.rect).adjusted(4, 2, -4, -2), 6, 6)

        # Magnifying glass icon
        icon_color = option.palette.color(QPalette.ColorRole.Text)
        icon_color.setAlpha(160)
        pen = QPen(icon_color, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        cx = option.rect.x() + 16
        cy = option.rect.center().y()
        circle = QRectF(cx - 4, cy - 4, 7.5, 7.5)
        painter.drawEllipse(circle)
        painter.drawLine(QPointF(cx + 2, cy + 2), QPointF(cx + 5.5, cy + 5.5))

        # Text
        painter.setPen(option.palette.color(QPalette.ColorRole.Text))
        text_rect = option.rect.adjusted(30, 0, -8, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(0, 32)


# ── SearchSuggestionPopup ────────────────────────────────────────────
class SearchSuggestionPopup(QFrame):
    """Dropdown suggestion list anchored below the address bar."""

    suggestionSelected = Signal(str)

    def __init__(self, address_bar):
        super().__init__(address_bar.window())
        self.address_bar = address_bar
        self._items = []
        self._selected_index = -1

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Popup
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(0)

        self.list_widget = QListWidget()
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_widget.setMouseTracking(True)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemEntered.connect(self._on_item_entered)
        self.list_widget.setItemDelegate(SuggestionDelegate(self.list_widget))
        layout.addWidget(self.list_widget)

        # Shadow for depth (avoids "sharp" flat look)
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(24)
        self._shadow.setOffset(0, 4)
        self._shadow.setColor(QColor(0, 0, 0, 100))
        self.setGraphicsEffect(self._shadow)

        # Theme application
        self._theme_applied = False

    def _theme(self):
        win = self.window()
        if hasattr(win, 'theme'):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def apply_theme(self, theme):
        c = theme.colors
        is_dark = theme.mode == ThemeMode.DARK
        self.setStyleSheet(f"""
            SearchSuggestionPopup {{
                background-color: {c.suggestion_bg};
                border: 1px solid {c.border};
                border-radius: 12px;
            }}
            QListWidget {{
                background-color: transparent;
                color: {c.suggestion_text};
                border: none;
                outline: none;
                font-size: 13px;
                padding: 4px;
            }}
        """)
        # Set palette for the delegate (highlight = hover/selected bg)
        pal = self.list_widget.palette()
        pal.setColor(QPalette.ColorRole.Highlight, QColor(c.suggestion_hover))
        pal.setColor(QPalette.ColorRole.Text, QColor(c.suggestion_text))
        pal.setColor(QPalette.ColorRole.Base, QColor(c.suggestion_bg))
        self.list_widget.setPalette(pal)
        # Update shadow color for depth
        self._shadow.setColor(QColor(0, 0, 0, 80) if is_dark else QColor(0, 0, 0, 40))
        self._theme_applied = True

    def show_suggestions(self, suggestions):
        self.list_widget.clear()
        self._items = suggestions
        self._selected_index = -1
        if not suggestions:
            self.hide()
            return
        for s in suggestions:
            item = QListWidgetItem(s)
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.list_widget.addItem(item)
        self._position_and_show()

    def _position_and_show(self):
        bar = self.address_bar
        # Position below the address bar, aligned to its left edge
        bar_rect = bar.rect()
        global_bottom_left = bar.mapToGlobal(bar_rect.bottomLeft())
        # Offset to account for border/padding
        x = global_bottom_left.x()
        y = global_bottom_left.y() + 2
        width = bar_rect.width()
        # Height capped at ~240px (about 8 suggestions)
        item_height = 36
        height = min(len(self._items) * item_height + 12, 240)
        self.setGeometry(x, y, width, height)
        self.show()
        self.raise_()

    def _on_item_clicked(self, item):
        text = item.data(Qt.ItemDataRole.UserRole)
        if text:
            self.suggestionSelected.emit(text)
            self.hide()

    def _on_item_entered(self, item):
        self.list_widget.setCurrentItem(item)

    def select_next(self):
        count = self.list_widget.count()
        if count == 0:
            return
        idx = (self._selected_index + 1) % count
        self._selected_index = idx
        self.list_widget.setCurrentRow(idx)

    def select_previous(self):
        count = self.list_widget.count()
        if count == 0:
            return
        idx = (self._selected_index - 1) if self._selected_index > 0 else (count - 1)
        self._selected_index = idx
        self.list_widget.setCurrentRow(idx)

    def get_selected_text(self):
        if self._selected_index >= 0 and self._selected_index < len(self._items):
            return self._items[self._selected_index]
        return None

    def is_visible(self):
        return self.isVisible()

    def hideEvent(self, event):
        self._selected_index = -1
        super().hideEvent(event)


# ── AddressBar ───────────────────────────────────────────────────────
class AddressBar(QLineEdit):
    """Address bar with an inline security lock icon and search suggestions."""

    focused = Signal()
    blurred = Signal()
    suggestionRequested = Signal(str)

    LOCK_AREA = 20   # pixels reserved on left for lock icon

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrame(False)
        self.setMinimumHeight(32)
        self.setMaximumHeight(32)
        self.setClearButtonEnabled(False)
        self._has_focus = False
        self._is_secure = True     # default to secure (HTTPS-only mode)
        self._is_local = False
        self._url = ""              # full URL for colored rendering
        self._scheme = ""           # e.g. "https://"
        self._rest = ""             # e.g. "example.com/page"
        # Push text right for lock icon, left for padding (no clear button, so right is minimal)
        self.setTextMargins(self.LOCK_AREA, 0, 14, 0)

        # ── Focus animation ──
        self._focus_progress = 0.0   # 0.0 = unfocused, 1.0 = focused
        self._focus_anim = QVariantAnimation(self)
        self._focus_anim.setDuration(180)
        self._focus_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._focus_anim.valueChanged.connect(self._on_focus_progress)

        # Suggestion popup
        self._popup = SearchSuggestionPopup(self)
        self._popup.suggestionSelected.connect(self._on_suggestion_selected)

        # Network manager for fetching suggestions
        self._network = QNetworkAccessManager(self)
        self._network.finished.connect(self._on_suggestions_received)

        # Debounce timer
        self._suggest_timer = QTimer(self)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.setInterval(180)  # ms debounce
        self._suggest_timer.timeout.connect(self._fetch_suggestions)

        # Connect text changes
        self.textChanged.connect(self._on_text_changed)

    def _on_focus_progress(self, value):
        self._focus_progress = value
        self.update()

    def _start_focus_anim(self, entering):
        self._focus_anim.stop()
        self._focus_anim.setStartValue(self._focus_progress)
        self._focus_anim.setEndValue(1.0 if entering else 0.0)
        self._focus_anim.start()

    # ── Brave-style URL rendering ────────────────────────────────────

    def setText(self, text):
        """Override setText to store parsed URL for colored rendering."""
        super().setText(text)
        self._url = text or ""
        self._parse_url(self._url)

    def _parse_url(self, url):
        """Extract the scheme (e.g. 'https://') from the URL."""
        idx = url.find("://")
        if idx > 0:
            self._scheme = url[:idx + 3]
            self._rest = url[idx + 3:]
        else:
            self._scheme = ""
            self._rest = url

    def set_url_security(self, url_str):
        lower = url_str.lower()
        old_secure = self._is_secure
        old_local = self._is_local
        self._is_local = lower.startswith("file://")
        self._is_secure = lower.startswith("https://") and bool(url_str)
        if old_secure != self._is_secure or old_local != self._is_local:
            self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._has_focus = True
        self.focused.emit()
        self._start_focus_anim(True)

    def _theme(self):
        win = self.window()
        if hasattr(win, 'theme'):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def _lerp_color(self, a, b, t):
        return QColor(
            int(a.red() + (b.red() - a.red()) * t),
            int(a.green() + (b.green() - a.green()) * t),
            int(a.blue() + (b.blue() - a.blue()) * t),
            int(a.alpha() + (b.alpha() - a.alpha()) * t),
        )

    def paintEvent(self, event):
        t = self._theme()
        c = t.colors
        fp = self._focus_progress

        # 1. Draw pill background + border with focus glow
        bg_painter = QPainter(self)
        bg_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        r = min(rect.height() / 2.0, t.radius_full)

        # Subtle focus glow — accent-colored
        if fp > 0.01:
            glow_rect = QRectF(self.rect()).adjusted(-1, -1, 1, 1)
            glow_path = QPainterPath()
            glow_path.addRoundedRect(glow_rect, r + 2, r + 2)
            glow_color = QColor(c.accent)
            glow_color.setAlphaF(0.10 * fp)
            bg_painter.setPen(Qt.PenStyle.NoPen)
            bg_painter.setBrush(QBrush(glow_color))
            bg_painter.drawPath(glow_path)

        # Pill background
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)

        bg_color = self._lerp_color(QColor(c.input_bg), QColor(c.surface_hover), fp)
        border_color = self._lerp_color(QColor(c.border_hover), QColor(c.border_focus), fp)
        border_w = 1.0 + 0.5 * fp  # border thickens subtly on focus

        bg_painter.setPen(Qt.PenStyle.NoPen)
        bg_painter.setBrush(QBrush(bg_color))
        bg_painter.drawPath(path)

        bg_painter.setPen(QPen(border_color, border_w))
        bg_painter.setBrush(Qt.BrushStyle.NoBrush)
        bg_painter.drawPath(path)
        bg_painter.end()

        # 2. Draw text — Brave-style: green scheme when unfocused
        if self._has_focus or not self._scheme:
            # Focused or no scheme: let Qt render text normally
            super().paintEvent(event)
        else:
            # Unfocused with scheme: draw manually with colored scheme
            self._draw_colored_url()

    def _draw_colored_url(self):
        """Draw the URL with the scheme part in green (Brave-style)."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        t = self._theme()
        c = t.colors

        # Text rect from QLineEdit's text layout
        margins = self.textMargins()
        # Add left offset for lock icon area
        text_rect = QRectF(
            self.rect().left() + margins.left(),
            self.rect().top() + margins.top(),
            self.width() - margins.left() - margins.right(),
            self.height() - margins.top() - margins.bottom()
        )

        font = self.font()
        painter.setFont(font)
        fm = QFontMetrics(font)

        scheme_w = fm.horizontalAdvance(self._scheme)

        # Clip to prevent overflow
        painter.save()
        painter.setClipRect(text_rect)

        # Draw scheme in green (secure) or orange (insecure)
        scheme_rect = QRectF(text_rect.left(), text_rect.top(),
                             min(scheme_w, text_rect.width()), text_rect.height())
        if self._is_secure:
            scheme_color = QColor(52, 199, 89)  # Brave-style green
        else:
            scheme_color = QColor(c.lock_insecure)
        painter.setPen(scheme_color)
        painter.drawText(scheme_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._scheme)

        # Draw rest in normal text color
        if scheme_w < text_rect.width():
            rest_rect = QRectF(text_rect.left() + scheme_w, text_rect.top(),
                               text_rect.width() - scheme_w, text_rect.height())
            painter.setPen(QColor(c.text))
            painter.drawText(rest_rect,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             self._rest)

        painter.restore()
        painter.end()

    # ── Suggestion methods ────────────────────────────────────────────

    def _on_text_changed(self, text):
        if not text.strip():
            self._popup.hide()
            return
        if len(text) < 2:
            return
        self._suggest_timer.start()

    def _fetch_suggestions(self):
        text = self.text().strip()
        if len(text) < 2:
            return
        url = f"https://duckduckgo.com/ac/?q={QUrl.toPercentEncoding(text).data().decode()}&type=list"
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        self._network.get(req)

    def _on_suggestions_received(self, reply):
        if reply.error() != QNetworkReply.NetworkError.NoError:
            return
        try:
            data = reply.readAll().data().decode("utf-8")
            import json
            results = json.loads(data)
            suggestions = [item.get("phrase", "") for item in results if item.get("phrase")]
            suggestions = suggestions[:10]  # max 10 suggestions
            self._popup.show_suggestions(suggestions)
        except Exception:
            pass

    def _on_suggestion_selected(self, text):
        self.setText(text)
        self._popup.hide()
        # Trigger navigation via returnPressed signal (which ChromeBar is connected to)
        self.returnPressed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Down:
            if self._popup.is_visible():
                self._popup.select_next()
                event.accept()
                return
        elif event.key() == Qt.Key.Key_Up:
            if self._popup.is_visible():
                self._popup.select_previous()
                event.accept()
                return
        elif event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if self._popup.is_visible():
                selected = self._popup.get_selected_text()
                if selected:
                    self._on_suggestion_selected(selected)
                    event.accept()
                    return
            # Normal return handling (navigate)
            self._popup.hide()
        elif event.key() == Qt.Key.Key_Escape:
            if self._popup.is_visible():
                self._popup.hide()
                event.accept()
                return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._has_focus = False
        self.blurred.emit()
        self._start_focus_anim(False)
        self.update()
        # Hide popup on focus loss (with small delay so clicks register)
        QTimer.singleShot(150, self._popup.hide)

    def apply_suggestion_theme(self, theme):
        self._popup.apply_theme(theme)


# ── ChromeBar ────────────────────────────────────────────────────────
class ChromeBar(QWidget):
    """The top chrome: tab strip + navigation toolbar."""

    newTabRequested = Signal()
    urlSubmitted = Signal(str)
    backRequested = Signal()
    forwardRequested = Signal()
    reloadRequested = Signal()
    homeRequested = Signal()
    addTabRequested = Signal()

    # ── Layout constants ─────────────────────────────────────────────
    BTN_SIZE = 28             # unified toolbar button size
    TAB_STRIP_HEIGHT = 30     # compact tab strip (matches theme tab_height)
    STRIP_BTN_SIZE = 26       # "+" button in tab strip
    TOOLBAR_MARGIN = 2        # vertical padding inside toolbar
    NAV_SPACING = 4           # gap between nav cluster and address bar
    TOOLBAR_PADDING = 6       # horizontal edge padding

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChromeBar")
        self._build()

    def _build(self):
        """Top-level chrome assembly: tabs strip + toolbar + bottom divider."""
        self._build_tabs_strip()
        self._build_toolbar()

        # Bottom visual divider between chrome and web content
        self.chrome_divider = QFrame()
        self.chrome_divider.setObjectName("ChromeDivider")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.tabs_strip)
        main_layout.addWidget(self.toolbar)
        main_layout.addWidget(self.chrome_divider)

    # ── Tab strip construction ────────────────────────────────────
    def _build_tabs_strip(self):
        self.tabs_strip = QWidget()
        self.tabs_strip.setObjectName("TabsStrip")
        self.tabs_strip.setFixedHeight(self.TAB_STRIP_HEIGHT)
        tabs_layout = QHBoxLayout(self.tabs_strip)
        tabs_layout.setContentsMargins(0, 0, 0, 0)
        tabs_layout.setSpacing(0)

        # New-tab "+" button (created here, wired to tab bar in addTabBar)
        self.add_tab_strip_btn = QPushButton("+")
        self.add_tab_strip_btn.setObjectName("AddTabStripBtn")
        self.add_tab_strip_btn.setToolTip("New Tab (Ctrl+T)")
        self.add_tab_strip_btn.setFixedSize(self.STRIP_BTN_SIZE, self.STRIP_BTN_SIZE)
        self.add_tab_strip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_tab_strip_btn.clicked.connect(self.addTabRequested)

        self.tab_bar_container = QWidget()
        self.tab_bar_container.setObjectName("TabBarContainer")
        self.tab_bar_layout = QHBoxLayout(self.tab_bar_container)
        self.tab_bar_layout.setContentsMargins(0, 0, 0, 0)
        self.tab_bar_layout.setSpacing(0)

        # New-tab "+" button: sits in tab_bar_layout after tabs (wired via insertWidget)
        self.tab_bar_layout.addWidget(self.add_tab_strip_btn)
        self.tab_bar_layout.addStretch(1)

        tabs_layout.addWidget(self.tab_bar_container, 1)

        # Tab count label (visible when >15 tabs for overflow awareness)
        self.tab_count_label = QLabel()
        self.tab_count_label.setObjectName("TabCountLabel")
        self.tab_count_label.setVisible(False)
        tabs_layout.addWidget(self.tab_count_label, 0, Qt.AlignmentFlag.AlignCenter)

    # ── Toolbar construction ──────────────────────────────────────
    def _build_toolbar(self):
        self.toolbar = QWidget()
        self.toolbar.setObjectName("Toolbar")
        tbm = self.TOOLBAR_MARGIN
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(self.TOOLBAR_PADDING, tbm, self.TOOLBAR_PADDING, tbm)
        toolbar_layout.setSpacing(0)

        self._add_nav_buttons(toolbar_layout)
        toolbar_layout.addSpacing(self.NAV_SPACING)
        self._add_address_bar(toolbar_layout)
        toolbar_layout.addSpacing(self.NAV_SPACING)
        self._add_home_button(toolbar_layout)

    def _add_nav_buttons(self, layout):
        self.back_btn = NavButton('back')
        self.back_btn.setToolTip("Back (Alt+\u2190)")
        self.back_btn.clicked.connect(self.backRequested)
        layout.addWidget(self.back_btn)

        self.forward_btn = NavButton('forward')
        self.forward_btn.setToolTip("Forward (Alt+\u2192)")
        self.forward_btn.clicked.connect(self.forwardRequested)
        layout.addWidget(self.forward_btn)

        self.reload_btn = NavButton('reload')
        self.reload_btn.setToolTip("Reload (F5)")
        self.reload_btn.clicked.connect(self.reloadRequested)
        layout.addWidget(self.reload_btn)

    def _add_address_bar(self, layout):
        self.address_bar = AddressBar()
        self.address_bar.setPlaceholderText("Search or enter address")
        self.address_bar.returnPressed.connect(self._on_url_submit)
        layout.addWidget(self.address_bar, 1)

    def _add_home_button(self, layout):
        self.home_btn = NavButton('home')
        self.home_btn.setToolTip("Home")
        self.home_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.home_btn.clicked.connect(self.homeRequested)
        layout.addWidget(self.home_btn)

    def addTabBar(self, tab_bar):
        self.tab_bar_layout.insertWidget(0, tab_bar)

    def _on_url_submit(self):
        text = self.address_bar.text().strip()
        if text:
            self.urlSubmitted.emit(text)
            self.address_bar.clearFocus()

    def setUrl(self, url):
        self.address_bar.setText(url)
        self.address_bar.set_url_security(url)
        if url:
            self.address_bar.clearFocus()

    def setNavigationState(self, can_back, can_forward):
        self.back_btn.setNavigationEnabled(can_back)
        self.forward_btn.setNavigationEnabled(can_forward)

    def setTabCount(self, n):
        """Show tab count when overflow threshold is reached."""
        if n > 15:
            self.tab_count_label.setText(f"{n} tabs")
            self.tab_count_label.setVisible(True)
        else:
            self.tab_count_label.setVisible(False)

    def apply_theme(self, theme):
        c = theme.colors
        is_dark = theme.mode == ThemeMode.DARK
        font = theme.font_family

        self.setStyleSheet(f"""
            QWidget#ChromeBar {{
                background-color: {c.toolbar_bg};
            }}
            QWidget#TabsStrip {{
                background-color: {c.tab_bar_bg};
                border: none;
            }}
            QWidget#TabBarContainer {{
                background-color: transparent;
            }}
            QWidget#Toolbar {{
                background-color: {c.toolbar_bg};
                border: none;
            }}
            QFrame#ChromeDivider {{
                background-color: {c.divider};
                border: none;
                max-height: 1px;
                min-height: 1px;
            }}
            QLabel#TabCountLabel {{
                color: {c.text_tertiary};
                font-size: 11px;
                font-weight: 500;
                padding: 0 4px;
                background: transparent;
            }}
            QPushButton#AddTabStripBtn {{
                background-color: transparent;
                color: {c.text_tertiary};
                border: none;
                border-radius: 6px;
                font-size: 18px;
                font-weight: 400;
                padding: 0;
                margin: 0;
            }}
            QPushButton#AddTabStripBtn:hover {{
                color: {c.text};
                background-color: {c.surface_hover};
            }}
            QPushButton#AddTabStripBtn:pressed {{
                background-color: {c.surface_pressed};
            }}
            QLineEdit {{
                background-color: transparent;
                color: {c.text};
                border: none;
                font-family: 'Segoe UI', 'SF Pro Text', {font};
                font-size: 13px;
                font-weight: 400;
                selection-background-color: {c.accent_muted};
            }}
            QLineEdit:focus {{
                background-color: transparent;
                border: none;
            }}
            QLineEdit::placeholder {{
                color: {c.text_tertiary};
            }}
        """)

        # Propagate theme to suggestion popup
        self.address_bar.apply_suggestion_theme(theme)


# ── TrafficLightButton ───────────────────────────────────────────────
class TrafficLightButton(QPushButton):
    """A single Mac OS traffic light dot (close/minimize/maximize)."""

    COLORS = {
        "close":    {"normal": "#ff5f57", "hover": "#ff3b30", "icon": "\u2715"},
        "minimize": {"normal": "#febc2e", "hover": "#f5a623", "icon": "\u2212"},
        "maximize": {"normal": "#28c840", "hover": "#34c759", "icon": "\u2295"},
        "restore":  {"normal": "#28c840", "hover": "#34c759", "icon": "\u25A1"},
    }

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self._action = action
        self._colors = self.COLORS[action]
        self.setFixedSize(12, 12)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip({
            "close": "Close",
            "minimize": "Minimize",
            "maximize": "Maximize",
        }[action])
        self.setMouseTracking(True)

        # ── Hover animation ──
        self._hover_progress = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(180)
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

    @staticmethod
    def _lerp_color(a, b, t):
        return QColor(
            int(a.red() + (b.red() - a.red()) * t),
            int(a.green() + (b.green() - a.green()) * t),
            int(a.blue() + (b.blue() - a.blue()) * t),
            int(a.alpha() + (b.alpha() - a.alpha()) * t),
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        hp = self._hover_progress  # 0.0 → 1.0 when hovering

        # Lerp between normal and hover color
        normal = QColor(self._colors["normal"])
        hover = QColor(self._colors["hover"])
        color = self._lerp_color(normal, hover, hp)

        # Draw filled circle
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))

        # Draw icon — fades in smoothly on hover
        if hp > 0.01:
            icon_alpha = min(255, int(80 + (175 * hp)))
            painter.setPen(QPen(QColor(0, 0, 0, icon_alpha), 1.2))
            f = QFont("Segoe UI", 7)
            f.setWeight(QFont.Weight.Bold)
            f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            painter.setFont(f)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._colors["icon"])

        painter.end()


# ── TitleBar ─────────────────────────────────────────────────────────
class TitleBar(QWidget):
    """Mac OS-style title bar with traffic light controls and page title."""

    TITLE_HEIGHT = 34

    PROFILE_PILL_HEIGHT = 18  # height of profile indicator pill

    def __init__(self, browser_window):
        super().__init__(browser_window)
        self._bw = browser_window
        self._drag_pos = None
        self._is_dark = True
        self.setFixedHeight(self.TITLE_HEIGHT)
        self.setObjectName("TitleBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(0)

        # ── Left: profile indicator pill ──
        profile_id = self._bw.identity_manager.get_current_profile_id()
        short_id = profile_id[-8:] if profile_id else "?"
        self.profile_pill = QPushButton(short_id)
        self.profile_pill.setObjectName("ProfilePill")
        self.profile_pill.setToolTip(f"Profile: {profile_id}")
        self.profile_pill.setFixedHeight(self.PROFILE_PILL_HEIGHT)
        self.profile_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.profile_pill.clicked.connect(self._on_profile_click)
        layout.addWidget(self.profile_pill, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addSpacing(8)

        # ── Title (centered with stretch on both sides) ──
        layout.addStretch(1)
        self.title_label = QLabel("Veil")
        self.title_label.setObjectName("TitleLabel")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label, 0)
        layout.addStretch(1)

        # ── Right cluster: menu + traffic lights ──
        self.menu_btn = QPushButton("\u22EF")
        self.menu_btn.setObjectName("TitleBarMenuBtn")
        self.menu_btn.setToolTip("Menu")
        self.menu_btn.setFixedSize(22, 22)
        self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_btn.clicked.connect(self._on_menu_click)
        layout.addWidget(self.menu_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addSpacing(6)

        self.close_btn = TrafficLightButton("close")
        self.close_btn.clicked.connect(self._bw.close)
        layout.addWidget(self.close_btn)

        self.min_btn = TrafficLightButton("minimize")
        self.min_btn.clicked.connect(self._bw.showMinimized)
        layout.addWidget(self.min_btn)

        self.max_btn = TrafficLightButton("maximize")
        self.max_btn.clicked.connect(self._toggle_maximize)
        layout.addWidget(self.max_btn)



    def _on_profile_click(self):
        """Show quick profile/workspace actions."""
        menu = QMenu(self)
        profile_id = self._bw.identity_manager.get_current_profile_id()
        menu.addAction(f"Profile: {profile_id[:20]}...").setEnabled(False)
        menu.addSeparator()
        act_reset = menu.addAction("Reset Digital Identity")
        act_reset.triggered.connect(self._bw.reset_digital_identity)
        act_clear = menu.addAction("Clear Browsing Data\u2026")
        act_clear.triggered.connect(self._bw.clear_browsing_data)
        pill_pos = self.profile_pill.mapToGlobal(self.profile_pill.rect().bottomLeft())
        # Apply theme to menu
        menu.setStyleSheet(self._bw.styleSheet())
        menu.exec(pill_pos)

    def _on_menu_click(self):
        # Show the browser menu below this button
        btn_pos = self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomLeft())
        self._bw.show_menu_at(btn_pos)

    def _toggle_maximize(self):
        bw = self._bw
        if bw.isMaximized():
            bw.showNormal()
            if hasattr(bw, '_saved_geometry') and bw._saved_geometry:
                bw.setGeometry(bw._saved_geometry)
        else:
            # Save current geometry for restore
            bw._saved_geometry = bw.geometry()
            # Use available desktop rect (excludes taskbar) to prevent overlap
            avail = QApplication.primaryScreen().availableGeometry()
            bw.setGeometry(avail)
            bw.setWindowState(bw.windowState() | Qt.WindowState.WindowMaximized)

    def set_title(self, text):
        self.title_label.setText(text if text else "Veil")

    # ── Window dragging ──────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() == Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self._bw.move(self._bw.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self._toggle_maximize()
        event.accept()


# ── TabContent ───────────────────────────────────────────────────────
class TabContent(QStackedWidget):
    """Manages web view content pages synced with a ChromeTabBar."""

    def __init__(self, tab_bar, parent=None):
        super().__init__(parent)
        self.tab_bar = tab_bar
        self.tab_bar.tabSelected.connect(self._on_tab_selected)

    def addTab(self, widget, title, private=False):
        self.addWidget(widget)
        idx = self.tab_bar.addTab(title=title)
        self.tab_bar.setPrivate(idx, private)
        return idx

    def removeTab(self, index):
        w = self.widget(index)
        self.removeWidget(w)
        self.tab_bar.removeTab(index)

    def tabText(self, index):
        return self.tab_bar.tabText(index)

    def setTabText(self, index, text):
        self.tab_bar.setTabText(index, text)

    def setCurrentIndex(self, index):
        if self.currentIndex() != index:
            super().setCurrentIndex(index)
        self.tab_bar.setCurrentIndex(index)

    def _on_tab_selected(self, index):
        if 0 <= index < self.count() and index != self.currentIndex():
            super().setCurrentIndex(index)


# ── BrowserWindow ────────────────────────────────────────────────────
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

        # Remove native title bar — we draw our own
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        self.setWindowTitle("Veil")
        self.resize(1280, 800)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Custom title bar (at the very top)
        self.title_bar = TitleBar(self)

        self.init_chrome()
        self.init_progress_bar()
        self.init_tabs()
        self.init_statusbar()
        self.init_menu()

        # Insert title bar above chrome in the layout
        self.main_layout.insertWidget(0, self.title_bar)

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
        self.main_layout.addWidget(self.chrome)

    def init_progress_bar(self):
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.hide()
        self.main_layout.addWidget(self.progress_bar)

    def init_tabs(self):
        self.tab_bar = ChromeTabBar()
        self.tab_bar.tabCloseRequested.connect(self.close_tab)
        self.tab_bar.newTabRequested.connect(lambda: self.add_new_tab())
        self.tab_bar.tabSelected.connect(self.on_tab_changed)

        self.tabs = TabContent(self.tab_bar)

        self.chrome.addTabBar(self.tab_bar)

        self.main_layout.addWidget(self.tabs)

    def init_statusbar(self):
        self.setStatusBar(QStatusBar())
        profile_id = self.identity_manager.get_current_profile_id()
        self.statusBar().showMessage(f"Ready | Profile: {profile_id[:20]}…")

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

        style = f"""
            QMainWindow {{
                background-color: {c.bg};
            }}
            QWidget#TitleBar {{
                background-color: {c.toolbar_bg};
                border-bottom: 1px solid {c.divider};
            }}
            QLabel#TitleLabel {{
                color: {c.text_secondary};
                font-size: 12px;
                font-weight: 500;
                background: transparent;
            }}
            QPushButton#ProfilePill {{
                background-color: {c.accent_muted};
                color: {c.text_secondary};
                border: 1px solid {c.border};
                border-radius: 9px;
                font-size: 10px;
                font-weight: 600;
                padding: 0 8px;
                letter-spacing: 0.3px;
            }}
            QPushButton#ProfilePill:hover {{
                background-color: {c.surface_hover};
                color: {c.text};
            }}
            QPushButton#TitleBarMenuBtn {{
                background-color: transparent;
                color: {c.text_secondary};
                border: none;
                border-radius: 6px;
                font-size: 16px;
                padding: 0;
            }}
            QPushButton#TitleBarMenuBtn:hover {{
                color: {c.text};
                background-color: {c.surface_hover};
            }}
            QWidget {{
                background-color: {c.bg};
                color: {c.text};
                font-family: {t.font_family};
                font-size: {t.font_size};
            }}
            QMenu {{
                background-color: {c.toolbar_bg};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {t.radius_md}px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 24px;
                border-radius: {t.radius_sm}px;
            }}
            QMenu::item:selected {{
                background-color: {c.surface_hover};
                color: {c.text};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {c.divider};
                margin: 6px 12px;
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
                padding: 10px 12px;
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
            }}
            QScrollBar::handle:vertical {{
                background: {c.border_hover};
                border-radius: {t.scrollbar_thumb_radius}px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {c.text_tertiary};
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
            }}
            QScrollBar::handle:horizontal {{
                background: {c.border_hover};
                border-radius: {t.scrollbar_thumb_radius}px;
                min-width: 30px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {c.text_tertiary};
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
                padding: 4px 12px;
            }}
            QToolTip {{
                background-color: {c.toolbar_bg};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {t.radius_sm}px;
                padding: 6px 10px;
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
        """
        self.setStyleSheet(style)
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

    # ── Native window events (Windows: resize, snap, shadow) ──────────
    RESIZE_MARGIN = 8  # px from each edge for resize grip

    def nativeEvent(self, eventType, message):
        """Handle Windows WM_NCHITTEST for resize + Aero Snap support."""
        if platform.system() == "Windows" and eventType == b"windows_generic_MSG":
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message.__int__()))
            except Exception:
                return (False, 0)

            if msg.message == 0x0084:  # WM_NCHITTEST
                # Don't handle resize when maximized or fullscreen
                if self.isMaximized() or self.isFullScreen():
                    return (True, 1)  # HTCLIENT

                # Extract cursor position (screen coords) from LPARAM
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value

                # Get window rect in screen coords
                win_rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(
                    ctypes.wintypes.HWND(int(self.winId())),
                    ctypes.byref(win_rect)
                )

                # Determine which edge/corner the cursor is near
                m = self.RESIZE_MARGIN
                on_left   = x - win_rect.left   < m
                on_right  = win_rect.right - x  < m
                on_top    = y - win_rect.top    < m
                on_bottom = win_rect.bottom - y < m

                # Return the appropriate Windows HT* constant for resizing
                if on_top and on_left:
                    return (True, 13)   # HTTOPLEFT
                if on_top and on_right:
                    return (True, 14)   # HTTOPRIGHT
                if on_bottom and on_left:
                    return (True, 16)   # HTBOTTOMLEFT
                if on_bottom and on_right:
                    return (True, 17)   # HTBOTTOMRIGHT
                if on_left:
                    return (True, 10)   # HTLEFT
                if on_right:
                    return (True, 11)   # HTRIGHT
                if on_top:
                    return (True, 12)   # HTTOP
                if on_bottom:
                    return (True, 15)   # HTBOTTOM

                # Check if cursor is over the title bar area (for dragging + Aero Snap)
                tb = self.title_bar
                tb_global = tb.mapToGlobal(QPoint(0, 0))
                in_title_bar = (
                    tb_global.x() <= x <= tb_global.x() + tb.width()
                    and tb_global.y() <= y <= tb_global.y() + tb.height()
                )
                if in_title_bar:
                    # Check if cursor is over any interactive button in the title bar
                    # (menu_btn, traffic lights) — let those handle clicks
                    # Everything else is draggable (HTCAPTION)
                    # All interactive buttons in the title bar (profile + menu + traffic lights)
                    title_btns = [tb.profile_pill, tb.menu_btn, tb.close_btn, tb.min_btn, tb.max_btn]
                    over_button = False
                    for btn in title_btns:
                        bg = btn.mapToGlobal(QPoint(0, 0))
                        if bg.x() <= x <= bg.x() + btn.width() and bg.y() <= y <= bg.y() + btn.height():
                            over_button = True
                            break
                    if not over_button:
                        return (True, 2)  # HTCAPTION — enables snap, drag, shake
                    else:
                        return (True, 1)  # HTCLIENT — let buttons work

                # Check tab strip area (ChromeTabBar background) for Aero Snap
                try:
                    tb = self.tab_bar
                    tb_global = tb.mapToGlobal(QPoint(0, 0))
                    in_tab_strip = (
                        tb_global.x() <= x <= tb_global.x() + tb.width()
                        and tb_global.y() <= y <= tb_global.y() + tb.height()
                    )
                    if in_tab_strip and tb.is_drag_area(QPoint(x, y)):
                        return (True, 2)  # HTCAPTION
                except Exception:
                    pass

                return (True, 1)  # HTCLIENT — default

        return (False, 0)

    # ── Tab management ────────────────────────────────────────────────
    def changeEvent(self, event):
        if event.type() == event.Type.WindowStateChange:
            was_maxed = event.oldState() & Qt.WindowState.WindowMaximized
            is_maxed = self.windowState() & Qt.WindowState.WindowMaximized

            # Update maximize button icon
            new_action = "restore" if is_maxed else "maximize"
            self.title_bar.max_btn._action = new_action
            self.title_bar.max_btn._colors = TrafficLightButton.COLORS[new_action]
            self.title_bar.max_btn.setToolTip("Restore" if is_maxed else "Maximize")
            self.title_bar.max_btn.update()

            # Save geometry before maximizing (for button/double-click restore)
            if is_maxed and not was_maxed:
                if not hasattr(self, '_saved_geometry') or not self._saved_geometry:
                    # Window might already be position by Windows snap at this point;
                    # this handles the programmatic maximize case
                    pass
            elif not is_maxed and was_maxed:
                # Restore saved geometry when un-maximizing via button/double-click
                if hasattr(self, '_saved_geometry') and self._saved_geometry:
                    self.setGeometry(self._saved_geometry)
        super().changeEvent(event)

    def add_new_tab(self, url=None, title="New Tab", private=False):
        view = QWebEngineView()

        if private:
            page = WebPage(self.private_profile, self, is_private=True, parent=view)
            view.setProperty("is_private", True)
        else:
            page = WebPage(self.default_profile, self, is_private=False, parent=view)
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

        index = self.tabs.addTab(view, title, private=private)
        self.tabs.setCurrentIndex(index)
        self.chrome.setTabCount(self.tabs.count())
        self.tab_bar.invalidate_layout()

        view.titleChanged.connect(lambda t, v=view: self.update_tab_title(v, t))
        view.iconChanged.connect(lambda: self.tab_bar.invalidate_layout())
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
            self.chrome.setTabCount(self.tabs.count())
        else:
            self.close()

    def on_tab_changed(self, index):
        if index < 0:
            return
        view = self.tabs.widget(index)
        if view:
            self.update_navigation_buttons(view)
            url = view.url().toString()
            if url.startswith("file://") and "start_page.html" in url:
                self.chrome.setUrl("")
                self.title_bar.set_title("Veil")
                self.setWindowTitle("Veil")
            else:
                self.chrome.setUrl(url)
                title = self.tabs.tabText(index)
                self.title_bar.set_title(title)
                self.setWindowTitle(title + " — Veil")

    def update_tab_title(self, view, title):
        index = self.tabs.indexOf(view)
        if index != -1:
            clean_title = title if title else "New Tab"
            is_private = view.property("is_private")
            self.tabs.setTabText(index, clean_title)
            self.tab_bar.setPrivate(index, is_private)
            if index == self.tabs.currentIndex():
                self.title_bar.set_title(clean_title)
                self.setWindowTitle(clean_title + " — Veil")

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
        self.chrome.setNavigationState(
            view.history().canGoBack(),
            view.history().canGoForward()
        )

    # ── Navigation ────────────────────────────────────────────────────
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

    # ── Extensions ───────────────────────────────────────────────────
    def show_extensions_dialog(self):
        manager = self.default_profile.extensionManager()
        installed = manager.extensions()
        t = self.theme
        c = t.colors

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
                outline: none;
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

    # ── Settings toggles ─────────────────────────────────────────────
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

    def set_search_engine(self, name, url):
        self.settings.set("search_engine_name", name)
        self.settings.set("search_engine", url)
        self.statusBar().showMessage(f"Search engine set to {name}", 3000)

    # ── Identity / data ──────────────────────────────────────────────
    def reset_digital_identity(self):
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


# ── Dev auto-reloader ────────────────────────────────────────────────
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


# ── Profile settings ─────────────────────────────────────────────────
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


# ── Entry point ──────────────────────────────────────────────────────
def main():
    dev_mode = "--dev" in sys.argv
    if dev_mode:
        sys.argv.remove("--dev")

    settings = SettingsManager()
    app = QApplication(sys.argv)

    # ── Global font: smooth, crisp text everywhere ──────────────────
    theme = get_theme(settings.get("theme"))
    base_font = _resolve_font(theme.font_family, 13)
    base_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    base_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(base_font)

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

    window = BrowserWindow(
        settings, extension_loader,
        default_profile, private_profile, identity_manager
    )
    window.adblocker = adblocker
    extension_loader.status_changed.connect(window.on_extension_status)
    extension_loader.count_changed.connect(window.on_extension_count)
    extension_loader.install_all()

    default_profile.downloadRequested.connect(window.on_download_requested)
    private_profile.downloadRequested.connect(window.on_download_requested)

    window.show()

    if dev_mode:
        DevAutoReloader(window)
        print("[dev] Auto-reload active — watching for file changes...")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()