import sys
import os
import json
import shutil
import zipfile
import subprocess
from uuid import uuid1
from random_username.generate import generate_username
from subprocess import call

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QComboBox, QProgressBar,
                             QDialog, QSpinBox, QFileDialog, QMessageBox, QListWidget,
                             QListWidgetItem, QInputDialog, QPlainTextEdit, QDockWidget,QSplashScreen)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QPixmap, QIcon
import minecraft_launcher_lib
from minecraft_launcher_lib.utils import get_minecraft_directory, get_installed_versions
from minecraft_launcher_lib.fabric import install_fabric
from minecraft_launcher_lib.forge import install_forge_version
from minecraft_launcher_lib.command import get_minecraft_command
from minecraft_launcher_lib.exceptions import VersionNotFound

###############################################################################
# JSON-based config helpers to remember user settings across launches
###############################################################################
CONFIG_FILE = "launcher_config.json"

def load_config():
    """
    Loads a JSON config file if it exists, otherwise returns defaults.
    """
    default_config = {
        "minecraft_directory": get_minecraft_directory(),
        "ram": 2048,
        "mod_loader": "None",       # "None", "Fabric", or "Forge"
        "mods_folder": "",
        "extra_jvm_args": "",
        "username": "",
        "server": "",
        # We'll store multiple offline accounts in a list of dicts: [{"name": "User1"}, ...]
        "accounts": []
    }
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge loaded data with defaults to avoid missing keys
            default_config.update(data)
        except Exception:
            pass
    return default_config

def save_config(config_data):
    """
    Saves the given config dictionary to a JSON file.
    """
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
    except Exception as e:
        print(f"Failed to save config: {e}")

###############################################################################
# Simple function to open folder in system file explorer
###############################################################################
def open_folder_in_explorer(path):
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform.startswith("darwin"):
        subprocess.call(["open", path])
    else:
        subprocess.call(["xdg-open", path])

###############################################################################
# Modpack Loader
###############################################################################
def load_modpack(zip_path, mc_dir):
    """
    Extracts the contents of `zip_path` (a .zip file) into `mc_dir`.
    Overwrites files if there's a collision.
    """
    if not os.path.isfile(zip_path):
        return

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(mc_dir)
    except Exception as e:
        print(f"Failed to load modpack: {e}")

###############################################################################
# Console Capture
###############################################################################
class EmittingStream:
    """
    A fake file-like stream that redirects writes to a signal.
    """
    def __init__(self, write_callback):
        self.write_callback = write_callback

    def write(self, text):
        if text:
            self.write_callback(text)

    def flush(self):
        pass

###############################################################################
# Account Manager Dialog
###############################################################################
class AccountManagerDialog(QDialog):
    """
    Simple offline "Account Manager" that stores multiple usernames.
    You can add or remove accounts, and pick which one is active.
    """
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Account Manager")
        self.setGeometry(400, 400, 300, 300)

        self.config = config

        # A list of accounts from config["accounts"], each is {"name": "Username"}
        self.accounts_list = QListWidget()
        self.load_accounts()

        self.add_button = QPushButton("Add Account")
        self.remove_button = QPushButton("Remove Selected")
        self.select_button = QPushButton("Use Selected")

        self.add_button.clicked.connect(self.add_account)
        self.remove_button.clicked.connect(self.remove_selected_account)
        self.select_button.clicked.connect(self.use_selected_account)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Offline Accounts:"))
        layout.addWidget(self.accounts_list)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.add_button)
        btn_layout.addWidget(self.remove_button)
        btn_layout.addWidget(self.select_button)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def load_accounts(self):
        """
        Populate the QListWidget with the accounts from config.
        """
        self.accounts_list.clear()
        for acc in self.config.get("accounts", []):
            item = QListWidgetItem(acc["name"])
            self.accounts_list.addItem(item)

    def add_account(self):
        """
        Prompt user for a username, then add it to the config.
        """
        text, ok = QInputDialog.getText(self, "Add Offline Account", "Enter username:")
        if ok and text.strip():
            new_acc = {"name": text.strip()}
            # Append to config
            self.config.setdefault("accounts", []).append(new_acc)
            self.load_accounts()

    def remove_selected_account(self):
        """
        Remove the selected account from the list and config.
        """
        selected_item = self.accounts_list.currentItem()
        if selected_item:
            username_to_remove = selected_item.text()
            # Remove from config
            accounts = self.config.setdefault("accounts", [])
            accounts[:] = [a for a in accounts if a["name"] != username_to_remove]
            self.load_accounts()

    def use_selected_account(self):
        """
        Sets the config["username"] to the selected account, then closes.
        """
        selected_item = self.accounts_list.currentItem()
        if selected_item:
            self.config["username"] = selected_item.text()
            save_config(self.config)
            QMessageBox.information(self, "Account Selected",
                                    f"Using account: {selected_item.text()}")
            self.accept()
        else:
            QMessageBox.warning(self, "No Selection", "Please select an account first.")

