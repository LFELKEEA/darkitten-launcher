from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..room_sync import (
    RoomManifest,
    RoomSyncError,
    decode_manifest,
    plan_room_sync,
)
from ..workers import RoomModSyncWorker
from .dialogs import StyledDialog
from .widgets import badge, colors


def _card() -> tuple[QFrame, QVBoxLayout]:
    box = QFrame()
    box.setObjectName("SoftCard")
    box.setAttribute(Qt.WA_StyledBackground, True)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(7)
    return box, layout


class ModSyncDialog(StyledDialog):
    """按房主邀请卡里的 Mod 清单补齐当前实例缺少的文件。"""

    def __init__(
        self,
        main,
        manifest: RoomManifest | None = None,
        version_id: str = "",
    ) -> None:
        super().__init__(main, "同步房主 Mod", "users", width=680)
        self.main = main
        self._manifest = manifest
        self._plan = None
        self._worker: RoomModSyncWorker | None = None
        self._closing = False

        intro = QLabel(
            "房主的邀请卡会携带 Mod 清单。启动器只下载缺失且能确认来源的文件，"
            "不会删除你已有的 Mod，也不会改动世界和配置。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        source_box, source_layout = _card()
        source_head = QHBoxLayout()
        source_title = QLabel("房主邀请卡 / Mod 清单")
        source_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        source_head.addWidget(source_title)
        source_head.addStretch(1)
        self.read_btn = QPushButton("读取剪贴板")
        self.read_btn.setObjectName("Soft")
        self.read_btn.setCursor(Qt.PointingHandCursor)
        self.read_btn.clicked.connect(self._read_source)
        source_head.addWidget(self.read_btn)
        source_layout.addLayout(source_head)

        self.source_edit = QPlainTextEdit()
        self.source_edit.setObjectName("SoftLog")
        self.source_edit.setFixedHeight(58)
        self.source_edit.setPlaceholderText(
            "粘贴 DKLF1: 好友邀请卡，或 DKLM1: Mod 清单"
        )
        source_layout.addWidget(self.source_edit)

        self.source_hint = QLabel("")
        self.source_hint.setObjectName("Tiny")
        self.source_hint.setWordWrap(True)
        source_layout.addWidget(self.source_hint)
        self.body.addWidget(source_box)

        version_row = QHBoxLayout()
        version_row.setSpacing(9)
        version_caption = QLabel("同步到版本")
        version_caption.setStyleSheet("font-weight: 600;")
        version_caption.setFixedWidth(70)
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(360)
        self.version_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_plan()
        )
        version_row.addWidget(version_caption)
        version_row.addWidget(self.version_combo, 1)
        self.body.addLayout(version_row)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("Tiny")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.addWidget(self.summary_label)

        self.count_row = QHBoxLayout()
        self.count_row.setSpacing(6)
        self.present_badge = badge("已有 0", "gray")
        self.missing_badge = badge("缺失 0", "gray")
        self.unknown_badge = badge("无法识别 0", "gray")
        self.count_row.addWidget(self.present_badge)
        self.count_row.addWidget(self.missing_badge)
        self.count_row.addWidget(self.unknown_badge)
        self.count_row.addStretch(1)
        self.body.addLayout(self.count_row)

        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_scroll.setMinimumHeight(150)
        self.list_scroll.setMaximumHeight(250)
        self.list_holder = QWidget()
        self.list_layout = QVBoxLayout(self.list_holder)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(4)
        self.list_scroll.setWidget(self.list_holder)
        self.body.addWidget(self.list_scroll, 1)

        self.warn_label = QLabel("")
        self.warn_label.setObjectName("Tiny")
        self.warn_label.setWordWrap(True)
        self.warn_label.setStyleSheet(f"color: {colors()['orange']};")
        self.warn_label.setVisible(False)
        self.body.addWidget(self.warn_label)

        self.progress_box = QWidget()
        progress_layout = QVBoxLayout(self.progress_box)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(5)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress_text = QLabel("")
        self.progress_text.setObjectName("Tiny")
        self.progress_text.setWordWrap(True)
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.progress_text)
        self.progress_box.setVisible(False)
        self.body.addWidget(self.progress_box)

        self.close_btn = self.add_button("关闭", "Secondary", self.reject)
        self.sync_btn = self.add_button("同步缺失 Mod", "Primary", self._start_sync)
        self.sync_btn.setMinimumWidth(118)

        self._populate_versions(version_id)
        if manifest is not None:
            self.source_hint.setText(
                f"已读取房主 {manifest.name or '好友'} 的清单，共 {manifest.mod_count} 个 Mod。"
            )
        else:
            self.source_hint.setText(
                "还没有清单。可以让房主重新复制邀请卡，或把清单粘贴到上方。"
            )
        self._refresh_plan()

    # ------------------------------------------------------------------
    def _populate_versions(self, preferred: str) -> None:
        versions = list(self.main.game_version_summaries())
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        for summary in versions:
            kind = summary.loader or summary.type_name or ""
            label = f"{summary.id} · {kind}" if kind else summary.id
            if summary.error:
                label += "（文件异常）"
            self.version_combo.addItem(label, summary.id)
        if not versions:
            self.version_combo.addItem("未安装游戏版本", "")
        target = str(preferred or "")
        if not target and self._manifest is not None:
            target = self._manifest.version_id
        if not target:
            target = str(self.main.selected_version or "")
        index = self.version_combo.findData(target)
        if index < 0:
            index = 0
        self.version_combo.setCurrentIndex(index)
        self.version_combo.blockSignals(False)

    def _read_source(self) -> None:
        if self._worker is not None:
            return
        text = self.source_edit.toPlainText().strip()
        if not text:
            text = QApplication.clipboard().text().strip()
            if not text:
                self.source_hint.setText("剪贴板里没有邀请卡或 Mod 清单。")
                return
            self.source_edit.setPlainText(text)
        try:
            manifest = decode_manifest(text)
        except RoomSyncError as exc:
            self.source_hint.setText(str(exc))
            self.main.toast(str(exc), "error")
            return
        self._manifest = manifest
        self.source_hint.setText(
            f"已读取 {manifest.name or '房主'} 的清单，共 {manifest.mod_count} 个 Mod。"
        )
        if manifest.version_id:
            index = self.version_combo.findData(manifest.version_id)
            if index >= 0:
                self.version_combo.blockSignals(True)
                self.version_combo.setCurrentIndex(index)
                self.version_combo.blockSignals(False)
        self._refresh_plan()

    def _refresh_plan(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._plan = None
        manifest = self._manifest
        version_id = str(self.version_combo.currentData() or "")
        if manifest is None or not version_id:
            self.summary_label.setText(
                "先读取房主邀请卡，并选择一个已经安装的游戏版本。"
            )
            self._set_counts(0, 0, 0)
            self._empty_row("等待读取房主清单")
            self.warn_label.setVisible(False)
            self.sync_btn.setEnabled(False)
            return
        try:
            plan = plan_room_sync(
                manifest, version_id, self.main.config, self.main.versions
            )
        except Exception as exc:
            self.summary_label.setText(str(exc))
            self._set_counts(0, 0, 0)
            self._empty_row("无法读取这个版本")
            self.sync_btn.setEnabled(False)
            return
        self._plan = plan
        loader = manifest.loader or "未知加载器"
        mc_version = manifest.mc_version or "未知版本"
        self.summary_label.setText(
            f"房主：{manifest.name or '好友'} · Minecraft {mc_version} · {loader} · "
            f"清单 {manifest.mod_count} 个 Mod\n"
            f"目标实例：{version_id} · 实际加载器 {plan.context.loader_label} · "
            f"实际版本 {plan.context.mc_version}"
        )
        self._set_counts(plan.present, len(plan.missing), len(plan.unknown))
        if not manifest.mods:
            self._empty_row("房主清单里没有 Mod")
        elif not plan.missing and not plan.unknown:
            self._empty_row("房主清单里的 Mod 都已经安装")
        for mod in plan.missing:
            self._add_mod_row(mod, "待下载", "blue")
        for mod in plan.unknown:
            self._add_mod_row(mod, "缺少来源", "orange")
        self.warn_label.setText("\n".join(f"· {item}" for item in plan.warnings))
        self.warn_label.setVisible(bool(plan.warnings))
        self.sync_btn.setEnabled(bool(plan.missing) and self._worker is None)

    def _set_counts(self, present: int, missing: int, unknown: int) -> None:
        for label, text, kind, count in (
            (self.present_badge, "已有", "green", present),
            (self.missing_badge, "缺失", "orange", missing),
            (self.unknown_badge, "无法识别", "gray", unknown),
        ):
            label.setText(f"{text} {count}")
            label.setProperty("badge", kind if count else "gray")
            label.style().unpolish(label)
            label.style().polish(label)
            label.update()

    def _empty_row(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("Muted")
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumHeight(100)
        self.list_layout.addWidget(label)
        self.list_layout.addStretch(1)

    def _add_mod_row(self, mod, state: str, color: str) -> None:
        row = QFrame()
        row.setObjectName("SelectRow")
        row.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        title = QLabel(mod.display_name)
        title.setStyleSheet("font-weight: 700; font-size: 12px;")
        texts.addWidget(title)
        meta = QLabel(f"{mod.filename} · {mod.source_label}")
        meta.setObjectName("Tiny")
        meta.setWordWrap(True)
        texts.addWidget(meta)
        layout.addLayout(texts, 1)
        layout.addWidget(badge(state, color), 0, Qt.AlignTop)
        self.list_layout.addWidget(row)

    # ------------------------------------------------------------------
    def _start_sync(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_text.setText("正在取消同步…")
            return
        if self._plan is None or not self._plan.missing:
            return
        worker = RoomModSyncWorker(
            self._plan,
            threads=self.main.config.download_threads,
            parent=self,
        )
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        self._set_busy(True)
        self.progress_box.setVisible(True)
        self.progress.setValue(0)
        self.progress_text.setText("准备同步…")
        worker.start()

    def _set_busy(self, busy: bool) -> None:
        self.read_btn.setEnabled(not busy)
        self.source_edit.setEnabled(not busy)
        self.version_combo.setEnabled(not busy)
        self.close_btn.setEnabled(not busy)
        self.sync_btn.setEnabled(
            not busy and bool(self._plan and self._plan.missing)
        )
        self.sync_btn.setText("取消同步" if busy else "同步缺失 Mod")

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress_box.setVisible(True)
        self.progress.setValue(max(0, min(100, percent)))
        self.progress_text.setText(message)

    def _on_finished(self, success: bool, message: str, result) -> None:
        self._worker = None
        if self._closing:
            self._closing = False
            super().reject()
            return
        if not success or result is None:
            self._set_busy(False)
            self.progress_text.setText(f"同步失败：{message}")
            self.main.toast(message or "同步失败", "error")
            return
        downloaded = getattr(result, "downloaded", []) or []
        errors = getattr(result, "errors", []) or []
        parts = [f"下载 {len(downloaded)} 个 Mod"] if downloaded else []
        if getattr(result, "cancelled", False):
            parts.append("已取消")
        text = "同步完成" + (f"：{'、'.join(parts)}" if parts else "")
        if errors:
            text += "\n" + "\n".join(f"· {item}" for item in errors)
        self.progress.setValue(100)
        self.progress_text.setText(text)
        self._set_busy(False)
        self._refresh_plan()
        self.main.mods_page.refresh_mods()
        self.main.toast(message, "success" if not errors else "warning")

    # ------------------------------------------------------------------
    def reject(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._closing = True
            self.progress_text.setText("正在取消同步，请稍候…")
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)
