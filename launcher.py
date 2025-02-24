import sys
import threading
from PyQt5.QtWidgets import (QApplication, QMainWindow, QPushButton, QLabel, QWidget, QVBoxLayout,
                             QLineEdit, QComboBox, QSpacerItem, QSizePolicy, QProgressBar, QFileDialog,
                             QDialog, QHBoxLayout, QSpinBox, QMessageBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt5.QtGui import QFont, QPixmap, QIcon
import os
import json
import minecraft_launcher_lib
from minecraft_launcher_lib.forge import install_forge, list_forge_versions
from subprocess import call
from minecraft_launcher_lib.utils import get_minecraft_directory, get_version_list
from minecraft_launcher_lib.install import install_minecraft_version
from minecraft_launcher_lib.command import get_minecraft_command
from random_username.generate import generate_username
from uuid import uuid1
import requests
from functools import partial


class LoadingDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Loading')
        self.setWindowModality(Qt.ApplicationModal)
        self.setFixedSize(300, 150)
        
        layout = QVBoxLayout()
        self.progress_label = QLabel('Preparing...')
        self.progress_bar = QProgressBar()
        self.skip_button = QPushButton('Skip')
        self.skip_button.clicked.connect(self.cancel)
        
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.skip_button)
        self.setLayout(layout)
        
        self.thread = None
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        if self.thread and self.thread.isRunning():
            self.thread.terminate()
        self.reject()