###############################################################################
# Settings Dialog
###############################################################################
class SettingsDialog(QDialog):
    """
    Extended Settings dialog with:
      - Minecraft folder selection
      - RAM selection
      - Mod Loader selection: None, Fabric, Forge
      - Mods folder selection
      - Extra JVM arguments
    """
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setGeometry(300, 300, 500, 380)

        self.config = config  # We'll modify this dict and save it later

        # Minecraft folder
        self.minecraft_folder_label = QLabel("Minecraft Folder:")
        self.minecraft_folder_edit = QLineEdit(self)
        self.browse_mc_button = QPushButton("Browse", self)
        self.browse_mc_button.clicked.connect(self.browse_minecraft_folder)

        # RAM
        self.ram_label = QLabel("RAM (MB):")
        self.ram_spin = QSpinBox(self)
        self.ram_spin.setRange(512, 32768)

        # Mod Loader
        self.loader_label = QLabel("Mod Loader:")
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(["None", "Fabric", "Forge"])

        # Mods folder
        self.mods_folder_label = QLabel("Mods Folder (Optional):")
        self.mods_folder_edit = QLineEdit(self)
        self.browse_mods_button = QPushButton("Browse", self)
        self.browse_mods_button.clicked.connect(self.browse_mods_folder)

        # Extra JVM arguments
        self.jvm_label = QLabel("Extra JVM Arguments:")
        self.jvm_edit = QLineEdit(self)

        # Button to open mods folder in system file explorer
        self.open_mods_folder_button = QPushButton("Open Mods Folder")
        self.open_mods_folder_button.clicked.connect(self.open_mods_folder)

        # Layout
        layout = QVBoxLayout()

        layout.addWidget(self.minecraft_folder_label)
        layout.addWidget(self.minecraft_folder_edit)
        layout.addWidget(self.browse_mc_button)

        layout.addWidget(self.ram_label)
        layout.addWidget(self.ram_spin)

        layout.addWidget(self.loader_label)
        layout.addWidget(self.loader_combo)

        layout.addWidget(self.mods_folder_label)
        layout.addWidget(self.mods_folder_edit)
        layout.addWidget(self.browse_mods_button)

        layout.addWidget(self.jvm_label)
        layout.addWidget(self.jvm_edit)

        layout.addWidget(self.open_mods_folder_button)

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_and_close)
        layout.addWidget(save_button)

        self.setLayout(layout)
        self.load_defaults()

    def load_defaults(self):
        """
        Populate fields from the current config dictionary.
        """
        self.minecraft_folder_edit.setText(self.config.get("minecraft_directory", get_minecraft_directory()))
        self.ram_spin.setValue(self.config.get("ram", 2048))
        mod_loader = self.config.get("mod_loader", "None")
        index = ["None", "Fabric", "Forge"].index(mod_loader) if mod_loader in ["None", "Fabric", "Forge"] else 0
        self.loader_combo.setCurrentIndex(index)
        self.mods_folder_edit.setText(self.config.get("mods_folder", ""))
        self.jvm_edit.setText(self.config.get("extra_jvm_args", ""))

    def browse_minecraft_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Minecraft Folder")
        if folder:
            self.minecraft_folder_edit.setText(folder)

    def browse_mods_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Mods Folder")
        if folder:
            self.mods_folder_edit.setText(folder)

    def open_mods_folder(self):
        """
        Opens the configured mods folder if set, otherwise the default .minecraft/mods folder.
        """
        mods_folder = self.mods_folder_edit.text().strip()
        if not mods_folder:
            # Use the default .minecraft/mods path
            mc_dir = self.minecraft_folder_edit.text().strip() or get_minecraft_directory()
            mods_folder = os.path.join(mc_dir, "mods")

        if not os.path.exists(mods_folder):
            os.makedirs(mods_folder, exist_ok=True)

        open_folder_in_explorer(mods_folder)

    def save_and_close(self):
        """
        Save the settings back to self.config and accept.
        """
        self.config["minecraft_directory"] = self.minecraft_folder_edit.text()
        self.config["ram"] = self.ram_spin.value()
        self.config["mod_loader"] = self.loader_combo.currentText()
        self.config["mods_folder"] = self.mods_folder_edit.text()
        self.config["extra_jvm_args"] = self.jvm_edit.text()
        self.accept()

