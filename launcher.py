# Command Launcher Client V2.0
#
# REQUIRED LIBRARIES:
# pip install PyQt5 minecraft-launcher-lib random-username mcstatus
#
# Made by firepdx, with major feature enhancements by Gemini.

import sys
import os
import json
import shutil
import webbrowser
import zipfile
import subprocess
import time
from uuid import uuid1, UUID
from random_username.generate import generate_username
from subprocess import Popen, PIPE, STDOUT

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QComboBox, QProgressBar,
                             QDialog, QSpinBox, QFileDialog, QMessageBox, QListWidget,
                             QListWidgetItem, QInputDialog, QPlainTextEdit, QDockWidget,
                             QSplashScreen, QCheckBox, QToolButton, QMenu)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QProcess
from PyQt5.QtGui import QPixmap, QIcon

import minecraft_launcher_lib

# Optional dependency for server status ping
try:
    from mcstatus import JavaServer
    MCSTATUS_AVAILABLE = True
except ImportError:
    MCSTATUS_AVAILABLE = False


###############################################################################
# CONFIGURATION HELPERS
###############################################################################
CONFIG_FILE = "launcher_config.json"

def load_config():
    """Loads a JSON config file if it exists, otherwise returns defaults."""
    default_config = {
        "minecraft_directory": minecraft_launcher_lib.utils.get_minecraft_directory(),
        "ram": 4096,  # Increased default RAM
        "mod_loader": "None",
        "extra_jvm_args": "",
        "java_path": "", # New: For custom Java executable
        "accounts": [], # New structure: [{"uuid": "...", "name": "...", "type": "offline/msa", ...}]
        "active_account_uuid": "" # New: To track the currently selected account
    }
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            default_config.update(data)
        except Exception as e:
            print(f"Warning: Could not load config file. Using defaults. Error: {e}")
            pass
    return default_config

def save_config(config_data):
    """Saves the given config dictionary to a JSON file."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        print(f"Error: Failed to save config: {e}")

###############################################################################
# UTILITY FUNCTIONS
###############################################################################
def open_folder_in_explorer(path):
    """Opens a folder in the default file explorer, creating it if it doesn't exist."""
    try:
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform.startswith("darwin"):
            subprocess.call(["open", path])
        else:
            subprocess.call(["xdg-open", path])
    except Exception as e:
        print(f"Error opening folder {path}: {e}")

def get_profile_path(mc_dir, version_id):
    """Returns the path for a specific profile/instance."""
    return os.path.join(mc_dir, "profiles", version_id)

