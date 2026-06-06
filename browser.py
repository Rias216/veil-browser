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
    QApplication, QMainWindow, QTabWidget, QToolBar, QLineEdit,
    QPushButton, QProgressBar, QFileDialog, QMenu, QStatusBar,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox, QTabBar,
    QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
    QStyle, QStyleOptionButton, QStackedWidget, QSizePolicy,
    QSpacerItem, QFrame, QGraphicsDropShadowEffect
)
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


# ── VeilTabBar ───────────────────────────────────────────────────────
class VeilTabBar(QTabBar):
    """Completely redone tab bar: inactive tabs get a visible subtle bg,
    active tab draws a clean top/side border path (no half-pixel bottom artifacts)."""

    CLOSE_SIZE = 16
    BTM_LINE = 1
    MIN_TAB_WIDTH = 100   # minimum px per tab — enough for ~8 chars + close button

    doubleClickedEmpty = Signal()

    def tabSizeHint(self, index):
        base = super().tabSizeHint(index)
        return QSize(max(self.MIN_TAB_WIDTH, base.width()), base.height())

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VeilTabBar")
        self.setTabsClosable(False)
        self.setMovable(True)
        self.setDrawBase(False)
        self.setDocumentMode(True)
        self.setExpanding(True)          # fill available space when few tabs
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)

        t = self._theme()
        self.setFixedHeight(t.tab_height)
        self.setMinimumWidth(0)
        self._tab_radius = t.tab_radius

        # ── Hover tracking ──
        self.setMouseTracking(True)
        self._hovered_index = -1
        self._hovered_close = -1
        self._pressed_close = -1

        # ─️ Paint generation tracking (skip redundant inactive tab draws) ──
        self._paint_gen = 0       # incremented when tab layout changes
        self._last_painted_gen = -1

        # ── Close button fade ──
        self._close_fade = 0.0
        self._close_fade_anim = QVariantAnimation(self)
        self._close_fade_anim.setDuration(120)
        self._close_fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._close_fade_anim.valueChanged.connect(self._on_fade_step)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _on_fade_step(self, v):
        self._close_fade = v
        idx = self._hovered_index
        if idx >= 0:
            self.update(self._close_rect(idx))
        else:
            self.update()

    def _theme(self):
        win = self.window()
        if hasattr(win, 'theme'):
            return win.theme
        from themes import THEMES
        return list(THEMES.values())[0]

    def _close_rect(self, index):
        tr = self.tabRect(index)
        size = self.CLOSE_SIZE
        return QRect(tr.right() - size - 6,
                     tr.y() + (tr.height() - size) // 2,
                     size, size)

    # ── Mouse event forwarding ──
    def _hover_index(self, pos):
        for i in range(self.count()):
            if self.tabRect(i).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        pos = event.position().toPoint()
        idx = self._hover_index(pos)
        old_hover = self._hovered_index
        old_close = self._hovered_close
        new_close = idx if idx >= 0 and self._close_rect(idx).contains(pos) else -1

        self._hovered_index = idx
        self._hovered_close = new_close

        # Only repaint when hover state actually changes — avoids paint storms
        if idx != old_hover:
            if old_hover >= 0:
                self.update(self.tabRect(old_hover))
            if idx >= 0:
                self.update(self.tabRect(idx))
        elif new_close != old_close and idx >= 0:
            # Close button hover changed within same tab — minimal repaint
            self.update(self._close_rect(idx))

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        pos = event.position().toPoint()
        idx = self._hover_index(pos)
        pressed_close = idx >= 0 and event.button() == Qt.MouseButton.LeftButton and self._close_rect(idx).contains(pos)
        self._pressed_close = idx if pressed_close else -1
        if idx >= 0:
            self.update(self._close_rect(idx) if pressed_close else self.tabRect(idx))

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._pressed_close >= 0:
            pos = event.position().toPoint()
            if self._close_rect(self._pressed_close).contains(pos):
                self.tabCloseRequested.emit(self._pressed_close)
            self._pressed_close = -1
            self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        old = self._hovered_index
        self._hovered_index = -1
        self._hovered_close = -1
        self._pressed_close = -1
        if old >= 0:
            self.update(self.tabRect(old))

    # ── Invalidate tab layout cache (call when tabs added/removed/renamed) ──
    def invalidate_layout(self):
        self._paint_gen += 1
        self.update()

    # ── Painting ──
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        t = self._theme()
        c = t.colors
        h = self.height()
        w = self.width()
        r = self._tab_radius
        full_redraw = self._paint_gen != self._last_painted_gen

        # ── 1. Bottom divider line (full width) ──
        painter.fillRect(0, h - self.BTM_LINE, w, self.BTM_LINE, QColor(c.divider))

        # ── 2. Inactive tabs ──
        if full_redraw:
            # Full redraw: backgrounds + labels + close buttons
            for i in range(self.count()):
                if i == self.currentIndex():
                    continue
                tr = self.tabRect(i)
                if tr.isNull():
                    continue
                self._draw_inactive(painter, i, tr, t, c, r)
            self._last_painted_gen = self._paint_gen
        else:
            # Quick pass: only close buttons + hover overlay (expensive bg fill skipped)
            for i in range(self.count()):
                if i == self.currentIndex():
                    continue
                tr = self.tabRect(i)
                if tr.isNull():
                    continue
                # Draw close button (small area — cheap even for 50 tabs)
                if i == self._hovered_index:
                    self._draw_close(painter, i, t, c)
            # Hover overlay (single tab — always cheap)
            hi = self._hovered_index
            if hi >= 0 and hi != self.currentIndex():
                tr = self.tabRect(hi)
                if not tr.isNull():
                    fill = QRectF(tr).adjusted(2, 0, -2, 0)
                    path = QPainterPath()
                    path.addRoundedRect(fill, r, r)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.fillPath(path, QColor(c.tab_hover))

        # ── 3. Active tab on top (always drawn — always dynamic) ──
        ai = self.currentIndex()
        if 0 <= ai < self.count():
            tr = self.tabRect(ai)
            if not tr.isNull():
                self._draw_active(painter, ai, tr, t, c, r)

        painter.end()

    # ── Shared tab shape primitives ──

    @staticmethod
    def _tab_fill_path(rect, radius, extend_bottom=0):
        """Rounded-rect path for the tab body, optionally extended past bottom."""
        r = min(radius, rect.width() / 2.0, (rect.height() + extend_bottom) / 2.0)
        fill = QRectF(rect.x(), rect.y(), rect.width(), rect.height() + extend_bottom)
        path = QPainterPath()
        path.addRoundedRect(fill, r, r)
        return path

    @staticmethod
    def _tab_border_path(rect, r):
        """Stroke path for top + left + right border ONLY — no bottom edge.
        Prevents 0.5px 'tails' past the divider line."""
        x, y = rect.x(), rect.y()
        x2, y2 = x + rect.width(), y + rect.height()
        r = min(r, rect.width() / 2.0, rect.height() / 2.0)
        path = QPainterPath()
        path.moveTo(x, y2)
        path.lineTo(x, y + r)
        path.quadTo(x, y, x + r, y)
        path.lineTo(x2 - r, y)
        path.quadTo(x2, y, x2, y + r)
        path.lineTo(x2, y2)
        return path

    # ── Tab drawing ──

    def _draw_inactive(self, painter, index, tab_rect, t, c, r):
        is_hovered = index == self._hovered_index
        fill = QRectF(tab_rect).adjusted(1, 0, -1, 0)
        path = self._tab_fill_path(fill, r, extend_bottom=0)

        # Base fill: visible separation from toolbar_bg
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, QColor(c.tab_inactive_bg))

        # Hover overlay
        if is_hovered:
            painter.fillPath(path, QColor(c.tab_hover))

        # Subtle right divider between adjacent inactive tabs
        if index + 1 < self.count() and index + 1 != self.currentIndex():
            x = tab_rect.right()
            painter.setPen(QPen(QColor(c.divider), 0.5))
            painter.drawLine(QPointF(x, tab_rect.top() + 4),
                             QPointF(x, tab_rect.bottom() - 4))

        self._draw_label(painter, index, tab_rect, is_hovered, False, t, c)

    def _draw_active(self, painter, index, tab_rect, t, c, r):
        # Fill extends +2px past bottom to cover the BTM_LINE divider
        path = self._tab_fill_path(QRectF(tab_rect), r, extend_bottom=2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, QColor(c.tab_active))

        # Border: top + left + right edges only (no bottom "tails")
        border_path = self._tab_border_path(QRectF(tab_rect), r)
        painter.setPen(QPen(QColor(c.tab_active_border), 0.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(border_path)

        self._draw_label(painter, index, tab_rect, False, True, t, c)

    def _draw_label(self, painter, index, tab_rect, is_hovered, is_selected, t, c):
        show_close = is_selected or is_hovered
        close_w = (self.CLOSE_SIZE + 12) if show_close else 0
        text_left = tab_rect.left() + 10
        text_right = tab_rect.right() - close_w
        text_rect = QRect(int(text_left), tab_rect.top() + 1,
                          max(0, int(text_right - text_left)), tab_rect.height() - 2)

        font = QFont("Segoe UI Variable Text,Segoe UI" if platform.system() == "Windows" else "SF Pro Display")
        font.setPixelSize(12)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        font.setWeight(QFont.Weight.Medium if is_selected else QFont.Weight.Normal)

        painter.setFont(font)
        if is_selected:
            painter.setPen(QColor(c.text))
        elif is_hovered:
            painter.setPen(QColor(c.text))
        else:
            col = QColor(c.text)
            col.setAlphaF(0.75)
            painter.setPen(col)
        title = self.tabText(index)
        if title:
            fm = QFontMetrics(font)
            elided = fm.elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

        if show_close:
            self._draw_close(painter, index, t, c)

    def _draw_close(self, painter, index, t, c):
        rect = self._close_rect(index)
        cx = rect.center().x()
        cy = rect.center().y()
        is_hover = index == self._hovered_close
        is_pressed = index == self._pressed_close

        # Circle bg only on hover/press (Chrome behavior)
        if is_hover or is_pressed:
            bg_col = QColor(c.surface_hover if not is_pressed else c.surface_pressed)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(bg_col))
            painter.drawEllipse(QPointF(cx, cy), 8.0, 8.0)

        # × glyph
        col = QColor(c.text)
        if not (is_hover or is_pressed):
            col.setAlphaF(0.55)
        pen = QPen(col, 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        s = 3.0
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
        shadow_color = "rgba(0, 0, 0, 0.32)" if is_dark else "rgba(0, 0, 0, 0.12)"
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
            QListWidget::item {{
                padding: 7px 12px;
                border-radius: 8px;
                margin: 1px 4px;
            }}
            QListWidget::item:hover, QListWidget::item:selected {{
                background-color: {c.suggestion_hover};
                color: {c.text};
            }}
        """)
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
            item = QListWidgetItem(self._format_suggestion(s))
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.list_widget.addItem(item)
        self._position_and_show()

    def _format_suggestion(self, text):
        """Show suggestion with a search icon prefix (Brave-style)."""
        return "\u2315  " + text

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
    LOCK_X = 10      # x position of lock center (centered within ~20px padding)

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
        self._is_secure = (
            not url_str
            or lower.startswith("https://")
            or self._is_local
        )
        if old_secure != self._is_secure or old_local != self._is_local:
            self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._has_focus = True
        self.focused.emit()
        self._start_focus_anim(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._has_focus = False
        self.blurred.emit()
        self._start_focus_anim(False)

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

        # Subtle focus glow — barely-there white tint, not accent-colored
        if fp > 0.01:
            glow_rect = QRectF(self.rect()).adjusted(-1, -1, 1, 1)
            glow_path = QPainterPath()
            glow_path.addRoundedRect(glow_rect, r + 2, r + 2)
            glow_color = QColor(255, 255, 255)
            glow_color.setAlphaF(0.06 * fp)
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

        # 3. Draw lock icon overlay
        lock_painter = QPainter(self)
        lock_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_lock_icon(lock_painter, c)
        lock_painter.end()

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

    def _draw_lock_icon(self, painter, c):
        cx = float(self.LOCK_X)
        cy = self.height() / 2.0

        if self._is_local:
            self._draw_file_icon(painter, cx, cy, c)
            return

        color = QColor(c.lock_secure if self._is_secure else c.lock_insecure)
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        self._draw_lock_body(painter, cx, cy, color)
        self._draw_lock_shackle(painter, cx, cy, color)
        self._draw_lock_keyhole(painter, cx, cy, color)

    def _draw_file_icon(self, painter, cx, cy, c):
        """Simple document outline for local files."""
        painter.setPen(QPen(QColor(c.text_tertiary), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        doc = QRectF(cx - 4, cy - 5, 7, 9)
        doc_path = QPainterPath()
        doc_path.moveTo(doc.x(), doc.y() + 2.5)
        doc_path.lineTo(doc.x(), doc.bottom())
        doc_path.lineTo(doc.right(), doc.bottom())
        doc_path.lineTo(doc.right(), doc.y())
        doc_path.lineTo(doc.x() + 2, doc.y())
        doc_path.lineTo(doc.x(), doc.y() + 2.5)
        painter.drawPath(doc_path)
        for dy in [-1.5, 0.5, 2.5]:
            painter.drawLine(QPointF(cx - 2, cy + dy), QPointF(cx + 2, cy + dy))

    def _draw_lock_body(self, painter, cx, cy, color):
        """Rounded rectangle body of the padlock."""
        bw, bh = 7.0, 5.5
        body_rect = QRectF(cx - bw / 2, cy - 0.5, bw, bh)
        body_path = QPainterPath()
        body_path.addRoundedRect(body_rect, 1.5, 1.5)
        painter.drawPath(body_path)

    def _draw_lock_shackle(self, painter, cx, cy, color):
        """Shackle arc — closed (secure) or open (insecure)."""
        sr = 2.6
        sx = cx - sr
        sy = cy - 0.5 - sr * 1.1
        shackle_rect = QRectF(sx, sy, sr * 2, sr * 2)

        if self._is_secure:
            path = QPainterPath()
            path.arcMoveTo(shackle_rect, 0)
            path.arcTo(shackle_rect, 0, 180)
        else:
            open_rect = QRectF(sx + 2.5, sy - 2.0, sr * 2, sr * 2)
            path = QPainterPath()
            path.arcMoveTo(open_rect, 0)
            path.arcTo(open_rect, 0, 180)
        painter.drawPath(path)

    def _draw_lock_keyhole(self, painter, cx, cy, color):
        """Small filled dot in the lock body."""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        bw, bh = 7.0, 5.5
        kx = cx - 0.7
        ky = cy - 0.5 + bh * 0.35 - 0.7
        painter.drawEllipse(QRectF(kx, ky, 1.4, 1.4))

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
    TOOLBAR_PADDING = 4       # horizontal edge padding

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
        self._add_shield_button(toolbar_layout)

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

        self.home_btn = NavButton('home')
        self.home_btn.setToolTip("Home")
        self.home_btn.clicked.connect(self.homeRequested)
        layout.addWidget(self.home_btn)

    def _add_address_bar(self, layout):
        self.address_bar = AddressBar()
        self.address_bar.setPlaceholderText("Search or enter address")
        self.address_bar.returnPressed.connect(self._on_url_submit)
        layout.addWidget(self.address_bar, 1)

    def _add_shield_button(self, layout):
        self.shield_btn = AdBlockButton()
        self.shield_btn.setObjectName("ShieldBtn")
        self.shield_btn.setToolTip("No threats blocked")
        self.shield_btn.setCursor(Qt.CursorShape.ArrowCursor)
        layout.addWidget(self.shield_btn)

    def addTabBar(self, tab_bar):
        # Insert VeilTabBar at position 0, so layout becomes: [tabs][+ button][stretch]
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

    def setShieldCount(self, n):
        self.shield_btn.setCount(n)
        if n == 0:
            self.shield_btn.setToolTip("No threats blocked")
        else:
            self.shield_btn.setToolTip(f"{n} ads/trackers blocked")

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
            VeilTabBar {{
                background-color: transparent;
                qproperty-drawBase: 0;
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

    TITLE_HEIGHT = 28

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
    RESIZE_MARGIN = 5  # px from each edge for resize grip

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

        index = self.tabs.addTab(view, title)
        if private:
            self.tabs.setTabText(index, f"\U0001f512 {title}")
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
            self.tab_bar.invalidate_layout()
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
                self.title_bar.set_title("Veil")
                self.setWindowTitle("Veil")
            else:
                self.chrome.setUrl(url)
                # Update title bar with current tab title
                title = self.tabs.tabText(index)
                clean = title.replace("\U0001f512 ", "")
                self.title_bar.set_title(clean)
                self.setWindowTitle(clean + " — Veil")

    def update_tab_title(self, view, title):
        index = self.tabs.indexOf(view)
        if index != -1:
            is_private = view.property("is_private")
            clean_title = title if title else "New Tab"
            if is_private:
                self.tabs.setTabText(index, f"\U0001f512 {clean_title}")
            else:
                self.tabs.setTabText(index, clean_title)
            # Update the custom title bar if this is the current tab
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

    def on_adblock_count_changed(self, n):
        self.chrome.setShieldCount(n)

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
    base_font = QFont(
        "Segoe UI" if platform.system() == "Windows" else "SF Pro Display"
    )
    base_font.setPixelSize(13)
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
    adblocker.count_changed.connect(window.on_adblock_count_changed)
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