###############################################################################
# Launch Thread
###############################################################################
class LaunchThread(QThread):
    """
    This QThread installs Fabric/Forge if needed, copies mods, and launches Minecraft.
    """
    # We'll pass in: version_id only; everything else is in self.config
    launch_signal = pyqtSignal(str)  # version_id

    # For progress updates and error handling
    progress_signal = pyqtSignal(int, int, str)
    state_signal = pyqtSignal(bool)
    error_signal = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.version_id = ""
        self.launch_signal.connect(self.setup_launch)

    def setup_launch(self, version_id):
        self.version_id = version_id

    def run(self):
        self.state_signal.emit(True)

        # 1) Ensure we have a username
        username = self.config.get("username", "").strip()
        if not username:
            username = generate_username()[0]
            self.config["username"] = username

        # 2) Potentially install Fabric or Forge
        final_version_id = self.version_id
        mod_loader = self.config.get("mod_loader", "None")
        mc_dir = self.config.get("minecraft_directory", get_minecraft_directory())

        try:
            if mod_loader == "Fabric":
                fabric_version_id = install_fabric(
                    final_version_id,
                    mc_dir,
                    callback={
                        "setStatus": lambda text: self.progress_signal.emit(0, 0, text),
                        "setProgress": lambda val: self.progress_signal.emit(val, 100, "Installing Fabric..."),
                        "setMax": lambda val: None
                    }
                )
                final_version_id = fabric_version_id
            elif mod_loader == "Forge":
                install_forge_version(
                    final_version_id,
                    mc_dir,
                    callback={
                        "setStatus": lambda text: self.progress_signal.emit(0, 0, text),
                        "setProgress": lambda val: self.progress_signal.emit(val, 100, "Installing Forge..."),
                        "setMax": lambda val: None
                    }
                )
        except Exception as e:
            self.error_signal.emit(f"Failed to install {mod_loader}: {str(e)}")
            self.state_signal.emit(False)
            return

        # 3) Copy mods if we have a mods folder and if mod loader is not "None"
        mods_folder = self.config.get("mods_folder", "").strip()
        if mods_folder and mod_loader in ["Fabric", "Forge"]:
            try:
                dest_mods = os.path.join(mc_dir, "mods")
                if not os.path.isdir(dest_mods):
                    os.makedirs(dest_mods, exist_ok=True)
                for file_name in os.listdir(mods_folder):
                    src = os.path.join(mods_folder, file_name)
                    if os.path.isfile(src):
                        shutil.copy2(src, dest_mods)
            except Exception as e:
                self.error_signal.emit(f"Failed to copy mods: {str(e)}")
                # Not a fatal error; keep going.

        # 4) Build the launch options
        ram = self.config.get("ram", 2048)
        extra_jvm_args = self.config.get("extra_jvm_args", "").strip()
        server = self.config.get("server", "").strip()

        options = {
            "username": username,
            "uuid": str(uuid1()),
            "token": "",
            "jvmArguments": [f"-Xmx{ram}M", f"-Xms{ram}M"]
        }
        if extra_jvm_args:
            options["jvmArguments"].extend(extra_jvm_args.split())

        if server:
            if ":" in server:
                host, port_str = server.split(":", 1)
                options["server"] = host
                try:
                    options["port"] = int(port_str)
                except ValueError:
                    self.error_signal.emit(f"Invalid port: {port_str}")
                    self.state_signal.emit(False)
                    return
            else:
                options["server"] = server

        # 5) Build the Minecraft command
        try:
            command = get_minecraft_command(
                version=final_version_id,
                minecraft_directory=mc_dir,
                options=options
            )
        except VersionNotFound:
            self.error_signal.emit(f"Version not found: {final_version_id}")
            self.state_signal.emit(False)
            return
        except Exception as e:
            self.error_signal.emit(f"Error building launch command: {str(e)}")
            self.state_signal.emit(False)
            return

        # 6) Launch
        try:
            call(command)
        except Exception as e:
            self.error_signal.emit(f"Error launching Minecraft: {str(e)}")

        self.state_signal.emit(False)