def load_modpack(zip_path, profile_dir):
    """Extracts a modpack ZIP into the specific profile directory."""
    if not os.path.isfile(zip_path):
        return
    try:
        print(f"Extracting modpack to {profile_dir}...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(profile_dir)
        # Ensure a "mods" folder exists inside the profile directory
        mods_path = os.path.join(profile_dir, "mods")
        if not os.path.isdir(mods_path):
             os.makedirs(mods_path)
        QMessageBox.information(None, "Modpack Loaded", f"Modpack '{os.path.basename(zip_path)}' extracted successfully.")
    except Exception as e:
        QMessageBox.critical(None, "Error", f"Failed to load modpack: {e}")

###############################################################################
# CONSOLE CAPTURE
###############################################################################
class EmittingStream:
    """A stream that redirects writes to a PyQt signal."""
    def __init__(self, write_callback):
        self.write_callback = write_callback
    def write(self, text):
        if text:
            self.write_callback(text)
    def flush(self):
        pass

###############################################################################
# WORKER THREADS (for non-blocking operations)
###############################################################################
class VersionInstallThread(QThread):
    """Installs a Minecraft version in a background thread."""
    progress_signal = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(bool, str) # Success (bool), message (str)

    def __init__(self, version_id, mc_dir):
        super().__init__()
        self.version_id = version_id
        self.mc_dir = mc_dir
        self.callback = {
            "setStatus": self.set_status,
            "setProgress": self.set_progress,
            "setMax": self.set_max
        }
        self.max_val = 0

    def set_status(self, text):
        self.progress_signal.emit(0, self.max_val, text)
    def set_progress(self, val):
        self.progress_signal.emit(val, self.max_val, f"Downloading: {val}/{self.max_val}")
    def set_max(self, val):
        self.max_val = val

    def run(self):
        try:
            minecraft_launcher_lib.install.install_minecraft_version(self.version_id, self.mc_dir, callback=self.callback)
            self.finished_signal.emit(True, f"Version {self.version_id} installed successfully!")
        except Exception as e:
            self.finished_signal.emit(False, f"Installation failed: {e}")

class ServerPingThread(QThread):
    """Pings a Minecraft server in the background."""
    result_signal = pyqtSignal(str)

    def __init__(self, server_address):
        super().__init__()
        self.server_address = server_address

    def run(self):
        if not MCSTATUS_AVAILABLE:
            self.result_signal.emit("Error: mcstatus library not found.\nPlease run: pip install mcstatus")
            return
        try:
            server = JavaServer.lookup(self.server_address)
            status = server.status()
            result = (f"✅ Server Online!\n"
                      f"Version: {status.version.name}\n"
                      f"Players: {status.players.online}/{status.players.max}\n"
                      f"MOTD: {status.description}")
            self.result_signal.emit(result)
        except Exception as e:
            self.result_signal.emit(f"❌ Could not connect to server.\nReason: {e}")

###############################################################################
# DIALOGS
###############################################################################
class VersionInstallDialog(QDialog):
    """Dialog to list and install all available Minecraft versions."""
    version_installed = pyqtSignal() # Emitted when an install finishes

    def __init__(self, mc_dir, parent=None):
        super().__init__(parent)
        self.mc_dir = mc_dir
        self.install_thread = None

        self.setWindowTitle("Install Minecraft Version")
        self.setGeometry(300, 300, 400, 500)
        layout = QVBoxLayout(self)

        # Filters
        filter_layout = QHBoxLayout()
        self.show_releases = QCheckBox("Releases")
        self.show_releases.setChecked(True)
        self.show_snapshots = QCheckBox("Snapshots")
        self.show_old_beta = QCheckBox("Beta")
        self.show_old_alpha = QCheckBox("Alpha")
        filter_layout.addWidget(self.show_releases)
        filter_layout.addWidget(self.show_snapshots)
        filter_layout.addWidget(self.show_old_beta)
        filter_layout.addWidget(self.show_old_alpha)
        layout.addLayout(filter_layout)

        # Connect filters to update function
        for checkbox in [self.show_releases, self.show_snapshots, self.show_old_beta, self.show_old_alpha]:
            checkbox.stateChanged.connect(self.update_version_list)

        # Version list
        self.version_list_widget = QListWidget()
        layout.addWidget(self.version_list_widget)

        # Progress bar and label
        self.progress_label = QLabel("")
        self.progress_bar = QProgressBar()
        self.progress_label.hide()
        self.progress_bar.hide()
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)

        # Install button
        self.install_button = QPushButton("Install Selected Version")
        self.install_button.clicked.connect(self.install_version)
        layout.addWidget(self.install_button)

        self.all_versions = minecraft_launcher_lib.utils.get_version_list()
        self.update_version_list()

    def update_version_list(self):
        self.version_list_widget.clear()
        show_types = []
        if self.show_releases.isChecked(): show_types.append("release")
        if self.show_snapshots.isChecked(): show_types.append("snapshot")
        if self.show_old_beta.isChecked(): show_types.append("old_beta")
        if self.show_old_alpha.isChecked(): show_types.append("old_alpha")

        for version in self.all_versions:
            if version['type'] in show_types:
                self.version_list_widget.addItem(QListWidgetItem(version['id']))

    def install_version(self):
        selected_item = self.version_list_widget.currentItem()
        if not selected_item:
            QMessageBox.warning(self, "No Selection", "Please select a version to install.")
            return

        version_id = selected_item.text()
        reply = QMessageBox.question(self, "Confirm Installation", f"Are you sure you want to install Minecraft {version_id}?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.install_button.setDisabled(True)
            self.progress_label.show()
            self.progress_bar.show()

            self.install_thread = VersionInstallThread(version_id, self.mc_dir)
            self.install_thread.progress_signal.connect(self.update_progress)
            self.install_thread.finished_signal.connect(self.on_install_finished)
            self.install_thread.start()

    def update_progress(self, current, maximum, text):
        self.progress_label.setText(text)
        self.progress_bar.setRange(0, maximum)
        self.progress_bar.setValue(current)

    def on_install_finished(self, success, message):
        self.install_button.setDisabled(False)
        self.progress_label.hide()
        self.progress_bar.hide()
        if success:
            QMessageBox.information(self, "Success", message)
            self.version_installed.emit()
        else:
            QMessageBox.critical(self, "Error", message)

class AccountManagerDialog(QDialog):
    """Dialog to manage both offline and Microsoft accounts."""
    account_changed = pyqtSignal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Account Manager")
        self.setGeometry(400, 400, 400, 350)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Available Accounts:"))
        self.accounts_list = QListWidget()
        self.accounts_list.itemDoubleClicked.connect(self.use_selected_account)
        layout.addWidget(self.accounts_list)

        btn_layout = QHBoxLayout()
        self.add_msa_button = QPushButton("Add Microsoft Account")
        self.add_offline_button = QPushButton("Add Offline Account")
        btn_layout.addWidget(self.add_msa_button)
        btn_layout.addWidget(self.add_offline_button)
        layout.addLayout(btn_layout)

        action_layout = QHBoxLayout()
        self.remove_button = QPushButton("Remove Selected")
        self.select_button = QPushButton("Use Selected Account")
        action_layout.addWidget(self.remove_button)
        action_layout.addWidget(self.select_button)
        layout.addLayout(action_layout)

        self.add_msa_button.clicked.connect(self.add_msa_account)
        self.add_offline_button.clicked.connect(self.add_offline_account)
        self.remove_button.clicked.connect(self.remove_selected_account)
        self.select_button.clicked.connect(self.use_selected_account)

        self.load_accounts()

    def load_accounts(self):
        self.accounts_list.clear()
        for acc in self.config.get("accounts", []):
            acc_type = "MSA" if acc.get("type") == "msa" else "Offline"
            display_text = f"[{acc_type}] {acc['name']}"
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, acc['uuid']) # Store UUID in item data
            self.accounts_list.addItem(item)
            if acc['uuid'] == self.config.get("active_account_uuid"):
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setText(f"▶ {display_text}")

    def add_msa_account(self):
        login_url, code = minecraft_launcher_lib.microsoft_account.get_login_url()
        msg_box = QMessageBox()
        msg_box.setWindowTitle("Microsoft Login")
        msg_box.setTextFormat(Qt.RichText)
        msg_box.setText(f"Please open the following link in your browser and enter the code below.<br><br>"
                      f"<a href='{login_url}'>{login_url}</a><br><br>"
                      f"<b>Code: {code}</b><br><br>"
                      "The launcher will continue automatically once you log in.")
        msg_box.exec_()
        
        try:
            auth_data = minecraft_launcher_lib.microsoft_account.get_secure_login(code)
            
            # Check if account already exists
            if any(acc['uuid'] == auth_data["id"] for acc in self.config["accounts"]):
                QMessageBox.warning(self, "Account Exists", "This Microsoft account is already registered.")
                return

            new_acc = {
                "type": "msa",
                "uuid": auth_data["id"],
                "name": auth_data["name"],
                "token": auth_data["access_token"],
                "refresh_token": auth_data["refresh_token"]
            }
            self.config.setdefault("accounts", []).append(new_acc)
            self.config["active_account_uuid"] = new_acc["uuid"] # Auto-select new account
            self.load_accounts()
            self.account_changed.emit()
            QMessageBox.information(self, "Success", f"Microsoft account '{new_acc['name']}' added successfully!")

        except Exception as e:
            QMessageBox.critical(self, "Login Failed", f"The Microsoft login failed.\nError: {e}")

    def add_offline_account(self):
        text, ok = QInputDialog.getText(self, "Add Offline Account", "Enter username:")
        if ok and text.strip():
            username = text.strip()
            # Check if account name already exists
            if any(acc['name'].lower() == username.lower() for acc in self.config["accounts"]):
                QMessageBox.warning(self, "Account Exists", "An account with this username already exists.")
                return

            new_acc = {"type": "offline", "name": username, "uuid": str(uuid1())}
            self.config.setdefault("accounts", []).append(new_acc)
            self.load_accounts()
            self.account_changed.emit()

    def remove_selected_account(self):
        selected_item = self.accounts_list.currentItem()
        if selected_item:
            uuid_to_remove = selected_item.data(Qt.UserRole)
            self.config["accounts"] = [a for a in self.config["accounts"] if a["uuid"] != uuid_to_remove]
            
            # If the active account was removed, unset it
            if self.config["active_account_uuid"] == uuid_to_remove:
                self.config["active_account_uuid"] = ""

            self.load_accounts()
            self.account_changed.emit()

    def use_selected_account(self):
        selected_item = self.accounts_list.currentItem()
        if selected_item:
            self.config["active_account_uuid"] = selected_item.data(Qt.UserRole)
            self.account_changed.emit()
            self.load_accounts() # Refresh bolding
            self.accept()
        else:
            QMessageBox.warning(self, "No Selection", "Please select an account first.")

