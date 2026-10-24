from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...accounts import offline_uuid
from ...skins import (
    SKIN_DIR,
    CurrentSkin,
    SkinError,
    SkinFile,
    default_skin,
    load_skin_file,
    local_skin_path,
    save_local_skin,
    variant_text,
)
from ...workers import SkinFetchWorker, SkinResetWorker, SkinUploadWorker
from .. import icons
from ..dialogs import ConfirmDialog
from ..skin_preview import SkinPreviewWidget
from ..widgets import (
    Card,
    ChibiCompanion,
    SegmentedControl,
    badge,
    colors,
    hline,
    icon_button,
    repolish,
)

SKIN_FORUM_URL = "https://klpbbs.com/forum.php?mod=forumdisplay&fid=54"
FORUM_URL = "https://klpbbs.com/"


def _rounded_face(image: QImage, size: int = 48) -> QPixmap:
    if image.isNull():
        image = default_skin()
    face = image.copy(8, 8, 8, 8)
    scaled = face.scaled(
        size, size, Qt.IgnoreAspectRatio, Qt.FastTransformation
    )
    canvas = QImage(size, size, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0.5, 0.5, size - 1, size - 1, 11, 11)
    painter.setClipPath(path)
    painter.drawImage(0, 0, scaled)
    painter.end()
    return QPixmap.fromImage(canvas)


class SkinPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._skin_file: SkinFile | None = None
        self._current_skin: CurrentSkin | None = None
        self._fetch_worker: SkinFetchWorker | None = None
        self._upload_worker: SkinUploadWorker | None = None
        self._reset_worker: SkinResetWorker | None = None
        self._busy = False
        self._auto_fetch_done: set[str] = set()
        self.setAcceptDrops(True)
        self._build()
        self._load_initial_skin()
        self.main.account_changed.connect(self.refresh_account)
        self.refresh_account()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        scroll.setWidget(container)
        outer.addWidget(scroll)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("猫猫试衣间")
        title.setObjectName("PageTitle")
        sub = QLabel(
            "导入 64×64 或 64×32 的 PNG 皮肤，实时查看 3D 效果，"
            "登录正版账户后可以直接上传并使用。"
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(sub)
        header.addLayout(titles, 1)
        header.addWidget(ChibiCompanion(60, "skin"), 0, Qt.AlignTop)

        self.account_badge = badge("离线预览", "gray")
        header.addWidget(self.account_badge, 0, Qt.AlignTop)
        forum_btn = QPushButton("苦力怕论坛 · 皮肤版块")
        forum_btn.setObjectName("Ghost")
        forum_btn.setCursor(Qt.PointingHandCursor)
        forum_btn.setIcon(icons.icon("globe", colors()["text2"], 15))
        forum_btn.clicked.connect(lambda: self._open_url(SKIN_FORUM_URL))
        header.addWidget(forum_btn, 0, Qt.AlignTop)
        layout.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(14)
        content.addWidget(self._build_preview_card())

        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_account_card())
        right.addWidget(self._build_upload_card())
        right.addWidget(self._build_community_card())
        right.addStretch(1)
        content.addLayout(right, 1)
        layout.addLayout(content, 1)

    def _build_preview_card(self) -> QWidget:
        card = Card()
        card.setFixedWidth(438)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("3D 预览")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addStretch(1)
        self.size_badge = badge("64×64", "blue")
        self.model_badge = badge("Classic", "gray")
        header.addWidget(self.size_badge)
        header.addWidget(self.model_badge)
        layout.addLayout(header)

        self.preview = SkinPreviewWidget()
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setMinimumHeight(440)
        layout.addWidget(self.preview, 1)

        hint = QLabel("按住左键拖动旋转 · 滚轮缩放 · 双击复位")
        hint.setObjectName("Tiny")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        flip_btn = QPushButton("翻面 180°")
        flip_btn.setObjectName("Secondary")
        flip_btn.setCursor(Qt.PointingHandCursor)
        flip_btn.setIcon(icons.icon("flip", colors()["text2"], 15))
        flip_btn.clicked.connect(self.preview.flip)
        reset_btn = QPushButton("重置视角")
        reset_btn.setObjectName("Ghost")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setIcon(icons.icon("rotate", colors()["text2"], 15))
        reset_btn.clicked.connect(self.preview.reset_view)
        controls.addWidget(flip_btn)
        controls.addWidget(reset_btn)
        controls.addStretch(1)
        layout.addLayout(controls)
        return card

    def _build_account_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("当前账户与皮肤")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addStretch(1)
        self.mode_badge = badge("离线模式", "gray")
        header.addWidget(self.mode_badge)
        layout.addLayout(header)

        row = QHBoxLayout()
        row.setSpacing(11)
        self.face_label = QLabel()
        self.face_label.setFixedSize(48, 48)
        self.face_label.setPixmap(_rounded_face(default_skin(), 48))
        row.addWidget(self.face_label)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.account_name = QLabel("离线预览")
        self.account_name.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.account_uuid = QLabel("")
        self.account_uuid.setObjectName("Tiny")
        self.account_uuid.setWordWrap(True)
        texts.addWidget(self.account_name)
        texts.addWidget(self.account_uuid)
        row.addLayout(texts, 1)
        layout.addLayout(row)
        layout.addWidget(hline())

        self.current_label = QLabel("尚未读取正版皮肤。")
        self.current_label.setObjectName("Tiny")
        self.current_label.setWordWrap(True)
        layout.addWidget(self.current_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.refresh_skin_btn = QPushButton("读取正版皮肤")
        self.refresh_skin_btn.setObjectName("Secondary")
        self.refresh_skin_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_skin_btn.setIcon(icons.icon("refresh", colors()["text2"], 15))
        self.refresh_skin_btn.clicked.connect(self._fetch_current_skin)
        self.reset_skin_btn = QPushButton("恢复默认皮肤")
        self.reset_skin_btn.setObjectName("Danger")
        self.reset_skin_btn.setCursor(Qt.PointingHandCursor)
        self.reset_skin_btn.clicked.connect(self._reset_skin)
        buttons.addWidget(self.refresh_skin_btn)
        buttons.addWidget(self.reset_skin_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Tiny")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        return card

    def _build_upload_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 13)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("选择与上传")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addStretch(1)
        info = QLabel("PNG · 64×64 / 64×32")
        info.setObjectName("Tiny")
        header.addWidget(info)
        layout.addLayout(header)

        file_row = QHBoxLayout()
        file_row.setSpacing(7)
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("尚未选择本地皮肤，也可以把 PNG 拖进窗口")
        self.file_edit.setMinimumWidth(260)
        self.select_btn = QPushButton("选择 PNG")
        self.select_btn.setObjectName("Soft")
        self.select_btn.setCursor(Qt.PointingHandCursor)
        self.select_btn.setIcon(icons.icon("image", colors()["accent"], 15))
        self.select_btn.clicked.connect(self._choose_skin)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(self.select_btn)
        layout.addLayout(file_row)

        model_row = QHBoxLayout()
        model_row.setSpacing(10)
        model_label = QLabel("皮肤模型")
        model_label.setStyleSheet("font-weight: 600;")
        self.variant_tabs = SegmentedControl()
        self.variant_tabs.add_item("Classic（标准手臂）", "classic")
        self.variant_tabs.add_item("Slim（Alex 细手臂）", "slim")
        self.variant_tabs.set_value(self.main.config.skin_variant)
        self.variant_tabs.changed.connect(self._on_variant_changed)
        model_row.addWidget(model_label)
        model_row.addWidget(self.variant_tabs)
        model_row.addStretch(1)
        layout.addLayout(model_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.upload_btn = QPushButton("上传并使用")
        self.upload_btn.setObjectName("Primary")
        self.upload_btn.setCursor(Qt.PointingHandCursor)
        self.upload_btn.setIcon(icons.icon("shirt", "#FFFFFF", 15))
        self.upload_btn.clicked.connect(self._upload_skin)
        folder_btn = QPushButton("打开皮肤文件夹")
        folder_btn.setObjectName("Ghost")
        folder_btn.setCursor(Qt.PointingHandCursor)
        folder_btn.setIcon(icons.icon("folder", colors()["text2"], 15))
        folder_btn.clicked.connect(self._open_skin_folder)
        actions.addWidget(self.upload_btn)
        actions.addWidget(folder_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        note = QLabel(
            "选择文件后会复制一份到启动器的 skins 文件夹，原始图片不会被修改。"
            "上传会立即替换正版账户当前皮肤，并同步到 Minecraft。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        layout.addWidget(note)
        return card

    def _build_community_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 13, 18, 13)
        layout.setSpacing(7)

        title = QLabel("资源与社区")
        title.setObjectName("H2")
        layout.addWidget(title)
        desc = QLabel(
            "苦力怕论坛（KLBBS）有大量玩家分享的皮肤、Mod 与整合包。"
            "可以前往皮肤版块挑选作品，再回到这里预览和上传。"
        )
        desc.setObjectName("Tiny")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        skin_forum = QPushButton("皮肤分享版块")
        skin_forum.setObjectName("Soft")
        skin_forum.setCursor(Qt.PointingHandCursor)
        skin_forum.setIcon(icons.icon("shirt", colors()["accent"], 15))
        skin_forum.clicked.connect(lambda: self._open_url(SKIN_FORUM_URL))
        home = QPushButton("苦力怕论坛首页")
        home.setObjectName("Ghost")
        home.setCursor(Qt.PointingHandCursor)
        home.setIcon(icons.icon("external", colors()["text2"], 15))
        home.clicked.connect(lambda: self._open_url(FORUM_URL))
        buttons.addWidget(skin_forum)
        buttons.addWidget(home)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return card

    # ------------------------------------------------------------------
    def _load_initial_skin(self) -> None:
        candidates: list[Path] = []
        configured = self.main.config.skin_path.strip()
        if configured:
            candidates.append(Path(configured))
        local = local_skin_path()
        if local not in candidates:
            candidates.append(local)

        for path in candidates:
            if not path.is_file():
                continue
            try:
                skin = load_skin_file(path)
            except SkinError as exc:
                self._set_status(str(exc), "error")
                continue
            self._skin_file = skin
            if skin.legacy and self.variant_tabs.current() == "slim":
                self.variant_tabs.set_value("classic")
                self.main.config.skin_variant = "classic"
                self.main.config.save()
            self._show_skin(skin.image, skin.legacy)
            self.file_edit.setText(str(path))
            self.file_edit.setToolTip(str(path))
            return

        self._show_skin(default_skin(), False)
        self.current_label.setText("使用默认皮肤预览。选择本地 PNG 即可开始。")

    def _show_skin(self, image: QImage, legacy: bool) -> None:
        self.preview.set_skin(image, legacy)
        self.preview.set_variant(self.main.config.skin_variant)
        self.size_badge.setText("64×32 · 旧版" if legacy else "64×64")
        self.size_badge.setProperty("badge", "orange" if legacy else "blue")
        self._update_model_badge()
        self.face_label.setPixmap(_rounded_face(image, 48))
        repolish(self.size_badge)

    def _update_model_badge(self) -> None:
        variant = self.variant_tabs.current()
        self.model_badge.setText("Slim" if variant == "slim" else "Classic")
        self.model_badge.setProperty("badge", "purple" if variant == "slim" else "gray")
        repolish(self.model_badge)

    def _apply_local_file(self, path: str | Path, *, original: str = "") -> None:
        try:
            skin = load_skin_file(path)
            saved = save_local_skin(skin)
        except SkinError as exc:
            self._set_status(str(exc), "error")
            self.main.toast(str(exc), "error")
            return
        self._skin_file = skin
        legacy_note = ""
        if skin.legacy:
            if self.variant_tabs.current() != "classic":
                self.variant_tabs.set_value("classic")
            legacy_note = " 旧版 64×32 皮肤只支持 Classic 模型。"
        self.main.config.skin_variant = "classic" if skin.legacy else self.variant_tabs.current()
        self.main.config.skin_path = str(saved)
        self.main.config.save()
        self._show_skin(skin.image, skin.legacy)
        self.file_edit.setText(original or str(Path(path)))
        self.file_edit.setToolTip(
            f"原始文件：{original or path}\n预览副本：{saved}"
        )
        self.current_label.setText(
            "本地皮肤已就绪，可以预览 3D 效果。"
            + ("点击“上传并使用”同步到正版账户。" if self.main.current_account().is_msa else "离线模式下只能预览。")
            + legacy_note
        )
        self._set_status(f"已加载皮肤：{Path(original or path).name}{legacy_note}", "success")
        self._update_buttons()
        self.main.account_changed.emit()

    def _choose_skin(self) -> None:
        start = str(Path.home() / "Pictures")
        configured = self.main.config.skin_path.strip()
        if configured:
            parent = Path(configured).parent
            if parent.is_dir():
                start = str(parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Minecraft 皮肤",
            start,
            "Minecraft 皮肤 (*.png)",
        )
        if path:
            self._apply_local_file(path, original=path)

    def _on_variant_changed(self, value: str) -> None:
        if value == "slim" and self.preview.is_legacy():
            self.variant_tabs.set_value("classic")
            value = "classic"
            self._set_status(
                "64×32 旧版皮肤没有独立的细手臂纹理，已保持 Classic 模型。",
                "info",
            )
        self.main.config.skin_variant = "slim" if value == "slim" else "classic"
        self.main.config.save()
        self.preview.set_variant(value)
        self._update_model_badge()

    # ------------------------------------------------------------------
    def _set_status(self, text: str, kind: str = "info") -> None:
        color = {
            "error": colors()["red"],
            "success": colors()["green"],
            "busy": colors()["accent"],
            "info": colors()["muted"],
        }.get(kind, colors()["muted"])
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _update_buttons(self) -> None:
        online = self.main.current_account().is_msa
        available = not self._busy
        has_file = self._skin_file is not None
        self.refresh_skin_btn.setEnabled(online and available)
        self.reset_skin_btn.setEnabled(online and available)
        self.upload_btn.setEnabled(online and available and has_file)
        self.select_btn.setEnabled(available)
        if not online and available:
            self.upload_btn.setToolTip("登录 Microsoft 正版账户后才能上传皮肤")
        else:
            self.upload_btn.setToolTip(
                "" if has_file else "请先选择一张 64×64 或 64×32 的 PNG 皮肤"
            )

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        if text:
            self._set_status(text, "busy")
        self._update_buttons()

    def _save_account(self, account) -> None:
        self.main.config.account = account.to_config()
        self.main.config.save()
        self.main.account_changed.emit()

    def _client_id(self, silent: bool = False) -> str | None:
        client_id = str(self.main.config.microsoft_client_id or "").strip()
        if not client_id:
            if not silent:
                self.main.toast("请先在设置中填写 Azure 客户端 ID", "error")
                self.main.switch_page("settings")
            return None
        return client_id

    # ------------------------------------------------------------------
    def _fetch_current_skin(self, silent: bool = False) -> None:
        account = self.main.current_account()
        if not account.is_msa:
            if not silent:
                self.main.toast("请先登录 Microsoft 正版账户", "info")
            return
        if self._busy:
            return
        client_id = self._client_id(silent=silent)
        if client_id is None:
            return
        self._auto_fetch_done.add(account.uuid or account.name)
        self._set_busy(True, "正在读取正版皮肤…")
        worker = SkinFetchWorker(account, client_id, self)
        self._fetch_worker = worker
        worker.status.connect(lambda text: self._set_status(text, "busy"))
        worker.finished.connect(self._on_skin_loaded)
        worker.start()

    def _on_skin_loaded(self, skin, account, error: str) -> None:
        self._fetch_worker = None
        self._busy = False
        self._save_account(account)
        if error:
            self._set_status(f"读取皮肤失败：{error}", "error")
            self._update_buttons()
            return
        if skin is None:
            self._current_skin = None
            self.current_label.setText("该正版账户目前使用默认皮肤（Steve/Alex）。")
            self._set_status("账户当前没有自定义皮肤。", "info")
            if self._skin_file is None:
                self._show_skin(default_skin(), False)
            self._update_buttons()
            return
        self._current_skin = skin
        if skin.image.height() == 32:
            skin.variant = "classic"
        self.main.config.skin_variant = (
            "slim" if skin.variant == "slim" else "classic"
        )
        self.main.config.save()
        self.variant_tabs.set_value(self.main.config.skin_variant)
        if self._skin_file is None:
            self._show_skin(skin.image, skin.image.height() == 32)
        else:
            self.face_label.setPixmap(_rounded_face(skin.image, 48))
            self._update_model_badge()
        self.current_label.setText(
            f"正版当前皮肤 · {variant_text(skin.variant)}"
            + (f" · {skin.alias}" if skin.alias else "")
        )
        if not self.main.config.skin_path.strip():
            try:
                local = local_skin_path()
                if skin.image.save(str(local), "PNG"):
                    self.main.config.skin_path = str(local)
                    self.main.config.save()
            except Exception:
                pass
        self._set_status("已读取正版账户当前皮肤。", "success")
        self._update_buttons()
        self.main.account_changed.emit()

    def _upload_skin(self) -> None:
        account = self.main.current_account()
        if not account.is_msa:
            self.main.toast("请先登录 Microsoft 正版账户", "error")
            return
        if self._busy:
            return
        if self._skin_file is None:
            self.main.toast("请先选择一张本地 PNG 皮肤", "info")
            return
        client_id = self._client_id()
        if client_id is None:
            return
        variant = self.variant_tabs.current()
        dialog = ConfirmDialog(
            self.main,
            "上传并更换皮肤",
            f"将把当前预览的皮肤上传到 {account.name or '正版账户'}，"
            "并立即替换该账户正在使用的 Minecraft 皮肤。",
            primary_text="上传并使用",
            secondary_text="取消",
            icon_name="shirt",
            details="上传后通常几秒内生效；其他玩家和皮肤站点的缓存可能需要更长时间同步。",
        )
        if not dialog.exec():
            return

        self._set_busy(True, "正在准备上传皮肤…")
        worker = SkinUploadWorker(
            account, client_id, self._skin_file.data, variant, self
        )
        self._upload_worker = worker
        worker.status.connect(lambda text: self._set_status(text, "busy"))
        worker.finished.connect(self._on_skin_uploaded)
        worker.start()

    def _on_skin_uploaded(self, success: bool, message: str, account) -> None:
        self._upload_worker = None
        self._busy = False
        self._save_account(account)
        if success:
            if self._skin_file is not None:
                self._current_skin = CurrentSkin(
                    image=self._skin_file.image,
                    variant=self.variant_tabs.current(),
                )
                self.current_label.setText(
                    f"正版当前皮肤 · 刚刚上传 · {variant_text(self.variant_tabs.current())}"
                )
            self._set_status(message, "success")
            self.main.toast(message, "success")
        else:
            self._set_status(f"上传失败：{message}", "error")
            self.main.toast(f"皮肤上传失败：{message}", "error")
        self._update_buttons()

    def _reset_skin(self) -> None:
        account = self.main.current_account()
        if not account.is_msa:
            self.main.toast("请先登录 Microsoft 正版账户", "error")
            return
        if self._busy:
            return
        client_id = self._client_id()
        if client_id is None:
            return
        dialog = ConfirmDialog(
            self.main,
            "恢复默认皮肤",
            f"将移除 {account.name or '该账户'} 当前的自定义皮肤，恢复为默认皮肤。",
            primary_text="恢复默认",
            secondary_text="取消",
            icon_name="warning",
            details="这个操作会修改正版账户皮肤，但不会删除启动器 skins 文件夹中的本地图片。",
        )
        if not dialog.exec():
            return

        self._set_busy(True, "正在恢复默认皮肤…")
        worker = SkinResetWorker(account, client_id, self)
        self._reset_worker = worker
        worker.status.connect(lambda text: self._set_status(text, "busy"))
        worker.finished.connect(self._on_skin_reset)
        worker.start()

    def _on_skin_reset(self, success: bool, message: str, account) -> None:
        self._reset_worker = None
        self._busy = False
        self._save_account(account)
        if success:
            self._current_skin = None
            self._skin_file = None
            self.main.config.skin_path = ""
            self.main.config.save()
            self.file_edit.clear()
            self.file_edit.setToolTip("")
            self.current_label.setText("正版账户已恢复默认皮肤。")
            self._show_skin(default_skin(), False)
            self._set_status(message, "success")
            self.main.toast(message, "success")
        else:
            self._set_status(f"恢复失败：{message}", "error")
            self.main.toast(f"恢复默认皮肤失败：{message}", "error")
        self._update_buttons()
        self.main.account_changed.emit()

    # ------------------------------------------------------------------
    def refresh_account(self) -> None:
        account = self.main.current_account()
        self.main.config.skin_variant = self.variant_tabs.current()
        if account.is_msa:
            self.account_badge.setText("微软正版")
            self.account_badge.setProperty("badge", "green")
            self.mode_badge.setText("正版账户")
            self.mode_badge.setProperty("badge", "green")
            self.account_name.setText(account.name or "正版账户")
            uid = account.uuid or ""
            self.account_uuid.setText(
                f"UUID {uid[:8]}…{uid[-4:]}" if uid else "已登录 Microsoft 账户"
            )
        else:
            self.account_badge.setText("离线预览")
            self.account_badge.setProperty("badge", "gray")
            self.mode_badge.setText("离线模式")
            self.mode_badge.setProperty("badge", "gray")
            name = self.main.config.player_name
            self.account_name.setText(f"离线预览 · {name}")
            uid = offline_uuid(name)
            self.account_uuid.setText(
                f"UUID {uid[:8]}…{uid[-4:]} · 登录正版后可上传皮肤"
            )
        repolish(self.account_badge)
        repolish(self.mode_badge)
        if not self._busy:
            online = account.is_msa
            if self._current_skin is not None and online:
                self._set_status("正版账户已连接，可以上传或恢复皮肤。", "info")
            elif not online:
                self._set_status("离线模式仅支持本地预览，不会修改任何账户。", "info")
            elif self._skin_file is not None:
                self._set_status("本地皮肤已就绪，可以上传。", "info")
            else:
                self._set_status("可以点击“读取正版皮肤”查看账户当前效果。", "info")
        self._update_buttons()
        self.preview.update()

    def on_show(self) -> None:
        self.refresh_account()
        account = self.main.current_account()
        if not account.is_msa:
            return
        key = account.uuid or account.name
        if self._skin_file is None and key not in self._auto_fetch_done:
            self._auto_fetch_done.add(key)
            self._fetch_current_skin(silent=True)

    def refresh_theme(self) -> None:
        self.preview.update()
        if self._skin_file is not None:
            self.face_label.setPixmap(_rounded_face(self._skin_file.image, 48))
        elif self._current_skin is not None:
            self.face_label.setPixmap(_rounded_face(self._current_skin.image, 48))
        else:
            self.face_label.setPixmap(_rounded_face(default_skin(), 48))

    # ------------------------------------------------------------------
    def _open_skin_folder(self) -> None:
        try:
            SKIN_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.main.toast(f"无法创建皮肤文件夹：{exc}", "error")
            return
        self.main.open_path(SKIN_DIR)

    @staticmethod
    def _open_url(url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if any(url.toLocalFile().lower().endswith(".png") for url in urls):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".png"):
                self._apply_local_file(path, original=path)
                event.acceptProposedAction()
                return
        super().dropEvent(event)
