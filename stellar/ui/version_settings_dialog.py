from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from ..versions import LOADER_COLORS
from .dialogs import StyledDialog
from .widgets import badge, colors


class VersionSettingsDialog(StyledDialog):
    """单个游戏版本的启动设置：原版启动，或绑定一个已安装的加载器。"""

    def __init__(self, main, version_id: str) -> None:
        summary = next((v for v in main.versions if v.id == version_id), None)
        super().__init__(
            main,
            f"版本设置 · {version_id or '未选择版本'}",
            "settings",
            width=580,
        )
        self.main = main
        self.summary = summary
        self.version_id = version_id
        self._profiles = []

        intro = QLabel(
            "加载器和游戏版本分开管理。这里只改变“用哪个加载器启动”，"
            "当前版本自己的存档、Mod、材质和光影都不会被移动。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        if summary is None:
            missing = QLabel("找不到这个版本，可能已经被删除。")
            missing.setObjectName("Muted")
            self.body.addWidget(missing)
            self.add_button("关闭", "Secondary", self.reject)
            return

        info_box = QFrame()
        info_box.setObjectName("SoftCard")
        info_box.setAttribute(Qt.WA_StyledBackground, True)
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(14, 11, 14, 11)
        info_layout.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(7)
        title = QLabel(summary.id)
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        head.addWidget(title)
        head.addWidget(badge(summary.type_name, "green" if summary.type == "release" else "orange"))
        if summary.loader:
            head.addWidget(badge(summary.loader, "purple"))
        head.addStretch(1)
        info_layout.addLayout(head)
        meta = QLabel(
            f"基础版本 {summary.base_id or summary.id} · "
            f"Java {summary.java_major or '自动'} · "
            f"目录 {main.config.version_game_path(summary.id)}"
        )
        meta.setObjectName("Tiny")
        meta.setWordWrap(True)
        info_layout.addWidget(meta)
        self.body.addWidget(info_box)

        self.add_button("打开版本目录", "Secondary", self._open_folder)
        self.install_btn = self.add_button("安装加载器", "Secondary", self._open_installer)

        self._profiles = main.compatible_loader_profiles(summary.id)
        built_in = bool(summary.loader or summary.is_loader_profile)

        if built_in:
            note = QLabel(
                "这个版本自身已经带有加载器（例如整合包实例），会直接按它内置的"
                "加载器启动，不需要再绑定其他加载器。"
            )
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self.body.addWidget(note)
            self.install_btn.setEnabled(not summary.is_loader_profile)
            self.add_button("关闭", "Primary", self.accept)
            return

        mode_box = QFrame()
        mode_box.setObjectName("SoftCard")
        mode_box.setAttribute(Qt.WA_StyledBackground, True)
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.setContentsMargins(14, 12, 14, 12)
        mode_layout.setSpacing(8)

        label = QLabel("启动方式")
        label.setStyleSheet("font-weight: 700; font-size: 12px;")
        mode_layout.addWidget(label)

        self.mode_combo = QComboBox()
        self.mode_combo.setMinimumWidth(430)
        self.mode_combo.addItem("原版启动 · 不加载任何 Mod 加载器", "")
        for profile in self._profiles:
            text = f"{profile.loader or '加载器'} · {profile.id}"
            if profile.loader_version:
                text += f"（{profile.loader_version}）"
            self.mode_combo.addItem(text, profile.id)
        current = main.config.loader_for(summary.id)
        index = self.mode_combo.findData(current)
        self.mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self.mode_combo.currentIndexChanged.connect(self._update_hint)
        mode_layout.addWidget(self.mode_combo)

        self.hint = QLabel("")
        self.hint.setObjectName("Tiny")
        self.hint.setWordWrap(True)
        mode_layout.addWidget(self.hint)
        self.body.addWidget(mode_box)

        if not self._profiles:
            empty = QLabel(
                f"基础版本 {summary.base_id or summary.id} 还没有可用的加载器配置。"
                "点击“安装加载器”去添加 Fabric、Forge 或 NeoForge。"
            )
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            self.body.addWidget(empty)

        note = QLabel(
            "同一个加载器可以分配给多个游戏版本。绑定后，启动器会用加载器的依赖库"
            "启动游戏，但运行目录仍然是当前游戏版本。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        self.body.addWidget(note)

        self.add_button("取消", "Secondary", self.reject)
        self.save_btn = self.add_button("保存设置", "Primary", self._save)
        self.save_btn.setEnabled(bool(self._profiles) or bool(current))
        self._update_hint()

    # ------------------------------------------------------------------
    def _update_hint(self) -> None:
        if not hasattr(self, "hint"):
            return
        profile_id = self.mode_combo.currentData()
        profile = next((p for p in self._profiles if p.id == profile_id), None)
        if not profile:
            self.hint.setText(
                "直接使用 Mojang 原版启动。已经放进版本目录的 Mod 不会生效。"
            )
            self.hint.setStyleSheet("")
            return
        accent = LOADER_COLORS.get(profile.loader, colors()["accent"])
        details = [f"将使用 {profile.loader or '加载器'} 配置 {profile.id} 启动"]
        if profile.java_major:
            details.append(f"需要 Java {profile.java_major}+")
        self.hint.setText(" · ".join(details))
        self.hint.setStyleSheet(f"color: {accent};")

    def _open_folder(self) -> None:
        self.main.open_path(
            Path(self.main.config.version_game_path(self.version_id))
        )

    def _open_installer(self) -> None:
        self.accept()
        QTimer.singleShot(0, self.main.open_loader_dialog)

    def _save(self) -> None:
        if self.summary is None:
            self.reject()
            return
        profile_id = str(self.mode_combo.currentData() or "")
        self.main.bind_loader(self.version_id, profile_id)
        self.main.select_version(self.version_id)
        if profile_id:
            profile = next((p for p in self._profiles if p.id == profile_id), None)
            name = profile.loader if profile and profile.loader else "加载器"
            self.main.toast(f"{self.version_id} 将使用 {name} 启动", "success")
        else:
            self.main.toast(f"{self.version_id} 已切换为原版启动", "success")
        self.accept()
