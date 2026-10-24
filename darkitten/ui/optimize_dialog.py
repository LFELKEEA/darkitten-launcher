from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..optimize import (
    GRAPHICS_PROFILES,
    build_graphics_plan,
    build_optimization_plan,
    graphics_profile,
    restore_optimization,
    state_summary,
)
from ..workers import GraphicsWorker, OptimizeWorker
from .dialogs import ConfirmDialog, StyledDialog
from .widgets import SegmentedControl, badge, colors, repolish


def _human_mb(value: int) -> str:
    value = max(0, int(value or 0))
    if value >= 1024:
        return f"{value / 1024:.1f} GB"
    return f"{value} MB"


def _gpu_text(gpus) -> str:
    names = [item.name for item in gpus if getattr(item, "name", "")]
    if not names:
        return "没有读取到显卡信息"
    return " / ".join(dict.fromkeys(names))


def _options_text(options: dict[str, str]) -> str:
    if not options:
        return "还没有 options.txt，会新建一份"
    graphics = {
        "fast": "流畅",
        "fancy": "精美",
        "fabulous": "极佳",
    }.get(str(options.get("graphics") or ""), str(options.get("graphics") or "默认"))
    particles = {
        "minimal": "最少",
        "decreased": "减少",
        "all": "全部",
    }.get(str(options.get("particles") or ""), str(options.get("particles") or "默认"))
    fps = str(options.get("maxFps") or "0")
    fps_text = "无限制" if fps in ("0", "") else fps
    return (
        f"当前：画质 {graphics} · 渲染距离 {options.get('renderDistance', '默认')} "
        f"· 粒子 {particles} · 帧率上限 {fps_text}"
    )


def _section() -> tuple[QFrame, QVBoxLayout]:
    box = QFrame()
    box.setObjectName("SoftCard")
    box.setAttribute(Qt.WA_StyledBackground, True)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(15, 13, 15, 13)
    layout.setSpacing(8)
    return box, layout