###############################################################################
# Main Window
###############################################################################
class MainWindow(QMainWindow):
    """
    Main launcher window with:
      - Installed versions combo
      - Username
      - Server
      - "Play", "Settings", "Open Mods Folder", "Account Manager", "Load Modpack"
      - Progress bar
      - JSON-based config
      - Dockable console
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Command Launcher Client")
        self.setWindowIcon(QIcon("Command_Block_(Story_Mode).ico"))  # Replace with your own icon if desired
        self.resize(700, 550)

        # Load or create config
        self.config = load_config()

        # Launch thread
        self.launch_thread = LaunchThread(self.config)
        self.launch_thread.progress_signal.connect(self.update_progress)
        self.launch_thread.state_signal.connect(self.on_state_change)
        self.launch_thread.error_signal.connect(self.show_error)

        # Main layout
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)

        # Optional logo
        self.logo_label = QLabel()
        self.logo_label.setPixmap(QPixmap("assets/title.png"))  # Replace with an actual image if desired
        self.logo_label.setScaledContents(True)
        self.logo_label.setFixedHeight(620)
        main_layout.addWidget(self.logo_label, alignment=Qt.AlignHCenter)

        # Username
        user_layout = QHBoxLayout()
        user_layout.addWidget(QLabel("Username:"))
        self.username_line = QLineEdit()
        self.username_line.setPlaceholderText("Leave blank for random")
        user_layout.addWidget(self.username_line)
        main_layout.addLayout(user_layout)

        # Server
        server_layout = QHBoxLayout()
        server_layout.addWidget(QLabel("Server:"))
        self.server_line = QLineEdit()
        self.server_line.setPlaceholderText("e.g. myserver.com or 127.0.0.1:25565")
        server_layout.addWidget(self.server_line)
        main_layout.addLayout(server_layout)

        # Versions
        version_layout = QHBoxLayout()
        version_layout.addWidget(QLabel("Version:"))
        self.version_combo = QComboBox()
        version_layout.addWidget(self.version_combo)
        main_layout.addLayout(version_layout)

        # Progress
        self.progress_label = QLabel("")
        self.progress_label.setVisible(False)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_label)
        main_layout.addWidget(self.progress_bar)

        # Buttons row
        btn_layout = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.play_game)

        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.open_settings)

        self.open_mods_button = QPushButton("Open Mods Folder")
        self.open_mods_button.clicked.connect(self.open_mods_folder_main)

        self.account_manager_button = QPushButton("Account Manager")
        self.account_manager_button.clicked.connect(self.open_account_manager)

        self.modpack_button = QPushButton("Load Modpack")
        self.modpack_button.clicked.connect(self.load_modpack_action)

        btn_layout.addWidget(self.play_button)
        btn_layout.addWidget(self.settings_button)
        btn_layout.addWidget(self.open_mods_button)
        btn_layout.addWidget(self.account_manager_button)
        btn_layout.addWidget(self.modpack_button)
        main_layout.addLayout(btn_layout)

        # Another row for console toggle
        console_btn_layout = QHBoxLayout()
        self.console_button = QPushButton("Toggle Console")
        self.console_button.clicked.connect(self.toggle_console)
        console_btn_layout.addWidget(self.console_button)
        main_layout.addLayout(console_btn_layout)

        self.setCentralWidget(central_widget)

        # Fill in UI from config
        self.username_line.setText(self.config.get("username", ""))
        self.server_line.setText(self.config.get("server", ""))

        # Populate versions from the chosen .minecraft directory
        self.load_installed_versions()

        # Add a dockable console
        self.console_dock = QDockWidget("Console Output", self)
        self.console_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea)
        self.console_text = QPlainTextEdit()
        self.console_text.setReadOnly(True)
        self.console_dock.setWidget(self.console_text)
        self.addDockWidget(Qt.RightDockWidgetArea, self.console_dock)
        self.console_dock.hide()  # hidden by default

        # Capture Python stdout/stderr to the console
        sys.stdout = EmittingStream(self.append_console_text)
        sys.stderr = EmittingStream(self.append_console_text)

    def load_installed_versions(self):
        """
        Get all installed versions from the .minecraft folder and fill combo box.
        """
        mc_dir = self.config.get("minecraft_directory", get_minecraft_directory())
        installed = get_installed_versions(mc_dir)
        self.version_combo.clear()

        if not installed:
            self.version_combo.addItem("No installed versions found!")
            self.version_combo.setEnabled(False)
        else:
            self.version_combo.setEnabled(True)
            for ver_dict in installed:
                self.version_combo.addItem(ver_dict["id"])

    def play_game(self):
        if not self.version_combo.currentText() or not self.version_combo.isEnabled():
            self.show_error("No valid Minecraft version selected!")
            return

        version_id = self.version_combo.currentText()
        # Store the user inputs in config, then save config
        self.config["username"] = self.username_line.text().strip()
        self.config["server"] = self.server_line.text().strip()
        save_config(self.config)

        print(f"[Launcher] Launching version: {version_id}")
        self.launch_thread.launch_signal.emit(version_id)
        self.launch_thread.start()

    def open_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec_() == QDialog.Accepted:
            # The dialog updates self.config, so reload versions
            save_config(self.config)
            self.load_installed_versions()

    def open_account_manager(self):
        dlg = AccountManagerDialog(self.config, self)
        if dlg.exec_() == QDialog.Accepted:
            # The chosen account's username might have changed
            self.username_line.setText(self.config.get("username", ""))
            save_config(self.config)

    def open_mods_folder_main(self):
        """
        Opens the mods folder from the main window. If none is configured,
        uses the default .minecraft/mods path.
        """
        mods_folder = self.config.get("mods_folder", "").strip()
        if not mods_folder:
            # Use the default .minecraft/mods path
            mc_dir = self.config.get("minecraft_directory", get_minecraft_directory())
            mods_folder = os.path.join(mc_dir, "mods")

        if not os.path.exists(mods_folder):
            os.makedirs(mods_folder, exist_ok=True)

        open_folder_in_explorer(mods_folder)

    def load_modpack_action(self):
        """
        Prompt user to pick a .zip modpack, then extract to .minecraft
        """
        zip_path, _ = QFileDialog.getOpenFileName(self, "Select Modpack (ZIP)", "", "Zip Files (*.zip)")
        if zip_path:
            mc_dir = self.config.get("minecraft_directory", get_minecraft_directory())
            print(f"[Launcher] Loading modpack from {zip_path} into {mc_dir} ...")
            load_modpack(zip_path, mc_dir)
            print("[Launcher] Modpack loaded.")

    def toggle_console(self):
        """
        Show/hide the console dock widget.
        """
        if self.console_dock.isVisible():
            self.console_dock.hide()
        else:
            self.console_dock.show()

    def append_console_text(self, text):
        """
        Append text to the console widget.
        """
        self.console_text.moveCursor(self.console_text.textCursor().End)
        self.console_text.insertPlainText(text)

    def update_progress(self, current, maximum, text):
        self.progress_label.setText(text)
        self.progress_label.setVisible(True)
        self.progress_bar.setVisible(True)
        if maximum > 0:
            self.progress_bar.setRange(0, maximum)
        else:
            # Indeterminate if setMax was never called
            self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(current)

    def on_state_change(self, is_running):
        self.play_button.setDisabled(is_running)
        self.settings_button.setDisabled(is_running)
        self.open_mods_button.setDisabled(is_running)
        self.account_manager_button.setDisabled(is_running)
        self.modpack_button.setDisabled(is_running)
        self.console_button.setDisabled(is_running)
        if not is_running:
            # Reset progress
            self.progress_label.setText("")
            self.progress_label.setVisible(False)
            self.progress_bar.setValue(0)
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setVisible(False)

    def show_error(self, msg):
        QMessageBox.critical(self, "Error", msg)

###############################################################################
# Main Entry with QSplashScreen and updated QSS
###############################################################################
def show_main_window():
    window = MainWindow()
    window.show()

def main():
    app = QApplication(sys.argv)

    # Show splash screen for 5 seconds
    splash_pix = QPixmap("assets/title.png")  # or a separate splash image
    splash = QSplashScreen(splash_pix, Qt.WindowStaysOnTopHint)
    splash.show()

    # Use a timer to wait 5 seconds, then close splash and show main window
    QTimer.singleShot(5000, lambda: (splash.close(), show_main_window()))

    # Updated QSS for a slightly new look
    app.setStyleSheet("""
        QMainWindow {
            background-color: #252525;
        }
        QLabel {
            color: #f0f0f0;
            font-size: 12pt;
        }
        QLineEdit, QComboBox, QSpinBox, QListWidget, QPlainTextEdit {
            background-color: #3c3c3c;
            color: #ffffff;
            border: 1px solid #5a5a5a;
        }
        QPushButton {
            background-color: #646464;
            color: #ffffff;
            border: 1px solid #444444;
            padding: 6px 12px;
            margin: 3px;
        }
        QPushButton:hover {
            background-color: #777777;
        }
        QProgressBar {
            border: 1px solid #555555;
            text-align: center;
            color: #ffffff;
        }
        QProgressBar::chunk {
            background-color: #00cc00;
        }
        QMessageBox {
            background-color: #2b2b2b;
        }
        QDockWidget {
            background-color: #2b2b2b;
            titlebar-close-icon: url(none);
            titlebar-normal-icon: url(none);
        }
    """)

    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
