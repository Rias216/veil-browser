import sys
import os
from PySide6.QtCore import QUrl, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QToolBar, QLineEdit,
    QPushButton, QProgressBar, QFileDialog, QMenu, QStatusBar,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox, QTabBar
)
from PySide6.QtWebEngineCore import (
    QWebEngineProfile, QWebEnginePage, QWebEngineSettings
)
from PySide6.QtWebEngineWidgets import QWebEngineView

# Import local modules
from config import SettingsManager, SEARCH_ENGINES
from adblocker import (
    AdBlocker, PrivacyRequestInterceptor,
    inject_fake_cookies, block_third_party_cookies
)
from proxy import apply_proxy, ProxyDialog

# Common User Agent to blend in with mainstream traffic
SPOOFED_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class WebPage(QWebEnginePage):
    """Custom page that routes target='_blank' and JS window.open into new tabs."""

    def __init__(self, profile, browser_window, is_private=False, parent=None):
        super().__init__(profile, parent)
        self._browser_window = browser_window
        self._is_private = is_private

    def createWindow(self, window_type):
        new_view = self._browser_window.add_new_tab(private=self._is_private)
        return new_view.page()


class BrowserWindow(QMainWindow):
    def __init__(self, settings, adblocker, private_profile):
        super().__init__()
        self.settings = settings
        self.adblocker = adblocker
        self.private_profile = private_profile

        self.setWindowTitle("Shield Browser")
        self.resize(1024, 768)

        # Main container and layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # UI Components
        self.init_toolbar()
        self.init_progress_bar()
        self.init_tabs()
        self.init_statusbar()
        self.init_stylesheet()

        # Update adblock count periodically
        self.adblock_timer = QTimer(self)
        self.adblock_timer.timeout.connect(self.update_adblock_label)
        self.adblock_timer.start(500)

        # Start with a default tab
        self.add_new_tab(private=False)

    # ── Stylesheet ────────────────────────────────────────────────────
    def init_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #121214;
            }
            QWidget {
                background-color: #121214;
                color: #ffffff;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QToolBar {
                background-color: #1c1c1e;
                border-bottom: 1px solid #2c2c2e;
                spacing: 4px;
                padding: 4px 8px;
            }
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                color: #aeaeae;
                padding: 4px;
                font-size: 16px;
                min-width: 28px;
                min-height: 28px;
            }
            QPushButton:hover {
                background-color: #2c2c2e;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #3a3a3c;
            }
            QLineEdit {
                background-color: #121214;
                border: 1px solid #2c2c2e;
                border-radius: 4px;
                color: #ffffff;
                padding: 5px 10px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #007acc;
            }
            QProgressBar {
                border: none;
                background-color: transparent;
                height: 2px;
            }
            QProgressBar::chunk {
                background-color: #007acc;
            }
            QTabWidget::pane {
                border: none;
                background-color: #121214;
            }
            QTabBar::tab {
                background-color: #1c1c1e;
                color: #8f90a6;
                padding: 6px 12px;
                border: 1px solid #2c2c2e;
                border-bottom: none;
                margin-right: 2px;
                min-width: 100px;
                max-width: 180px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-size: 12px;
            }
            QTabBar::tab:hover {
                background-color: #252528;
                color: #ffffff;
            }
            QTabBar::tab:selected {
                background-color: #121214;
                color: #ffffff;
                border-bottom: 1px solid #121214;
            }
            QStatusBar {
                background-color: #1c1c1e;
                color: #8f90a6;
                border-top: 1px solid #2c2c2e;
                font-size: 11px;
            }
        """)

    # ── Toolbar ────────────────────────────────────────────────────────
    def init_toolbar(self):
        self.toolbar = QToolBar("Navigation")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.addToolBar(self.toolbar)

        # Navigation Buttons
        self.back_btn = QPushButton("←")
        self.back_btn.setToolTip("Back")
        self.back_btn.clicked.connect(self.current_tab_back)
        self.toolbar.addWidget(self.back_btn)

        self.forward_btn = QPushButton("→")
        self.forward_btn.setToolTip("Forward")
        self.forward_btn.clicked.connect(self.current_tab_forward)
        self.toolbar.addWidget(self.forward_btn)

        self.reload_btn = QPushButton("⟳")
        self.reload_btn.setToolTip("Reload")
        self.reload_btn.clicked.connect(self.current_tab_reload)
        self.toolbar.addWidget(self.reload_btn)

        self.home_btn = QPushButton("⌂")
        self.home_btn.setToolTip("Home Start Page")
        self.home_btn.clicked.connect(self.go_home)
        self.toolbar.addWidget(self.home_btn)

        # Address Bar
        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("Enter URL or search privately...")
        self.address_bar.returnPressed.connect(self.navigate_to_url)
        self.toolbar.addWidget(self.address_bar)

        # AdBlock Shield Indicator
        self.shield_btn = QPushButton("🛡️ 0")
        self.shield_btn.setToolTip("AdBlock active. Click to toggle.")
        self.shield_btn.setStyleSheet("""
            QPushButton {
                color: #00ca72;
                font-size: 12px;
                font-weight: bold;
                padding: 4px 8px;
                border: 1px solid #2c2c2e;
                border-radius: 4px;
                margin-left: 4px;
            }
            QPushButton:hover {
                background-color: #2c2c2e;
            }
        """)
        self.shield_btn.clicked.connect(self.toggle_adblock_from_toolbar)
        self.toolbar.addWidget(self.shield_btn)

        # Settings Button
        self.menu_btn = QPushButton("⋮")
        self.menu_btn.setToolTip("Menu")

        # Build Menu
        self.menu = QMenu(self)
        self.menu.setStyleSheet("""
            QMenu {
                background-color: #1c1c1e;
                color: #ffffff;
                border: 1px solid #2c2c2e;
            }
            QMenu::item {
                padding: 6px 20px;
            }
            QMenu::item:selected {
                background-color: #007acc;
            }
            QMenu::separator {
                height: 1px;
                background-color: #2c2c2e;
                margin: 4px 0px;
            }
        """)

        # Menu Actions
        self.menu.addAction("New Tab", self.add_new_tab, "Ctrl+T")
        self.menu.addAction("New Private Tab", self.add_new_private_tab, "Ctrl+Shift+N")
        self.menu.addSeparator()

        self.adblock_action = self.menu.addAction("AdBlock Enabled")
        self.adblock_action.setCheckable(True)
        self.adblock_action.setChecked(self.settings.get("adblock_enabled"))
        self.adblock_action.triggered.connect(self.toggle_adblock)

        self.https_action = self.menu.addAction("HTTPS-Only Mode")
        self.https_action.setCheckable(True)
        self.https_action.setChecked(self.settings.get("https_only"))
        self.https_action.triggered.connect(self.toggle_https_only)

        self.menu.addSeparator()

        # ── Search Engine Submenu ──────────────────────────────────────
        self.search_menu = self.menu.addMenu("Search Engine")
        self.search_menu.setStyleSheet(self.menu.styleSheet())
        self.search_action_group = QActionGroup(self)
        self.search_action_group.setExclusive(True)

        current_engine = self.settings.get("search_engine_name")
        for name, url in SEARCH_ENGINES.items():
            action = QAction(name, self)
            action.setCheckable(True)
            action.setChecked(name == current_engine)
            action.triggered.connect(lambda checked, n=name, u=url: self.set_search_engine(n, u))
            self.search_action_group.addAction(action)
            self.search_menu.addAction(action)

        self.menu.addSeparator()
        self.menu.addAction("Proxy Configuration...", self.open_proxy_settings)
        self.menu.addAction("Clear Browsing Data...", self.clear_browsing_data)
        self.menu.addSeparator()
        self.menu.addAction("Exit", self.close)

        self.menu_btn.setMenu(self.menu)
        self.toolbar.addWidget(self.menu_btn)

    def init_progress_bar(self):
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.main_layout.addWidget(self.progress_bar)

    # ── Tabs ──────────────────────────────────────────────────────────
    def init_tabs(self):
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_tab)

        # Double-click on tab → close it; double-click on empty bar → new tab
        self.tabs.tabBarDoubleClicked.connect(self.on_tab_bar_double_clicked)

        # Add new tab button
        self.add_tab_btn = QPushButton("+")
        self.add_tab_btn.clicked.connect(self.add_new_tab)
        self.tabs.setCornerWidget(self.add_tab_btn, Qt.Corner.TopRightCorner)

        self.main_layout.addWidget(self.tabs)

    def init_statusbar(self):
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

    # ── Tab Management ────────────────────────────────────────────────
    def add_new_tab(self, url=None, title="New Tab", private=False):
        view = QWebEngineView()

        # Create custom page with the right profile
        if private:
            page = WebPage(self.private_profile, self, is_private=True, parent=view)
            view.setProperty("is_private", True)
        else:
            page = WebPage(
                QWebEngineProfile.defaultProfile(), self,
                is_private=False, parent=view
            )
            view.setProperty("is_private", False)

        view.setPage(page)

        # ── Anti-Fingerprinting Settings ──────────────────────────────
        s = page.settings()
        # Block canvas fingerprinting
        s.setAttribute(QWebEngineSettings.WebAttribute.ReadingFromCanvasEnabled, False)
        # Block <a ping> hyperlink auditing
        s.setAttribute(QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled, False)
        # Block WebRTC local IP leaking
        s.setAttribute(QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly, True)

        # Set default URL if none provided
        if url is None:
            file_path = os.path.abspath("start_page.html")
            url_str = f"file:///{file_path}"
            if private:
                url_str += "?mode=incognito"
            url = QUrl(url_str)

        view.load(url)

        # Add tab to QTabWidget
        index = self.tabs.addTab(view, title)

        # Style private tabs differently
        if private:
            self.tabs.setTabText(index, f"🔒 {title}")

        self.tabs.setCurrentIndex(index)

        # Connect signals
        view.titleChanged.connect(lambda t, v=view: self.update_tab_title(v, t))
        view.urlChanged.connect(lambda u, v=view: self.update_tab_url(v, u))
        view.loadProgress.connect(lambda p, v=view: self.update_tab_progress(v, p))
        view.loadFinished.connect(lambda f, v=view: self.on_load_finished(v, f))

        # Link hover safety feature
        page.linkHovered.connect(self.on_link_hovered)

        # Shortcut keys within the view
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
            # Double-clicked on empty tab bar space → new tab
            self.add_new_tab()
        else:
            # Double-clicked on an existing tab → close it
            self.close_tab(index)

    def on_tab_changed(self, index):
        if index < 0:
            return
        view = self.tabs.widget(index)
        if view:
            self.update_navigation_buttons(view)
            url = view.url().toString()
            if url.startswith("file://") and "start_page.html" in url:
                self.address_bar.setText("")
            else:
                self.address_bar.setText(url)

            progress = view.property("progress")
            if progress is not None and progress < 100:
                self.progress_bar.setValue(progress)
                self.progress_bar.show()
            else:
                self.progress_bar.hide()

    def update_tab_title(self, view, title):
        index = self.tabs.indexOf(view)
        if index != -1:
            is_private = view.property("is_private")
            clean_title = title if title else "New Tab"
            if is_private:
                self.tabs.setTabText(index, f"🔒 {clean_title}")
            else:
                self.tabs.setTabText(index, clean_title)

    def update_tab_url(self, view, url):
        if view == self.tabs.currentWidget():
            url_str = url.toString()
            if url_str.startswith("file://") and "start_page.html" in url_str:
                self.address_bar.setText("")
            else:
                self.address_bar.setText(url_str)
            self.update_navigation_buttons(view)

    def update_tab_progress(self, view, progress):
        view.setProperty("progress", progress)
        if view == self.tabs.currentWidget():
            self.progress_bar.setValue(progress)
            if progress < 100:
                self.progress_bar.show()
            else:
                self.progress_bar.hide()

    def on_load_finished(self, view, success):
        view.setProperty("progress", 100)
        if view == self.tabs.currentWidget():
            self.progress_bar.hide()
            self.update_navigation_buttons(view)

    def update_navigation_buttons(self, view):
        self.back_btn.setEnabled(view.history().canGoBack())
        self.forward_btn.setEnabled(view.history().canGoForward())

    # ── Navigation Actions ────────────────────────────────────────────
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
            file_path = os.path.abspath("start_page.html")
            url_str = f"file:///{file_path}"
            if is_private:
                url_str += "?mode=incognito"
            view.load(QUrl(url_str))

    def navigate_to_url(self):
        text = self.address_bar.text().strip()
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

    # ── Keyboard & Events ─────────────────────────────────────────────
    def eventFilter(self, obj, event):
        if event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_F5:
                self.current_tab_reload()
                return True
        return super().eventFilter(obj, event)

    # ── Privacy Settings ──────────────────────────────────────────────
    def update_adblock_label(self):
        count = self.adblocker.get_blocked_count()
        self.shield_btn.setText(f"🛡️ {count}")

    def toggle_adblock_from_toolbar(self):
        is_enabled = not self.settings.get("adblock_enabled")
        self.toggle_adblock(is_enabled)
        self.adblock_action.setChecked(is_enabled)

    def toggle_adblock(self, checked):
        self.settings.set("adblock_enabled", checked)
        state = "enabled" if checked else "disabled"
        self.statusBar().showMessage(f"AdBlock {state}", 3000)
        view = self.tabs.currentWidget()
        if view:
            view.reload()

    def toggle_https_only(self, checked):
        self.settings.set("https_only", checked)
        state = "enabled" if checked else "disabled"
        self.statusBar().showMessage(f"HTTPS-Only mode {state}", 3000)

    def set_search_engine(self, name, url):
        self.settings.set("search_engine_name", name)
        self.settings.set("search_engine", url)
        self.statusBar().showMessage(f"Search engine set to {name}", 3000)

    def open_proxy_settings(self):
        dialog = ProxyDialog(self.settings, self)
        dialog.exec()

    def clear_browsing_data(self):
        confirm = QMessageBox.question(
            self, "Clear Browsing Data",
            "Are you sure you want to delete all cache, cookies, and website data?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            QWebEngineProfile.defaultProfile().clearHttpCache()
            QWebEngineProfile.defaultProfile().cookieStore().deleteAllCookies()

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


def main():
    app = QApplication(sys.argv)

    # Initialize settings
    settings = SettingsManager()

    # Initialize proxy
    apply_proxy(settings)

    # Initialize AdBlocker
    adblocker = AdBlocker(settings)
    interceptor = PrivacyRequestInterceptor(adblocker, settings)

    # ── Default Profile Setup ─────────────────────────────────────────
    default_profile = QWebEngineProfile.defaultProfile()
    default_profile.setUrlRequestInterceptor(interceptor)
    default_profile.setHttpUserAgent(SPOOFED_UA)

    # Block third-party cookies & inject fake tracking cookies
    block_third_party_cookies(default_profile)
    inject_fake_cookies(default_profile)

    # ── Private Profile Setup ─────────────────────────────────────────
    private_profile = QWebEngineProfile("", app)
    private_profile.setUrlRequestInterceptor(interceptor)
    private_profile.setHttpUserAgent(SPOOFED_UA)
    block_third_party_cookies(private_profile)

    # Setup window
    window = BrowserWindow(settings, adblocker, private_profile)

    # Connect downloads for both profiles
    default_profile.downloadRequested.connect(window.on_download_requested)
    private_profile.downloadRequested.connect(window.on_download_requested)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