class OptimizeDialog(StyledDialog):
    """当前游戏实例的一键性能优化与画面升级。"""

    def __init__(self, main, version_id: str) -> None:
        summary = next((item for item in main.versions if item.id == version_id), None)
        super().__init__(
            main,
            f"游戏优化 · {version_id or '未选择版本'}",
            "bolt",
            width=720,
        )
        self.main = main
        self.version_id = version_id
        self.summary = summary
        self._worker: OptimizeWorker | GraphicsWorker | None = None
        self._worker_kind = ""
        self._opt_plan = None
        self._graphics_plan = None
        self._closing = False

        intro = QLabel(
            "只调整当前游戏版本：写入推荐内存与 JVM 参数，安装适配加载器的优化 Mod，"
            "并按显卡选择画面档位。不会改动其他版本和全局默认设置。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        status_row = QHBoxLayout()
        status_row.setSpacing(7)
        self.state_badge = badge("正在读取配置", "gray")
        status_row.addWidget(self.state_badge)
        self.state_note = QLabel("")
        self.state_note.setObjectName("Tiny")
        self.state_note.setWordWrap(True)
        status_row.addWidget(self.state_note, 1)
        self.body.addLayout(status_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(430)
        scroll.setMaximumHeight(520)
        holder = QWidget()
        content = QVBoxLayout(holder)
        content.setContentsMargins(0, 0, 4, 0)
        content.setSpacing(10)

        self.hardware_box, hardware_layout = _section()
        self.hardware_label = QLabel("")
        self.hardware_label.setObjectName("Tiny")
        self.hardware_label.setWordWrap(True)
        self.hardware_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hardware_layout.addWidget(self.hardware_label)
        content.addWidget(self.hardware_box)

        self.performance_box, performance_layout = _section()
        perf_head = QHBoxLayout()
        perf_title = QLabel("一键性能优化")
        perf_title.setStyleSheet("font-weight: 700; font-size: 13px;")
        perf_head.addWidget(perf_title)
        perf_head.addWidget(badge("推荐", "green"))
        perf_head.addStretch(1)
        performance_layout.addLayout(perf_head)

        self.memory_label = QLabel("")
        self.memory_label.setObjectName("Tiny")
        self.memory_label.setWordWrap(True)
        performance_layout.addWidget(self.memory_label)

        self.jvm_label = QLabel("")
        self.jvm_label.setObjectName("Tiny")
        self.jvm_label.setWordWrap(True)
        performance_layout.addWidget(self.jvm_label)

        self.mods_label = QLabel("")
        self.mods_label.setObjectName("Tiny")
        self.mods_label.setWordWrap(True)
        performance_layout.addWidget(self.mods_label)

        self.install_mods_box = QCheckBox("安装缺失的优化 Mod")
        self.memory_box = QCheckBox("应用推荐内存（仅当前版本）")
        self.jvm_box = QCheckBox("应用推荐 JVM 参数（仅当前版本）")
        performance_layout.addWidget(self.install_mods_box)
        performance_layout.addWidget(self.memory_box)
        performance_layout.addWidget(self.jvm_box)

        perf_actions = QHBoxLayout()
        perf_actions.addStretch(1)
        self.optimize_btn = QPushButton("一键性能优化")
        self.optimize_btn.setObjectName("Primary")
        self.optimize_btn.setCursor(Qt.PointingHandCursor)
        self.optimize_btn.setMinimumWidth(126)
        self.optimize_btn.clicked.connect(self._start_optimize)
        perf_actions.addWidget(self.optimize_btn)
        performance_layout.addLayout(perf_actions)
        content.addWidget(self.performance_box)

        self.graphics_box, graphics_layout = _section()
        graphics_head = QHBoxLayout()
        graphics_title = QLabel("画面一键升级")
        graphics_title.setStyleSheet("font-weight: 700; font-size: 13px;")
        graphics_head.addWidget(graphics_title)
        self.graphics_recommend = badge("按显卡推荐", "blue")
        graphics_head.addWidget(self.graphics_recommend)
        graphics_head.addStretch(1)
        graphics_layout.addLayout(graphics_head)

        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)
        profile_caption = QLabel("画面档位")
        profile_caption.setStyleSheet("font-weight: 600;")
        profile_row.addWidget(profile_caption)
        self.profile_tabs = SegmentedControl()
        for profile in GRAPHICS_PROFILES:
            self.profile_tabs.add_item(profile.title, profile.key)
        self.profile_tabs.changed.connect(self._on_profile_changed)
        profile_row.addWidget(self.profile_tabs)
        profile_row.addStretch(1)
        graphics_layout.addLayout(profile_row)

        self.graphics_detail = QLabel("")
        self.graphics_detail.setObjectName("Tiny")
        self.graphics_detail.setWordWrap(True)
        graphics_layout.addWidget(self.graphics_detail)

        self.options_box = QCheckBox("写入画面参数（自动备份原 options.txt）")
        self.iris_box = QCheckBox("安装 Iris 光影加载器")
        self.shader_box = QCheckBox("安装 Complementary Reimagined 光影并设为默认")
        graphics_layout.addWidget(self.options_box)
        graphics_layout.addWidget(self.iris_box)
        graphics_layout.addWidget(self.shader_box)

        graphics_actions = QHBoxLayout()
        graphics_actions.addStretch(1)
        self.graphics_btn = QPushButton("应用画面方案")
        self.graphics_btn.setObjectName("Primary")
        self.graphics_btn.setCursor(Qt.PointingHandCursor)
        self.graphics_btn.setMinimumWidth(126)
        self.graphics_btn.clicked.connect(self._start_graphics)
        graphics_actions.addWidget(self.graphics_btn)
        graphics_layout.addLayout(graphics_actions)
        content.addWidget(self.graphics_box)

        self.warn_box, warn_layout = _section()
        warn_title = QLabel("注意事项")
        warn_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        warn_layout.addWidget(warn_title)
        self.warn_label = QLabel("")
        self.warn_label.setObjectName("Tiny")
        self.warn_label.setWordWrap(True)
        warn_layout.addWidget(self.warn_label)
        content.addWidget(self.warn_box)

        self.result_label = QLabel("")
        self.result_label.setObjectName("Tiny")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_label.setVisible(False)
        content.addWidget(self.result_label)
        content.addStretch(1)
        scroll.setWidget(holder)
        self.body.addWidget(scroll, 1)

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

        self.add_button("打开版本目录", "Secondary", self._open_folder)
        self.restore_btn = self.add_button("撤销优化", "Secondary", self._restore)
        self.close_btn = self.add_button("关闭", "Secondary", self.reject)

        if summary is None:
            self._show_error("找不到这个游戏版本，可能已经被删除。")
            return
        self._reload_plans(initial=True)

    # ------------------------------------------------------------------
    def _show_error(self, message: str) -> None:
        self.state_badge.setText("不可用")
        self.state_badge.setProperty("badge", "red")
        repolish(self.state_badge)
        self.state_note.setText(message)
        self.hardware_box.setVisible(False)
        self.performance_box.setVisible(False)
        self.graphics_box.setVisible(False)
        self.warn_box.setVisible(False)
        self.optimize_btn.setEnabled(False)
        self.graphics_btn.setEnabled(False)
        self.restore_btn.setVisible(False)

    def _reload_plans(self, initial: bool = False) -> None:
        profile_key = ""
        if self._graphics_plan is not None:
            profile_key = self._graphics_plan.profile.key
        try:
            opt_plan = build_optimization_plan(
                self.version_id, self.main.config, self.main.versions
            )
            gpus = opt_plan.gpus
            graphics_plan = build_graphics_plan(
                self.version_id,
                self.main.config,
                self.main.versions,
                profile_key,
                gpus=gpus,
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._opt_plan = opt_plan
        self._graphics_plan = graphics_plan
        self.profile_tabs.set_value(graphics_plan.profile.key)
        self._update_hardware(opt_plan)
        self._update_performance(opt_plan)
        self._update_graphics(graphics_plan)
        self._update_warnings(opt_plan, graphics_plan)
        self._update_state()
        if initial:
            self._sync_defaults(opt_plan, graphics_plan)
        self.adjustSize()

    def _sync_defaults(self, opt_plan, graphics_plan) -> None:
        self.install_mods_box.setChecked(
            bool(opt_plan.has_loader and opt_plan.missing)
        )
        self.memory_box.setChecked(True)
        self.jvm_box.setChecked(True)
        self.options_box.setChecked(True)
        self.iris_box.setChecked(
            bool(graphics_plan.can_install_iris and not graphics_plan.iris_installed)
        )
        self.shader_box.setChecked(
            bool(
                not graphics_plan.shader_installed
                and (graphics_plan.iris_installed or graphics_plan.can_install_iris)
            )
        )

    def _update_hardware(self, plan) -> None:
        self.hardware_label.setText(
            f"系统内存 {_human_mb(plan.total_ram_mb)} · 显卡 {_gpu_text(plan.gpus)}\n"
            f"加载器 {plan.loader_label} · Minecraft {plan.mc_version} · "
            f"已装 Mod {plan.mod_count} 个"
        )

    def _update_performance(self, plan) -> None:
        self.memory_label.setText(
            f"推荐内存 {_human_mb(plan.recommended_memory_mb)} "
            f"（当前 {_human_mb(plan.current_memory_mb)}）"
        )
        jvm_text = plan.recommended_jvm_args
        self.jvm_label.setText(
            "推荐 JVM 参数：G1 GC + 并行引用处理"
            if plan.current_jvm_args.strip() != jvm_text
            else "当前版本已经使用推荐的 JVM 参数"
        )
        self.jvm_label.setToolTip(jvm_text)

        present = "、".join(item.title for item in plan.present)
        missing = "、".join(item.title for item in plan.missing)
        if not plan.has_loader:
            self.mods_label.setText("原版客户端没有可安装的优化 Mod，只会调整启动参数。")
        elif missing:
            text = f"将安装：{missing}"
            if present:
                text += f"\n已装：{present}"
            self.mods_label.setText(text)
        elif present:
            self.mods_label.setText(f"优化 Mod 已齐全：{present}")
        else:
            self.mods_label.setText("这个加载器暂时没有可自动安装的优化 Mod。")
        available = bool(plan.has_loader and plan.missing)
        self.install_mods_box.setEnabled(available)
        if not available:
            self.install_mods_box.setChecked(False)
        if not plan.has_loader:
            self.install_mods_box.setText("原版客户端无需优化 Mod")
        elif plan.missing:
            self.install_mods_box.setText(
                f"安装 {len(plan.missing)} 个缺失的优化 Mod"
            )
        else:
            self.install_mods_box.setText("优化 Mod 已齐全")

    def _update_graphics(self, plan) -> None:
        profile = plan.profile
        profile_note = next(
            (item.subtitle for item in GRAPHICS_PROFILES if item.key == profile.key),
            "",
        )
        recommend = graphics_profile(plan.recommended_profile)
        self.graphics_recommend.setText(f"按显卡推荐：{recommend.title}")
        self.graphics_detail.setText(
            f"{profile.title} · {profile_note}\n{_options_text(plan.current_options)}"
        )
        iris_installed = plan.iris_installed
        self.iris_box.setEnabled(bool(plan.can_install_iris and not iris_installed))
        if iris_installed:
            self.iris_box.setText("Iris 已安装")
        elif plan.can_install_iris:
            self.iris_box.setText("安装 Iris 光影加载器")
        else:
            self.iris_box.setText("当前加载器不支持自动安装 Iris")
        shader_possible = bool(iris_installed or plan.can_install_iris)
        self.shader_box.setEnabled(shader_possible and not plan.shader_installed)
        if plan.shader_installed:
            self.shader_box.setText("Complementary Reimagined 已安装")
        else:
            self.shader_box.setText(
                "安装 Complementary Reimagined 光影并设为默认"
            )
        if plan.shader_installed:
            self.shader_box.setChecked(False)
        if not self.iris_box.isEnabled():
            self.iris_box.setChecked(False)
        self.options_box.setEnabled(True)

    def _update_warnings(self, opt_plan, graphics_plan) -> None:
        warnings = list(dict.fromkeys([*opt_plan.warnings, *graphics_plan.warnings]))
        self.warn_label.setText("\n".join(f"· {item}" for item in warnings))
        self.warn_box.setVisible(bool(warnings))

    def _update_state(self) -> None:
        state = state_summary(self.version_id, self.main.config)
        active = bool(state)
        if active:
            installed = state.get("installed") or []
            self.state_badge.setText("已应用优化")
            self.state_badge.setProperty("badge", "green")
            self.state_note.setText(
                f"记录到 {len(installed)} 个由启动器安装的文件；"
                "撤销时会把它们移到优化备份目录，不会直接删除。"
            )
        else:
            self.state_badge.setText("尚未优化")
            self.state_badge.setProperty("badge", "gray")
            self.state_note.setText(
                "优化只影响当前版本，随时可以撤销并恢复原设置。"
            )
        repolish(self.state_badge)
        self.restore_btn.setVisible(active)

    # ------------------------------------------------------------------
    def _on_profile_changed(self, key: str) -> None:
        if self._worker is not None:
            return
        try:
            self._graphics_plan = build_graphics_plan(
                self.version_id,
                self.main.config,
                self.main.versions,
                key,
                gpus=self._opt_plan.gpus if self._opt_plan else None,
            )
        except Exception as exc:
            self.main.toast(str(exc), "error")
            return
        self._update_graphics(self._graphics_plan)

    def _start_optimize(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._set_progress(-1, "正在取消性能优化…")
            return
        if self._opt_plan is None:
            return
        choices = (
            self.install_mods_box.isChecked(),
            self.memory_box.isChecked(),
            self.jvm_box.isChecked(),
        )
        if not any(choices):
            self.main.toast("至少勾选一项性能优化内容", "info")
            return
        worker = OptimizeWorker(
            self._opt_plan,
            self.main.config,
            install_mods=choices[0],
            apply_memory=choices[1],
            apply_jvm=choices[2],
            threads=self.main.config.download_threads,
            parent=self,
        )
        self._worker = worker
        self._worker_kind = "performance"
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_optimize_finished)
        self._set_busy(True)
        self._set_progress(0, "准备性能优化…")
        worker.start()

    def _start_graphics(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._set_progress(-1, "正在取消画面升级…")
            return
        if self._graphics_plan is None:
            return
        choices = (
            self.options_box.isChecked(),
            self.iris_box.isChecked(),
            self.shader_box.isChecked(),
        )
        if not any(choices):
            self.main.toast("至少勾选一项画面升级内容", "info")
            return
        worker = GraphicsWorker(
            self._graphics_plan,
            self.main.config,
            apply_options=choices[0],
            install_iris=choices[1],
            install_shader=choices[2],
            threads=self.main.config.download_threads,
            parent=self,
        )
        self._worker = worker
        self._worker_kind = "graphics"
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_graphics_finished)
        self._set_busy(True)
        self._set_progress(0, "准备画面升级…")
        worker.start()

    def _set_busy(self, busy: bool) -> None:
        for box in (
            self.install_mods_box,
            self.memory_box,
            self.jvm_box,
            self.options_box,
            self.iris_box,
            self.shader_box,
        ):
            box.setEnabled(not busy and self._box_available(box))
        self.profile_tabs.setEnabled(not busy)
        self.restore_btn.setEnabled(not busy)
        self.close_btn.setEnabled(not busy)
        self.optimize_btn.setEnabled(not busy or self._worker_kind == "performance")
        self.graphics_btn.setEnabled(not busy or self._worker_kind == "graphics")
        if busy:
            self.optimize_btn.setText(
                "取消优化"
                if self._worker_kind == "performance"
                else "一键性能优化"
            )
            self.graphics_btn.setText(
                "取消升级"
                if self._worker_kind == "graphics"
                else "应用画面方案"
            )
        else:
            self.optimize_btn.setText("一键性能优化")
            self.graphics_btn.setText("应用画面方案")
        if not busy and self._opt_plan is not None:
            self.install_mods_box.setEnabled(
                bool(self._opt_plan.has_loader and self._opt_plan.missing)
            )

    def _box_available(self, box) -> bool:
        if box is self.install_mods_box:
            return bool(self._opt_plan and self._opt_plan.has_loader and self._opt_plan.missing)
        if box is self.iris_box:
            return bool(
                self._graphics_plan
                and self._graphics_plan.can_install_iris
                and not self._graphics_plan.iris_installed
            )
        if box is self.shader_box:
            return bool(
                self._graphics_plan
                and (self._graphics_plan.iris_installed or self._graphics_plan.can_install_iris)
                and not self._graphics_plan.shader_installed
            )
        return True

    def _set_progress(self, percent: int, message: str) -> None:
        self.progress_box.setVisible(True)
        if percent >= 0:
            self.progress.setValue(max(0, min(100, percent)))
        self.progress_text.setText(message)

    def _on_progress(self, percent: int, message: str) -> None:
        self._set_progress(percent, message)

    def _on_optimize_finished(self, success: bool, message: str, result) -> None:
        self._worker = None
        self._worker_kind = ""
        if self._closing:
            self._closing = False
            super().reject()
            return
        if not success or result is None:
            self._finish_failure(message)
            return
        parts = []
        if getattr(result, "memory_mb", 0):
            parts.append(f"内存 {_human_mb(result.memory_mb)}")
        if getattr(result, "jvm_args", ""):
            parts.append("JVM 参数")
        installed = getattr(result, "installed", []) or []
        if installed:
            parts.append(f"{len(installed)} 个优化 Mod")
        errors = getattr(result, "errors", []) or []
        if getattr(result, "cancelled", False):
            parts.append("已取消")
        text = "性能优化完成" + (f"：{'、'.join(parts)}" if parts else "")
        if errors:
            text += "\n" + "\n".join(f"· {item}" for item in errors)
        self._finish_success(text, errors)
        self.main.toast(message, "success" if not errors else "warning")

    def _on_graphics_finished(self, success: bool, message: str, result) -> None:
        self._worker = None
        self._worker_kind = ""
        if self._closing:
            self._closing = False
            super().reject()
            return
        if not success or result is None:
            self._finish_failure(message)
            return
        parts = []
        if getattr(result, "options_written", False):
            parts.append("画面参数")
        installed = getattr(result, "installed", []) or []
        if installed:
            parts.append(f"{len(installed)} 个文件")
        if getattr(result, "shader_file", ""):
            parts.append(f"光影 {result.shader_file}")
        if getattr(result, "cancelled", False):
            parts.append("已取消")
        errors = getattr(result, "errors", []) or []
        text = "画面升级完成" + (f"：{'、'.join(parts)}" if parts else "")
        if errors:
            text += "\n" + "\n".join(f"· {item}" for item in errors)
        self._finish_success(text, errors)
        self.main.toast(message, "success" if not errors else "warning")

    def _finish_failure(self, message: str) -> None:
        self._set_busy(False)
        self.progress.setValue(0)
        self._set_progress(0, f"操作失败：{message}")
        self.main.toast(message or "优化失败", "error")

    def _finish_success(self, text: str, errors: list[str]) -> None:
        self._set_busy(False)
        self.progress.setValue(100)
        self.result_label.setText(text)
        self.result_label.setStyleSheet(
            f"color: {colors()['orange']};" if errors else f"color: {colors()['green']};"
        )
        self.result_label.setVisible(True)
        self._reload_plans()
        self.main.mods_page.refresh_mods()

    # ------------------------------------------------------------------
    def _restore(self) -> None:
        if self._worker is not None:
            return
        state = state_summary(self.version_id, self.main.config)
        if not state:
            return
        installed = state.get("installed") or []
        confirm = ConfirmDialog(
            self,
            "撤销当前版本的优化？",
            "会恢复优化前保存的内存、JVM 参数、options.txt 和 Iris 配置，"
            "并把启动器安装的优化 Mod 移到优化备份目录。",
            "撤销优化",
            "继续保留",
            "warning",
            details=(
                f"将处理 {len(installed)} 个由启动器安装的文件。"
                "手动改过内容的文件会保留原位，不会覆盖或删除。"
            ),
        )
        if confirm.exec() != ConfirmDialog.Accepted:
            return
        try:
            result = restore_optimization(self.version_id, self.main.config)
        except Exception as exc:
            self.main.toast(str(exc), "error")
            return
        bits = []
        if result.settings_restored:
            bits.append("启动参数")
        if result.options_restored:
            bits.append("画面设置")
        if result.iris_restored:
            bits.append("Iris 配置")
        if result.moved_files:
            bits.append(f"{len(result.moved_files)} 个文件已移到备份目录")
        if result.kept_files:
            bits.append(f"{len(result.kept_files)} 个文件因内容变化被保留")
        text = "已撤销优化" + (f"：{'、'.join(bits)}" if bits else "")
        if result.errors:
            text += "\n" + "\n".join(f"· {item}" for item in result.errors)
        self.result_label.setText(text)
        self.result_label.setVisible(True)
        self._set_progress(0, "优化已撤销")
        self._reload_plans()
        self.main.mods_page.refresh_mods()
        self.main.toast("已撤销优化", "success" if not result.errors else "warning")

    def _open_folder(self) -> None:
        self.main.open_path(self.main.config.version_game_path(self.version_id))

    # ------------------------------------------------------------------
    def reject(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._closing = True
            self._set_progress(-1, "正在取消并清理任务，请稍候…")
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)