class SettingsDialog(QDialog):
    """Dialog for advanced launcher settings."""
    def __init__(self, config, main_window, parent=None):
        super().__init__(parent)
        self.config = config
        self.main_window = main_window # To get current version for mods folder
        self.setWindowTitle("Settings")
        self.setGeometry(300, 300, 500, 450)
        layout = QVBoxLayout(self)

        # Minecraft folder
        layout.addWidget(QLabel("Minecraft Folder:"))
        mc_layout = QHBoxLayout()
        self.minecraft_folder_edit = QLineEdit(self.config.get("minecraft_directory"))
        mc_layout.addWidget(self.minecraft_folder_edit)
        browse_mc_button = QPushButton("Browse")
        browse_mc_button.clicked.connect(self.browse_minecraft_folder)
        mc_layout.addWidget(browse_mc_button)
        layout.addLayout(mc_layout)

        # Java Path
        layout.addWidget(QLabel("Custom Java Path (Optional):"))
        java_layout = QHBoxLayout()
        self.java_path_edit = QLineEdit(self.config.get("java_path"))
        java_layout.addWidget(self.java_path_edit)
        browse_java_button = QPushButton("Browse")
        browse_java_button.clicked.connect(self.browse_java_path)
        java_layout.addWidget(browse_java_button)
        layout.addLayout(java_layout)

        # RAM and Mod Loader
        options_layout = QHBoxLayout()
        ram_v_layout = QVBoxLayout()
        ram_v_layout.addWidget(QLabel("RAM (MB):"))
        self.ram_spin = QSpinBox()
        self.ram_spin.setRange(1024, 32768)
        self.ram_spin.setSingleStep(512)
        self.ram_spin.setValue(self.config.get("ram", 4096))
        ram_v_layout.addWidget(self.ram_spin)
        options_layout.addLayout(ram_v_layout)

        loader_v_layout = QVBoxLayout()
        loader_v_layout.addWidget(QLabel("Mod Loader:"))
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(["None", "Fabric", "Forge"])
        self.loader_combo.setCurrentText(self.config.get("mod_loader", "None"))
        loader_v_layout.addWidget(self.loader_combo)
        options_layout.addLayout(loader_v_layout)
        layout.addLayout(options_layout)

        # JVM Arguments
        layout.addWidget(QLabel("Extra JVM Arguments:"))
        self.jvm_edit = QLineEdit(self.config.get("extra_jvm_args", ""))
        layout.addWidget(self.jvm_edit)

        # Utility Buttons
        util_layout = QHBoxLayout()
        clear_mods_button = QPushButton("Clear Current Profile's Mods")
        clear_mods_button.clicked.connect(self.clear_mods_folder)
        clear_cache_button = QPushButton("Clear Launcher Cache")
        clear_cache_button.clicked.connect(self.clear_cache)
        util_layout.addWidget(clear_mods_button)
        util_layout.addWidget(clear_cache_button)
        layout.addLayout(util_layout)

        # Save Button
        save_button = QPushButton("Save and Close")
        save_button.clicked.connect(self.save_and_close)
        layout.addWidget(save_button)

    def browse_minecraft_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Minecraft Folder")
        if folder: self.minecraft_folder_edit.setText(folder)

    def browse_java_path(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Java Executable", "", "Executables (*.exe)" if sys.platform == "win32" else "All files (*)")
        if path: self.java_path_edit.setText(path)

    def clear_mods_folder(self):
        version_id = self.main_window.version_combo.currentText()
        if not version_id or "No installed versions" in version_id:
            QMessageBox.warning(self, "Warning", "Please select a valid version on the main window first.")
            return
        
        profile_dir = get_profile_path(self.config["minecraft_directory"], version_id)
        mods_dir = os.path.join(profile_dir, "mods")

        if os.path.isdir(mods_dir):
            reply = QMessageBox.question(self, "Confirm", f"This will delete all files inside:\n{mods_dir}\n\nAre you sure?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                try:
                    shutil.rmtree(mods_dir)
                    os.makedirs(mods_dir) # Recreate empty folder
                    QMessageBox.information(self, "Success", "Mods folder has been cleared.")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Could not clear mods folder: {e}")
        else:
            QMessageBox.information(self, "Info", "Mods folder does not exist for this profile yet.")

    def clear_cache(self):
        mc_dir = self.config["minecraft_directory"]
        paths_to_clear = [os.path.join(mc_dir, "assets", "indexes"), os.path.join(mc_dir, "assets", "objects"), os.path.join(mc_dir, "versions")]
        reply = QMessageBox.question(self, "Confirm", f"This will delete downloaded assets and versions, forcing a re-download on next launch. This can fix corruption issues.\n\nAre you sure?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                for path in paths_to_clear:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                QMessageBox.information(self, "Success", "Launcher cache cleared. Versions and assets will be re-downloaded as needed.")
                self.main_window.load_installed_versions() # Refresh versions list
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not clear cache: {e}")

    def save_and_close(self):
        self.config["minecraft_directory"] = self.minecraft_folder_edit.text()
        self.config["java_path"] = self.java_path_edit.text()
        self.config["ram"] = self.ram_spin.value()
        self.config["mod_loader"] = self.loader_combo.currentText()
        self.config["extra_jvm_args"] = self.jvm_edit.text()
        self.accept()

###############################################################################
# LAUNCH THREAD
###############################################################################
class LaunchThread(QThread):
    """Handles the entire game launch process in the background."""
    progress_signal = pyqtSignal(int, int, str)
    state_signal = pyqtSignal(bool)
    error_signal = pyqtSignal(str)
    output_signal = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.version_id = ""
        self.process = None
        self.process_output = ""

    def setup_launch(self, version_id):
        self.version_id = version_id

    def run(self):
        self.state_signal.emit(True)

        # 1. Get Active Account
        active_uuid = self.config.get("active_account_uuid")
        if not active_uuid:
            self.error_signal.emit("No account selected. Please select an account in the Account Manager.")
            self.state_signal.emit(False)
            return

        account = next((acc for acc in self.config["accounts"] if acc["uuid"] == active_uuid), None)
        if not account:
            self.error_signal.emit("Active account not found. Please re-select it in the Account Manager.")
            self.state_signal.emit(False)
            return
            
        # 2. Refresh MSA Token if needed
        if account["type"] == "msa":
            try:
                self.progress_signal.emit(0, 0, "Refreshing Microsoft login...")
                new_auth_data = minecraft_launcher_lib.microsoft_account.refresh_login(account["refresh_token"])
                account.update({
                    "token": new_auth_data["access_token"],
                    "refresh_token": new_auth_data["refresh_token"]
                })
                save_config(self.config) # Save the refreshed tokens
            except Exception as e:
                self.error_signal.emit(f"Failed to refresh Microsoft login: {e}\nPlease try adding the account again.")
                self.state_signal.emit(False)
                return

        # 3. Potentially install Fabric or Forge
        final_version_id = self.version_id
        mod_loader = self.config.get("mod_loader", "None")
        mc_dir = self.config.get("minecraft_directory")
        callback = {
            "setStatus": lambda text: self.progress_signal.emit(0, 0, text),
            "setProgress": lambda val: self.progress_signal.emit(val, 100, f"Installing {mod_loader}..."),
            "setMax": lambda val: None
        }

        try:
            if mod_loader == "Fabric":
                self.progress_signal.emit(0, 0, "Installing Fabric...")
                final_version_id = minecraft_launcher_lib.fabric.install_fabric(self.version_id, mc_dir, callback=callback)
            elif mod_loader == "Forge":
                self.progress_signal.emit(0, 0, "Installing Forge...")
                # Get the latest Forge version for our Minecraft version
                forge_versions = minecraft_launcher_lib.forge.list_forge_versions()
                matching_versions = [v for v in forge_versions if v.startswith(self.version_id)]
                if not matching_versions:
                    raise Exception(f"No Forge version found for Minecraft {self.version_id}")
                
                # Use the latest Forge version
                forge_version = matching_versions[0]
                minecraft_launcher_lib.forge.install_forge_version(forge_version, mc_dir, callback=callback)
                final_version_id = forge_version
        except Exception as e:
            self.error_signal.emit(f"Failed to install {mod_loader}: {str(e)}")
            self.state_signal.emit(False)
            return
            
        # 4. Build Launch Options
        ram_mb = self.config.get("ram", 4096)
        profile_dir = get_profile_path(mc_dir, self.version_id)
        
        options = {
            "username": account["name"],
            "uuid": account["uuid"],
            "token": account.get("token", ""), # Empty for offline
            "jvmArguments": [f"-Xmx{ram_mb}M", f"-Xms{ram_mb}M"],
            "gameDirectory": profile_dir,
            "executablePath": self.config.get("java_path") or self.find_java_executable()
        }
        
        # Ensure Java executable exists
        if not os.path.isfile(options["executablePath"]):
            self.error_signal.emit(f"Java executable not found at: {options['executablePath']}\nPlease set a valid Java path in Settings.")
            self.state_signal.emit(False)
            return

        extra_jvm_args = self.config.get("extra_jvm_args", "").strip()
        if extra_jvm_args:
            options["jvmArguments"].extend(extra_jvm_args.split())

        server_address = self.config.get("server", "").strip()
        if server_address:
            try:
                host, port = server_address.split(":")
                options["server"] = host
                options["port"] = int(port)
            except ValueError:
                options["server"] = server_address
                options["port"] = 25565

        # Ensure profile directory and its mods folder exist
        profile_mods_dir = os.path.join(profile_dir, "mods")
        os.makedirs(profile_mods_dir, exist_ok=True)

        # 5. Build and run command
        try:
            # Let the library find the full version ID (e.g., with Forge)
            command = minecraft_launcher_lib.command.get_minecraft_command(final_version_id, mc_dir, options)
            
            # Print command for debugging
            print(f"[Launcher] Launching with command: {' '.join(command)}")
            self.output_signal.emit(f"Launching Minecraft {final_version_id}...\n")
            
            # Launch the game
            self.process = Popen(command, stdout=PIPE, stderr=STDOUT, universal_newlines=True, bufsize=1)
            
            # Stream output to console
            for line in iter(self.process.stdout.readline, ''):
                self.output_signal.emit(line)
                
            # Wait for process to finish
            self.process.stdout.close()
            return_code = self.process.wait()
            
            if return_code != 0:
                self.output_signal.emit(f"\nMinecraft exited with error code: {return_code}")
            else:
                self.output_signal.emit("\nMinecraft exited successfully")
                
        except minecraft_launcher_lib.exceptions.VersionNotFound:
             self.error_signal.emit(f"Version '{final_version_id}' not found. It might be corrupted or a loader failed to install. Try reinstalling it.")
        except Exception as e:
            self.error_signal.emit(f"An error occurred during launch: {e}")
        finally:
            self.state_signal.emit(False)

    def find_java_executable(self):
        """Finds a suitable Java executable"""
        # Try system default
        try:
            java_path = minecraft_launcher_lib.utils.get_java_executable()
            if java_path and os.path.isfile(java_path):
                return java_path
        except:
            pass
        
        # Try common locations
        common_paths = []
        if sys.platform == "win32":
            common_paths = [
                os.path.join(os.getenv("ProgramFiles"), "Java", "jdk-*", "bin", "java.exe"),
                os.path.join(os.getenv("ProgramFiles(x86)"), "Java", "jdk-*", "bin", "java.exe"),
                os.path.join(os.getenv("ProgramFiles"), "Java", "jre*", "bin", "java.exe"),
                os.path.join(os.getenv("ProgramFiles(x86)"), "Java", "jre*", "bin", "java.exe")
            ]
        elif sys.platform == "darwin":
            common_paths = [
                "/Library/Java/JavaVirtualMachines/jdk-*.jdk/Contents/Home/bin/java",
                "/Library/Internet Plug-Ins/JavaAppletPlugin.plugin/Contents/Home/bin/java"
            ]
        else:  # Linux
            common_paths = [
                "/usr/lib/jvm/java-*-openjdk-*/bin/java",
                "/usr/lib/jvm/java-*-oracle-*/bin/java",
                "/usr/bin/java"
            ]
        
        # Search for Java in common paths
        for pattern in common_paths:
            for path in glob.glob(pattern):
                if os.path.isfile(path):
                    return path
        
        # Last resort
        return "java"  # Hope it's in PATH

###############################################################################
# MAIN WINDOW
###############################################################################
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Command Launcher Client V2.0")
        self.setWindowIcon(QIcon("Command_Block_(Story_Mode).ico"))
        self.resize(800, 600)

        self.config = load_config()

        # Threads
        self.launch_thread = LaunchThread(self.config)
        self.ping_thread = None

        # Signals
        self.launch_thread.progress_signal.connect(self.update_progress)
        self.launch_thread.state_signal.connect(self.on_state_change)
        self.launch_thread.error_signal.connect(self.show_error)
        self.launch_thread.output_signal.connect(self.append_console_text)

        # UI Setup
        self.setup_ui()
        
        # Initial state
        self.update_account_display()
        self.load_installed_versions()
        self.server_line.setText(self.config.get("server", ""))

        # Console
        self.setup_console()
    
    def setup_ui(self):
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)

        # Top Bar: Account Display
        top_bar_layout = QHBoxLayout()
        self.account_label = QLabel("No account selected")
        top_bar_layout.addWidget(self.account_label)
        top_bar_layout.addStretch()
        account_manager_button = QPushButton("Account Manager")
        account_manager_button.clicked.connect(self.open_account_manager)
        top_bar_layout.addWidget(account_manager_button)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.open_settings)
        top_bar_layout.addWidget(self.settings_button)
        main_layout.addLayout(top_bar_layout)
        
        # Logo
        self.logo_label = QLabel()
        try:
            self.logo_label.setPixmap(QPixmap("assets/title.png"))
            self.logo_label.setScaledContents(True)
            self.logo_label.setFixedSize(500, 281) # 16:9 aspect ratio
            main_layout.addWidget(self.logo_label, alignment=Qt.AlignHCenter)
        except Exception:
            pass # Logo is optional

        # Server Input
        server_layout = QHBoxLayout()
        server_layout.addWidget(QLabel("Join Server (Optional):"))
        self.server_line = QLineEdit()
        self.server_line.setPlaceholderText("e.g., myserver.com:25565")
        server_layout.addWidget(self.server_line)
        ping_button = QPushButton("Ping")
        ping_button.clicked.connect(self.ping_server)
        server_layout.addWidget(ping_button)
        main_layout.addLayout(server_layout)

        # Bottom Controls
        bottom_layout = QHBoxLayout()
        
        # Left side: Version selection
        version_v_layout = QVBoxLayout()
        version_v_layout.addWidget(QLabel("Version:"))
        self.version_combo = QComboBox()
        version_v_layout.addWidget(self.version_combo)
        bottom_layout.addLayout(version_v_layout)

        # Right side: Buttons
        self.play_button = QPushButton("PLAY")
        self.play_button.setMinimumHeight(50)
        self.play_button.setStyleSheet("font-size: 18pt; font-weight: bold;")
        self.play_button.clicked.connect(self.play_game)
        bottom_layout.addWidget(self.play_button, 1) # Give play button more space

        # Add more buttons here
        controls_v_layout = QVBoxLayout()
        install_version_button = QPushButton("Install New Version")
        install_version_button.clicked.connect(self.open_version_installer)
        
        # Game Folders menu button
        game_folders_button = QToolButton()
        game_folders_button.setText("Game Folders")
        game_folders_button.setPopupMode(QToolButton.InstantPopup)
        folder_menu = QMenu()
        folder_menu.addAction("Open Mods Folder", self.open_mods_folder)
        folder_menu.addAction("Open Resource Packs Folder", lambda: self.open_game_folder("resourcepacks"))
        folder_menu.addAction("Open Shader Packs Folder", lambda: self.open_game_folder("shaderpacks"))
        folder_menu.addAction("Open Screenshots Folder", lambda: self.open_game_folder("screenshots"))
        game_folders_button.setMenu(folder_menu)

        modpack_button = QPushButton("Load Modpack (.zip)")
        modpack_button.clicked.connect(self.load_modpack_action)

        controls_v_layout.addWidget(install_version_button)
        controls_v_layout.addWidget(game_folders_button)
        controls_v_layout.addWidget(modpack_button)
        bottom_layout.addLayout(controls_v_layout)
        
        main_layout.addLayout(bottom_layout)

        # Progress Bar
        self.progress_label = QLabel("")
        self.progress_bar = QProgressBar()
        self.progress_label.hide()
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_label)
        main_layout.addWidget(self.progress_bar)

        self.setCentralWidget(central_widget)

    def setup_console(self):
        self.console_dock = QDockWidget("Console", self)
        self.console_dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        
        console_widget = QWidget()
        console_layout = QVBoxLayout(console_widget)
        console_layout.setContentsMargins(2,2,2,2)

        self.console_text = QPlainTextEdit()
        self.console_text.setReadOnly(True)
        console_layout.addWidget(self.console_text)

        console_actions_layout = QHBoxLayout()
        self.console_button = QPushButton("Toggle Console") # Moved button
        self.console_button.setCheckable(True)
        self.console_button.toggled.connect(self.console_dock.setVisible)
        self.console_dock.visibilityChanged.connect(self.console_button.setChecked)

        save_log_button = QPushButton("Save Log")
        save_log_button.clicked.connect(self.save_console_log)
        console_actions_layout.addWidget(self.console_button)
        console_actions_layout.addWidget(save_log_button)
        console_layout.addLayout(console_actions_layout)

        self.console_dock.setWidget(console_widget)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.console_dock)
        self.console_dock.hide()

        # Redirect stdout/stderr
        sys.stdout = EmittingStream(self.append_console_text)
        sys.stderr = EmittingStream(self.append_console_text)

    def play_game(self):
        version_id = self.version_combo.currentText()
        if not version_id or "No installed versions" in version_id:
            self.show_error("No Minecraft version selected!")
            return

        # Save current server input to config before launch
        self.config["server"] = self.server_line.text().strip()
        save_config(self.config)

        print(f"[Launcher] Preparing to launch version: {version_id}")
        self.launch_thread.setup_launch(version_id)
        self.launch_thread.start()

    # DIALOG AND ACTION HANDLERS
    def open_settings(self):
        dlg = SettingsDialog(self.config, self, self)
        if dlg.exec_() == QDialog.Accepted:
            save_config(self.config)
            self.load_installed_versions() # Minecraft dir might have changed
            print("[Launcher] Settings saved.")

    def open_account_manager(self):
        dlg = AccountManagerDialog(self.config, self)
        dlg.account_changed.connect(self.update_account_display)
        dlg.exec_()
        save_config(self.config)

    def open_version_installer(self):
        dlg = VersionInstallDialog(self.config["minecraft_directory"], self)
        dlg.version_installed.connect(self.load_installed_versions)
        dlg.exec_()

    def load_modpack_action(self):
        version_id = self.version_combo.currentText()
        if not version_id or "No installed versions" in version_id:
            self.show_error("Please select a target version/profile first.")
            return

        zip_path, _ = QFileDialog.getOpenFileName(self, "Select Modpack File", "", "Zip Files (*.zip)")
        if zip_path:
            profile_dir = get_profile_path(self.config["minecraft_directory"], version_id)
            load_modpack(zip_path, profile_dir)

    # FOLDER HANDLERS
    def open_mods_folder(self):
        version_id = self.version_combo.currentText()
        if not version_id or "No installed versions" in version_id:
            self.show_error("Please select a version first to open its corresponding mods folder.")
            return
        self.open_game_folder("mods", version_id)

    def open_game_folder(self, folder_name, version_id=None):
        if version_id is None:
            version_id = self.version_combo.currentText()
            if not version_id or "No installed versions" in version_id:
                self.show_error("Please select a version first.")
                return

        profile_dir = get_profile_path(self.config["minecraft_directory"], version_id)
        target_path = os.path.join(profile_dir, folder_name)
        open_folder_in_explorer(target_path)

    # UTILITY AND UI UPDATE METHODS
    def load_installed_versions(self):
        mc_dir = self.config.get("minecraft_directory")
        self.version_combo.clear()
        try:
            installed = minecraft_launcher_lib.utils.get_installed_versions(mc_dir)
            if not installed:
                self.version_combo.addItem("No installed versions found!")
                self.version_combo.setEnabled(False)
            else:
                self.version_combo.setEnabled(True)
                # Sort versions, perhaps putting latest release first
                installed.sort(key=lambda x: x.get('releaseTime', ''), reverse=True)
                for ver in installed:
                    self.version_combo.addItem(ver["id"])
        except Exception as e:
            self.show_error(f"Failed to load versions from '{mc_dir}'. Error: {e}")
            self.version_combo.addItem("Error loading versions!")
            self.version_combo.setEnabled(False)


    def update_account_display(self):
        active_uuid = self.config.get("active_account_uuid")
        account = next((acc for acc in self.config["accounts"] if acc["uuid"] == active_uuid), None)
        if account:
            acc_type = "MSA" if account["type"] == "msa" else "Offline"
            self.account_label.setText(f"Logged in as: <b>{account['name']}</b> ({acc_type})")
        else:
            self.account_label.setText("<i>No account selected. Please use the Account Manager.</i>")

    def ping_server(self):
        server_address = self.server_line.text().strip()
        if not server_address:
            self.show_error("Please enter a server address to ping.")
            return

        self.ping_thread = ServerPingThread(server_address)
        self.ping_thread.result_signal.connect(lambda result: QMessageBox.information(self, "Server Status", result))
        self.ping_thread.start()

    def append_console_text(self, text):
        self.console_text.moveCursor(self.console_text.textCursor().End)
        self.console_text.insertPlainText(text)

    def save_console_log(self):
        log_content = self.console_text.toPlainText()
        if not log_content:
            QMessageBox.information(self, "Empty Log", "There is nothing in the console to save.")
            return
        
        path, _ = QFileDialog.getSaveFileName(self, "Save Log File", "launcher_log.txt", "Text Files (*.txt)")
        if path:
            try:
                with open(path, "w", encoding='utf-8') as f:
                    f.write(log_content)
            except Exception as e:
                self.show_error(f"Failed to save log: {e}")

    def update_progress(self, current, maximum, text):
        self.progress_label.setText(text)
        self.progress_label.show()
        self.progress_bar.show()
        self.progress_bar.setRange(0, maximum if maximum > 0 else 0)
        self.progress_bar.setValue(current)

    def on_state_change(self, is_running):
        # Disable all major controls while the game is running
        for widget in self.centralWidget().findChildren(QPushButton) + self.centralWidget().findChildren(QToolButton):
            widget.setDisabled(is_running)
        self.console_button.setDisabled(False) # Always allow console toggle
        
        if not is_running:
            # Hide progress bar when not running
            self.progress_label.hide()
            self.progress_bar.hide()
            # Refresh config in case MSA tokens were updated
            self.config = load_config()
            self.update_account_display()

    def show_error(self, msg):
        print(f"[ERROR] {msg}")
        QMessageBox.critical(self, "Error", msg)

    def closeEvent(self, event):
        """Ensures config is saved on exit."""
        save_config(self.config)
        event.accept()

