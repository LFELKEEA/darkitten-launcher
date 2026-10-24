from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config import APP_DIR, APP_VERSION
from ..updater import ReleaseInfo
from ..workers import UpdateDownloadWorker
from .dialogs import StyledDialog
from .widgets import colors


class UpdateDialog(StyledDialog):
    def __init__(self, main, release: ReleaseInfo) -> None:
        super().__init__(
            main,
            "发现新版本",
            "download",
            colors()["accent"],
            width=560,
        )
        self.main = main
        self.release = release
        self._worker: UpdateDownloadWorker | None = None
        self._payload = ""
        self._skip_requested = False

        self.body.setSpacing(12)
        self._build_intro()
        self._build_preserve_card()
        self._build_notes()
        self._build_progress()

        self.later_btn = self.add_button("稍后", "Secondary", self.reject)
        self.skip_btn = self.add_button("跳过此版本", "Ghost", self._skip)
        self.primary_btn = self.add_button("下载并更新", "Primary", self._primary)
        self.primary_btn.setIcon(self._icon("download", "#FFFFFF"))
        self.adjustSize()

    def _icon(self, name: str, color: str):
        from . import icons

        return icons.icon(name, color, 15)

    def _build_intro(self) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        current = QLabel(f"v{APP_VERSION}")
        current.setObjectName("Tiny")
        arrow = QLabel("→")
        arrow.setStyleSheet(f"color: {colors()['muted']}; font-size: 16px;")
        latest = QLabel(f"v{self.release.version}")
        latest.setStyleSheet(
            f"color: {colors()['accent']}; font-size: 18px; font-weight: 800;"
        )
        self.version_label = latest
        layout.addWidget(current)
        layout.addWidget(arrow)
        layout.addWidget(latest)
        layout.addStretch(1)

        self.body.addWidget(row)

        meta_parts = []
        if self.release.released:
            meta_parts.append(self.release.released)
        if self.release.size:
            meta_parts.append(self.release.size_text)
        if self.release.sha256:
            meta_parts.append(f"SHA-256 {self.release.sha256[:12]}…")
        if meta_parts:
            meta = QLabel(" · ".join(meta_parts))
            meta.setObjectName("Tiny")
            self.body.addWidget(meta)

        intro = QLabel(
            "下载完成后启动器会自动退出，替换代码并重新打开。"
            "更新过程不会触碰你的游戏数据。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

    def _build_preserve_card(self) -> None:
        card = QFrame()
        card.setObjectName("SoftCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)

        title = QLabel("会原样保留")
        title.setStyleSheet("font-weight: 700;")
        layout.addWidget(title)
        detail = QLabel(
            "游戏版本与存档 · Mod 文件 · 账户登录信息 · 好友与房间 · 皮肤 · 全部设置"
        )
        detail.setObjectName("Tiny")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        self.body.addWidget(card)

    def _build_notes(self) -> None:
        if not self.release.notes:
            return
        title = QLabel("这个版本带来了什么")
        title.setStyleSheet("font-weight: 700;")
        self.body.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(170)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(4)
        for note in self.release.notes:
            label = QLabel("• " + note)
            label.setWordWrap(True)
            label.setObjectName("Tiny")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(label)
        layout.addStretch(1)
        scroll.setWidget(container)
        self.body.addWidget(scroll)

    def _build_progress(self) -> None:
        self.progress_box = QFrame()
        self.progress_box.setObjectName("SoftCard")
        self.progress_box.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self.progress_box)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(1)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_status = QLabel("准备下载…")
        self.progress_status.setObjectName("Tiny")
        self.progress_status.setWordWrap(True)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_status)
        self.progress_box.hide()
        self.body.addWidget(self.progress_box)

    def _skip(self) -> None:
        self._skip_requested = True
        self.main.config.update_skipped_version = self.release.version
        self.main.config.save()
        self.reject()

    def _primary(self) -> None:
        if self._payload:
            self._apply()
            return
        if self._worker is not None:
            return
        self.progress_box.show()
        self.progress_bar.setValue(1)
        self.progress_status.setText("准备下载…")
        self.primary_btn.setEnabled(False)
        self.primary_btn.setText("正在下载…")
        self.skip_btn.setEnabled(False)
        self.later_btn.setText("取消下载")
        self.adjustSize()

        worker = UpdateDownloadWorker(
            self.release,
            APP_DIR / "updates",
            threads=self.main.config.download_threads,
            parent=self,
        )
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        self.main.set_status(f"正在下载 DKL {self.release.version}…", "busy")
        worker.start()

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress_bar.setValue(max(0, min(100, int(percent))))
        self.progress_status.setText(message)

    def _on_finished(self, ok: bool, message: str, payload: str) -> None:
        self._worker = None
        self.later_btn.setText("稍后")
        self.later_btn.setEnabled(True)
        self.progress_box.show()
        if not ok:
            self.progress_status.setText(message)
            self.primary_btn.setEnabled(True)
            self.primary_btn.setText("重新下载")
            self.skip_btn.setEnabled(True)
            self.main.set_status("更新下载失败", "error")
            self.main.toast(message, "error")
            return

        self._payload = payload
        self.progress_bar.setValue(100)
        self.progress_status.setText(
            "更新包已准备就绪。点击“重启并更新”，启动器会自动退出并完成替换。"
        )
        self.primary_btn.setEnabled(True)
        self.primary_btn.setText("重启并更新")
        self.primary_btn.setIcon(self._icon("refresh", "#FFFFFF"))
        self.skip_btn.setEnabled(False)
        self.main.set_status(f"DKL {self.release.version} 已下载", "success")

    def _apply(self) -> None:
        if not self._payload:
            return
        self.primary_btn.setEnabled(False)
        self.progress_status.setText("正在安排更新，启动器即将退出…")
        if self.main.apply_update(self._payload, self.release.version):
            self.accept()
        else:
            self.primary_btn.setEnabled(True)

    def reject(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_status.setText("正在取消下载…")
            self.later_btn.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._worker.cancel()
            event.ignore()
            self.progress_status.setText("正在取消下载…")
            return
        super().closeEvent(event)