class LaunchThread(QThread):
    launch_setup_signal = pyqtSignal(str, str, str, int, str)
    progress_update_signal = pyqtSignal(int, int, str)
    state_update_signal = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.launch_setup_signal.connect(self.launch_setup)
        self.version_id = ''
        self.username = ''
        self.minecraft_directory = ''
        self.ram = 0
        self.skin_file = ''

    def launch_setup(self, version_id, username, minecraft_directory, ram, skin_file=''):
        self.version_id = version_id
        self.username = username
        self.minecraft_directory = minecraft_directory
        self.ram = ram
        self.skin_file = skin_file

    def update_progress_label(self, value):
        self.progress_label = value
        self.progress_update_signal.emit(self.progress, self.progress_max, self.progress_label)

    def update_progress(self, value):
        self.progress = value
        self.progress_update_signal.emit(self.progress, self.progress_max, self.progress_label)

    def update_progress_max(self, value):
        self.progress_max = value
        self.progress_update_signal.emit(self.progress, self.progress_max, self.progress_label)

    def run(self):
        self.state_update_signal.emit(True)
        try:
            # Install base Minecraft version
            install_minecraft_version(
                versionid=self.version_id.split('-')[0],
                minecraft_directory=self.minecraft_directory,
                callback=self._get_callback()
            )

            # Install Forge if needed
            if 'forge' in self.version_id.lower():
                forge_version = self.version_id.split('-')[-1]
                install_forge(
                    minecraft_version=self.version_id.split('-')[0],
                    forge_version=forge_version,
                    path=self.minecraft_directory,
                    callback=self._get_callback()
                )

            if self.username == '':
                self.username = generate_username()[0]

            options = {
                'username': self.username,
                'uuid': str(uuid1()),
                'token': '',
                'jvmArguments': [f'-Xmx{self.ram}M']
            }

            if self.skin_file:
                options['skin'] = self.skin_file

            call(get_minecraft_command(
                version=self.version_id,
                minecraft_directory=self.minecraft_directory,
                options=options
            ))
        except Exception as e:
            print(f"Error: {str(e)}")
        finally:
            self.state_update_signal.emit(False)

    def _get_callback(self):
        return {
            'setStatus': self.update_progress_label,
            'setProgress': self.update_progress,
            'setMax': self.update_progress_max
        }


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super(SettingsDialog, self).__init__(parent)
        self.setWindowTitle('Settings')
        self.setGeometry(100, 100, 400, 200)
        self.layout = QVBoxLayout(self)

        # Minecraft Folder Settings
        self.folder_label = QLabel('Minecraft Folder:', self)
        self.folder_edit = QLineEdit(self)
        self.folder_button = QPushButton('Browse', self)
        self.folder_button.clicked.connect(self.browse_folder)

        # RAM Settings
        self.ram_label = QLabel('Set RAM (MB):', self)
        self.ram_spinbox = QSpinBox(self)
        self.ram_spinbox.setRange(512, 32768)
        self.ram_spinbox.setValue(2048)

        # Skin Settings
        self.skin_label = QLabel('Select Skin File:', self)
        self.skin_edit = QLineEdit(self)
        self.skin_button = QPushButton('Browse', self)
        self.skin_button.clicked.connect(self.browse_skin)

        # Save Button
        self.save_button = QPushButton('Save', self)
        self.save_button.clicked.connect(self.save_settings)

        # Add widgets to layout
        self.layout.addWidget(self.folder_label)
        self.layout.addWidget(self.folder_edit)
        self.layout.addWidget(self.folder_button)
        self.layout.addWidget(self.ram_label)
        self.layout.addWidget(self.ram_spinbox)
        self.layout.addWidget(self.skin_label)
        self.layout.addWidget(self.skin_edit)
        self.layout.addWidget(self.skin_button)
        self.layout.addWidget(self.save_button)

        self.load_settings()

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select Minecraft Folder')
        if folder:
            self.folder_edit.setText(folder)

    def browse_skin(self):
        skin_file, _ = QFileDialog.getOpenFileName(self, 'Select Skin File', '', 'PNG Files (*.png);;All Files (*)')
        if skin_file:
            self.skin_edit.setText(skin_file)

    def load_settings(self):
        self.folder_edit.setText(get_minecraft_directory())

    def save_settings(self):
        self.accept()

    def get_settings(self):
        return self.folder_edit.text(), self.ram_spinbox.value(), self.skin_edit.text()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Minecraft Launcher')
        self.setWindowIcon(QIcon('run1.webp'))
        self.resize(300, 350)
        self.centralwidget = QWidget(self)

        # UI Elements
        self.logo = QLabel(self.centralwidget)
        self.logo.setMaximumSize(QSize(256, 37))
        self.logo.setPixmap(QPixmap('assets/title.png').scaled(256, 37, Qt.KeepAspectRatio))
        
        self.username = QLineEdit(self.centralwidget)
        self.username.setPlaceholderText('Username')
        
        self.version_select = QComboBox(self.centralwidget)
        self.load_versions()
        
        self.start_progress_label = QLabel(self.centralwidget)
        self.start_progress = QProgressBar(self.centralwidget)
        self.start_button = QPushButton('Play', self.centralwidget)
        
        # Buttons
        self.offline_button = QPushButton('Offline Login', self.centralwidget)
        self.add_account_button = QPushButton('Add Account', self.centralwidget)
        self.save_accounts_button = QPushButton('Save Accounts', self.centralwidget)
        self.settings_button = QPushButton('Settings', self.centralwidget)

        # Layout
        self.vertical_layout = QVBoxLayout(self.centralwidget)
        self.vertical_layout.addWidget(self.logo, 0, Qt.AlignHCenter)
        self.vertical_layout.addWidget(self.username)
        self.vertical_layout.addWidget(self.version_select)
        self.vertical_layout.addWidget(self.start_progress_label)
        self.vertical_layout.addWidget(self.start_progress)
        self.vertical_layout.addWidget(self.start_button)
        self.vertical_layout.addWidget(self.offline_button)
        self.vertical_layout.addWidget(self.add_account_button)
        self.vertical_layout.addWidget(self.save_accounts_button)
        self.vertical_layout.addWidget(self.settings_button)

        # Thread and Dialog
        self.launch_thread = LaunchThread()
        self.loading_dialog = LoadingDialog(self)
        self.launch_thread.state_update_signal.connect(self.state_update)
        self.launch_thread.progress_update_signal.connect(self.update_progress)
        self.loading_dialog.rejected.connect(self.handle_skip)

        # Connections
        self.start_button.clicked.connect(self.launch_game)
        self.offline_button.clicked.connect(self.offline_login)
        self.add_account_button.clicked.connect(self.add_account)
        self.save_accounts_button.clicked.connect(self.save_accounts)
        self.settings_button.clicked.connect(self.open_settings)

        # Initialize settings
        self.minecraft_directory = get_minecraft_directory()
        self.ram = 2048
        self.skin_file = ''
        self.accounts = []

        self.setCentralWidget(self.centralwidget)

    def load_versions(self):
        self.version_select.clear()
        # Add regular versions
        for version in get_version_list():
            if version['type'] == 'release':
                self.version_select.addItem(version['id'])
        # Add Forge versions
        for forge_version in list_forge_versions():
            self.version_select.addItem(f"{forge_version['mcversion']}-forge-{forge_version['version']}")

    def state_update(self, value):
        self.start_button.setDisabled(value)
        self.offline_button.setDisabled(value)
        self.add_account_button.setDisabled(value)
        self.save_accounts_button.setDisabled(value)
        if not value:
            self.loading_dialog.close()

    def update_progress(self, progress, max_progress, label):
        self.start_progress.setValue(progress)
        self.start_progress.setMaximum(max_progress)
        self.start_progress_label.setText(label)
        self.loading_dialog.progress_bar.setValue(progress)
        self.loading_dialog.progress_bar.setMaximum(max_progress)
        self.loading_dialog.progress_label.setText(label)

    def launch_game(self):
        self.loading_dialog.show()
        self.loading_dialog.thread = self.launch_thread
        self.launch_thread.launch_setup_signal.emit(
            self.version_select.currentText(),
            self.username.text(),
            self.minecraft_directory,
            self.ram,
            self.skin_file
        )
        self.launch_thread.start()

    def handle_skip(self):
        QMessageBox.warning(self, 'Installation Cancelled', 'Game installation was cancelled by user.')

    def offline_login(self):
        if not self.username.text():
            QMessageBox.warning(self, 'Error', 'Please enter a username for offline login.')
            return
        self.launch_game()

    def add_account(self):
        username = self.username.text()
        if username:
            self.accounts.append(username)
            QMessageBox.information(self, 'Success', f'Account {username} added.')
        else:
            QMessageBox.warning(self, 'Error', 'Please enter a username to add.')

    def save_accounts(self):
        if not self.accounts:
            QMessageBox.warning(self, 'Error', 'No accounts to save.')
            return
        with open('accounts.json', 'w') as f:
            json.dump(self.accounts, f)
        QMessageBox.information(self, 'Success', 'Accounts saved successfully.')

    def open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.minecraft_directory, self.ram, self.skin_file = dialog.get_settings()


if __name__ == '__main__':
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())