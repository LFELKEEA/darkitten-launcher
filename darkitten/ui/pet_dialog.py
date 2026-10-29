from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .. import pet
from ..config import DEFAULT_UPDATE_URL
from ..updater import format_size
from ..workers import PetDownloadWorker, PetManifestWorker
from . import icons, theme
from .dialogs import StyledDialog
from .download_center import TASK_SUCCESS
from .widgets import badge, repolish


class PetDialog(StyledDialog):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent, "LFELKEEA 桌宠", "paw", width=540)
        self.main = main
        self._release: pet.PetRelease | None = None
        self._manifest_worker: PetManifestWorker | None = None
        self._task_id: int | None = None
        self._build()
        self._restore_task()
        self._refresh_state()
        self._load_manifest()
        self.adjustSize()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        hero = QFrame()
        hero.setObjectName("SoftCard")
        hero.setAttribute(Qt.WA_StyledBackground, True)
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(14, 12, 14, 12)
        hero_layout.setSpacing(12)

        art = QLabel()
        art.setFixedSize(64, 64)
        art.setAlignment(Qt.AlignCenter)
        art.setStyleSheet(
            f"background: {theme.colors()['accent_soft']}; border-radius: 16px;"
        )
        image_path = icons.ASSETS_DIR / "chibi_face.png"
        if image_path.is_file():
            pixmap = QPixmap(str(image_path)).scaled(
                56,
                56,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            art.setPixmap(pixmap)
        else:
            art.setPixmap(icons.pixmap("paw", theme.colors()["accent"], 30))
        hero_layout.addWidget(art)

        texts = QVBoxLayout()
        texts.setSpacing(4)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name = QLabel("LFELKEEA 桌宠")
        name.setObjectName("H2")
        self.version_badge = badge("未安装", "gray")
        name_row.addWidget(name)
        name_row.addWidget(self.version_badge)
        name_row.addStretch(1)
        texts.addLayout(name_row)
        intro = QLabel("会眨眼、会闲聊的桌面小猫，装好后可以随时从这里召唤。")
        intro.setObjectName("Tiny")
        intro.setWordWrap(True)
        texts.addWidget(intro)
        hero_layout.addLayout(texts, 1)
        self.body.addWidget(hero)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.size_value = QLabel("—")
        self.release_value = QLabel("—")
        self.path_value = QLabel("—")
        self.path_value.setWordWrap(True)
        for value in (self.size_value, self.release_value, self.path_value):
            value.setObjectName("Tiny")
        grid.addWidget(QLabel("安装包大小"), 0, 0)
        grid.addWidget(self.size_value, 0, 1)
        grid.addWidget(QLabel("发布日期"), 1, 0)
        grid.addWidget(self.release_value, 1, 1)
        grid.addWidget(QLabel("安装位置"), 2, 0)
        grid.addWidget(self.path_value, 2, 1)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)

        self.notes_label = QLabel("")
        self.notes_label.setObjectName("Tiny")
        self.notes_label.setWordWrap(True)
        self.notes_label.hide()
        self.body.addWidget(self.notes_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.body.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Tiny")
        self.status_label.setWordWrap(True)
        self.body.addWidget(self.status_label)

        self.folder_btn = self.add_button("打开文件夹", "Ghost", self._open_folder)
        self.launch_btn = self.add_button("启动桌宠", "Secondary", self._launch)
        self.action_btn = self.add_button("检查中…", "Primary", self._primary)
        self.action_btn.setEnabled(False)

        self.main.download_center.task_progress.connect(self._on_task_progress)
        self.main.download_center.task_finished.connect(self._on_task_finished)

    # ------------------------------------------------------------------
    def _load_manifest(self) -> None:
        url = str(self.main.config.update_url or DEFAULT_UPDATE_URL)
        self._manifest_worker = PetManifestWorker(url, self.main)
        self._manifest_worker.ready.connect(self._on_manifest)
        self._manifest_worker.start()

    def _on_manifest(self, release, error: str) -> None:
        self._manifest_worker = None
        if error:
            self.status_label.setText(f"读取桌宠清单失败：{error}")
            if self._release is None and self._task_id is None:
                self.action_btn.setText("重新检查")
                self.action_btn.setEnabled(True)
            return
        if release is None:
            self.status_label.setText("更新清单里还没有桌宠信息。")
            if self._release is None and self._task_id is None:
                self.action_btn.setText("重新检查")
                self.action_btn.setEnabled(True)
            return
        self._release = release
        self._refresh_state()

    def _restore_task(self) -> None:
        for task in self.main.download_center.active_tasks():
            if str(task.content_type) != "pet":
                continue
            self._task_id = task.id
            self.progress.show()
            self.progress.setValue(task.progress)
            self.status_label.setText(task.message)
            self.action_btn.setText("取消下载")
            self.action_btn.setEnabled(True)
            return

    # ------------------------------------------------------------------
    def _refresh_state(self) -> None:
        installed = pet.is_installed()
        local_version = pet.installed_version()
        release = self._release
        root = pet.pet_root()

        if installed:
            local_text = f"v{local_version}" if local_version else "已安装"
            self.version_badge.setText(local_text)
            self.version_badge.setProperty("badge", "green")
            repolish(self.version_badge)
            self.path_value.setText(str(pet.pet_app_dir()))
            self.folder_btn.setEnabled(True)
            self.launch_btn.setEnabled(True)
        else:
            self.version_badge.setText("未安装")
            self.version_badge.setProperty("badge", "gray")
            repolish(self.version_badge)
            self.path_value.setText(str(pet.pet_app_dir(root)))
            self.folder_btn.setEnabled(False)
            self.launch_btn.setEnabled(False)

        if release is not None:
            self.size_value.setText(
                format_size(release.size) if release.size else "未知"
            )
            self.release_value.setText(release.released or release.version)
            if release.notes:
                self.notes_label.setText(
                    "\n".join(f"· {item}" for item in release.notes[:4])
                )
                self.notes_label.show()
            if self._task_id is None:
                if not installed:
                    self.action_btn.setText("下载桌宠")
                elif release.version and pet.is_newer(release.version, local_version):
                    self.action_btn.setText(f"更新到 v{release.version}")
                else:
                    self.action_btn.setText("重新下载")
                self.action_btn.setEnabled(True)
            if installed and not self.status_label.text():
                self.status_label.setText("桌宠已安装，可以直接启动。")
        else:
            self.size_value.setText("—")
            self.release_value.setText("—")

    # ------------------------------------------------------------------
    def _primary(self) -> None:
        if self._task_id is not None:
            self.main.download_center.cancel(self._task_id)
            self.action_btn.setEnabled(False)
            self.action_btn.setText("正在取消…")
            return
        if self._release is None:
            self.action_btn.setEnabled(False)
            self.action_btn.setText("检查中…")
            self._load_manifest()
            return
        self._start_download(self._release)

    def _start_download(self, release: pet.PetRelease) -> None:
        worker = PetDownloadWorker(
            release,
            threads=int(self.main.config.download_threads or 4),
            parent=self.main,
        )
        task = self.main.download_center.start_worker(
            worker,
            title=f"桌宠 {release.version}",
            content_type="pet",
            target=pet.pet_root(),
            source="github-release",
            metadata={"pet_version": release.version},
        )
        self._task_id = task.id
        self.progress.setValue(1)
        self.progress.show()
        self.status_label.setText("正在下载桌宠…")
        self.action_btn.setText("取消下载")
        self.action_btn.setEnabled(True)

    def _on_task_progress(self, task_id: int, percent: int, message: str) -> None:
        if task_id != self._task_id:
            return
        self.progress.show()
        self.progress.setValue(max(0, min(100, int(percent))))
        self.status_label.setText(message)

    def _on_task_finished(self, task) -> None:
        if task.id != self._task_id:
            return
        self._task_id = None
        self.progress.hide()
        if task.status == TASK_SUCCESS:
            version = self._release.version if self._release else ""
            self.status_label.setText(task.message or "桌宠已安装")
            self.main.toast(f"桌宠 {version} 安装完成", "success")
        else:
            self.status_label.setText(task.message or "桌宠下载失败")
            self.main.toast(task.message or "桌宠下载失败", "error")
        self._refresh_state()

    # ------------------------------------------------------------------
    def _open_folder(self) -> None:
        try:
            pet.open_pet_folder()
        except Exception as exc:
            self.main.toast(str(exc), "error")

    def _launch(self) -> None:
        try:
            pet.launch_pet()
        except Exception as exc:
            self.main.toast(str(exc), "error")
        else:
            self.main.toast("桌宠已启动", "success")