###############################################################################
# APPLICATION ENTRY POINT
###############################################################################
def main():
    app = QApplication(sys.argv)
    
    # Splash Screen
    splash_pix = QPixmap("assets/title.png") # Make sure this asset exists
    splash = QSplashScreen(splash_pix, Qt.WindowStaysOnTopHint)
    splash.show()

    # Main Window (created but not shown yet)
    window = MainWindow()

    # Hide splash and show main window after a delay
    QTimer.singleShot(3000, lambda: (splash.close(), window.show()))

    # Global Stylesheet (QSS)
    app.setStyleSheet("""
        QMainWindow, QDialog {
            background-color: #2E2F30;
            color: #E0E0E0;
        }
        QLabel {
            color: #E0E0E0;
            font-size: 10pt;
        }
        QLineEdit, QComboBox, QSpinBox, QListWidget, QPlainTextEdit {
            background-color: #3C3F41;
            color: #E0E0E0;
            border: 1px solid #555555;
            padding: 4px;
            font-size: 10pt;
        }
        QPushButton, QToolButton {
            background-color: #4A4D50;
            color: #FFFFFF;
            border: 1px solid #606366;
            padding: 6px 12px;
            margin: 2px;
            border-radius: 4px;
        }
        QPushButton:hover, QToolButton:hover {
            background-color: #585B5E;
        }
        QPushButton:disabled {
            background-color: #3A3D40;
            color: #888888;
        }
        QProgressBar {
            border: 1px solid #555;
            text-align: center;
            color: #FFFFFF;
            background-color: #3C3F41;
        }
        QProgressBar::chunk {
            background-color: #007ACC;
            width: 10px;
            margin: 0.5px;
        }
        QDockWidget {
            titlebar-close-icon: url(none);
            titlebar-normal-icon: url(none);
        }
        QDockWidget::title {
            background-color: #4A4D50;
            text-align: center;
            padding: 4px;
        }
        QMessageBox {
            background-color: #3C3F41;
        }
    """)

    sys.exit(app.exec_())

if __name__ == "__main__":
    # Create required asset/icon files if they don't exist
    if not os.path.isdir("assets"): os.makedirs("assets")
    # A simple placeholder icon/image if the real ones are missing
    if not os.path.isfile("Command_Block_(Story_Mode).ico"):
        try:
            from urllib import request
            request.urlretrieve("https://static.wikia.nocookie.net/minecraftstorymode/images/d/d4/Command_Block_%28Story_Mode%29.png/revision/latest?cb=20230225211910", "Command_Block_(Story_Mode).ico")
        except: pass
    if not os.path.isfile("assets/title.png"):
        try:
            from urllib import request
            request.urlretrieve("https://i.imgur.com/rS2Fk9z.png", "assets/title.png") # A generic placeholder
        except: pass

    main()