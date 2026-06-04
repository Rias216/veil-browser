from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QComboBox, QCheckBox, QPushButton, QMessageBox, QFormLayout
)
from PySide6.QtNetwork import QNetworkProxy

def apply_proxy(settings):
    if not settings.get("proxy_enabled"):
        QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        print("Proxy disabled (Direct connection).")
        return

    proxy_type = settings.get("proxy_type")
    host = settings.get("proxy_host")
    port = settings.get("proxy_port")
    user = settings.get("proxy_user")
    pwd = settings.get("proxy_pass")

    proxy = QNetworkProxy()
    if proxy_type == "SOCKS5":
        proxy.setType(QNetworkProxy.ProxyType.Socks5Proxy)
    else:
        proxy.setType(QNetworkProxy.ProxyType.HttpProxy)

    proxy.setHostName(host)
    proxy.setPort(int(port))
    if user:
        proxy.setUser(user)
    if pwd:
        proxy.setPassword(pwd)

    QNetworkProxy.setApplicationProxy(proxy)
    print(f"Proxy applied: {proxy_type}://{host}:{port}")

class ProxyDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Proxy Connection Settings")
        self.setModal(True)
        self.resize(350, 250)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Style the dialog
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e24;
                color: #ffffff;
            }
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QLineEdit, QComboBox {
                background-color: #2d2d37;
                border: 1px solid #4a4a5a;
                border-radius: 4px;
                color: #ffffff;
                padding: 6px;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #007acc;
            }
            QCheckBox {
                color: #ffffff;
                spacing: 6px;
            }
            QPushButton {
                background-color: #2d2d37;
                border: 1px solid #4a4a5a;
                border-radius: 4px;
                color: #ffffff;
                padding: 6px 16px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #3e3e4f;
                border: 1px solid #007acc;
            }
            QPushButton#saveBtn {
                background-color: #007acc;
                border: 1px solid #007acc;
            }
            QPushButton#saveBtn:hover {
                background-color: #0098ff;
            }
        """)

        form = QFormLayout()

        self.enable_cb = QCheckBox("Enable Proxy Connection")
        self.enable_cb.setChecked(self.settings.get("proxy_enabled"))
        form.addRow(self.enable_cb)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["HTTP", "SOCKS5"])
        self.type_combo.setCurrentText(self.settings.get("proxy_type"))
        form.addRow(QLabel("Proxy Type:"), self.type_combo)

        self.host_input = QLineEdit()
        self.host_input.setText(self.settings.get("proxy_host"))
        form.addRow(QLabel("Host/IP:"), self.host_input)

        self.port_input = QLineEdit()
        self.port_input.setText(str(self.settings.get("proxy_port")))
        form.addRow(QLabel("Port:"), self.port_input)

        self.user_input = QLineEdit()
        self.user_input.setText(self.settings.get("proxy_user"))
        self.user_input.setPlaceholderText("Optional")
        form.addRow(QLabel("Username:"), self.user_input)

        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.setText(self.settings.get("proxy_pass"))
        self.pass_input.setPlaceholderText("Optional")
        form.addRow(QLabel("Password:"), self.pass_input)

        layout.addLayout(form)

        # Buttons
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.clicked.connect(self.save_settings)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.save_btn)
        
        layout.addLayout(btn_layout)

    def save_settings(self):
        host = self.host_input.text().strip()
        port_str = self.port_input.text().strip()

        if self.enable_cb.isChecked() and (not host or not port_str):
            QMessageBox.warning(self, "Invalid Inputs", "Host and Port cannot be empty if proxy is enabled.")
            return

        try:
            port = int(port_str)
            if not (0 <= port <= 65535):
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "Invalid Port", "Port must be a number between 0 and 65535.")
            return

        self.settings.set("proxy_enabled", self.enable_cb.isChecked())
        self.settings.set("proxy_type", self.type_combo.currentText())
        self.settings.set("proxy_host", host)
        self.settings.set("proxy_port", port)
        self.settings.set("proxy_user", self.user_input.text())
        self.settings.set("proxy_pass", self.pass_input.text())

        apply_proxy(self.settings)
        self.accept()
