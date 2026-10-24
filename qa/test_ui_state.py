from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from darkitten.config import APP_NAME, Config
from darkitten.mods import ModInfo
from darkitten.ui.main_window import MainWindow
from darkitten.ui.pages.mods import ModRow
from darkitten.ui.widgets import VersionRow


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(Config.load())
    ok = True

    title_label = window.findChild(QLabel, "AppName")
    title_ok = (
        title_label is not None
        and title_label.minimumWidth()
        >= title_label.fontMetrics().horizontalAdvance(APP_NAME)
    )
    print("titlebar_app_name:", "OK" if title_ok else "FAIL")
    ok &= title_ok

    if window.versions:
        summary = window.versions[0]
        row = VersionRow(summary)
        captured: list[str] = []
        row.delete_requested.connect(captured.append)
        buttons = [b for b in row.findChildren(QPushButton) if b.toolTip() == "删除此版本"]
        row_ok = bool(buttons) and not buttons[0].testAttribute(
            Qt.WA_TransparentForMouseEvents
        )
        if buttons:
            buttons[0].click()
        row_ok = row_ok and captured == [summary.id]
        print("version_row_delete:", "OK" if row_ok else "FAIL")
        ok &= row_ok

        exported: list[str] = []
        row.export_requested.connect(exported.append)
        export_buttons = [
            b for b in row.findChildren(QPushButton)
            if b.toolTip().startswith("打包此版本")
        ]
        export_ok = bool(export_buttons) and not export_buttons[0].testAttribute(
            Qt.WA_TransparentForMouseEvents
        )
        if export_buttons:
            export_buttons[0].click()
        export_ok = export_ok and exported == [summary.id]
        print("version_row_export:", "OK" if export_ok else "FAIL")
        ok &= export_ok

        try:
            from darkitten.ui.pack_dialog import PackExportDialog, PackImportDialog

            export_dialog = PackExportDialog(window, summary.id)
            import_dialog = PackImportDialog(window)
            dialogs_ok = (
                export_dialog.title_label.text() == "打包带走"
                and import_dialog.title_label.text() == "领养整合包"
            )
            export_dialog.deleteLater()
            import_dialog.deleteLater()
        except Exception as exc:
            print("pack_dialogs:", "FAIL", exc)
            dialogs_ok = False
        else:
            print("pack_dialogs:", "OK" if dialogs_ok else "FAIL")
        ok &= bool(dialogs_ok)

    home = window.home_page
    # 干净环境里可能没有已安装版本，给状态切换一个占位版本，避免 _refresh_status 提前返回。
    home._selected = home._selected or "ui-state-test"
    window.launch_active = True
    home.set_launching(True)
    launching_ok = (
        home.launch_btn.text() == "取消启动"
        and home.launch_btn.objectName() == "Danger"
        and home.launch_btn.isEnabled()
    )
    print("home_launching:", "OK" if launching_ok else "FAIL")
    ok &= launching_ok

    window.launch_active = False
    window.game_running = True
    home.set_game_running(True)
    running_ok = (
        home.launch_btn.text() == "关闭游戏"
        and home.launch_btn.objectName() == "Danger"
    )
    print("home_running:", "OK" if running_ok else "FAIL")
    ok &= running_ok

    home.set_game_closing()
    closing_ok = (
        home.launch_btn.text() == "正在关闭…"
        and not home.launch_btn.isEnabled()
    )
    print("home_closing:", "OK" if closing_ok else "FAIL")
    ok &= closing_ok

    window.game_running = False
    home.set_game_running(False)
    idle_ok = (
        home.launch_btn.text() == "启动游戏"
        and home.launch_btn.objectName() == "Launch"
    )
    print("home_idle:", "OK" if idle_ok else "FAIL")
    ok &= idle_ok

    download = window.download_page
    version_id = window.selected_version or "test"
    window.install_active = True
    window._install_cancelling = False
    download._on_install_started(version_id)
    cancel_visible = not download.cancel_btn.isHidden() and download.cancel_btn.isEnabled()
    window.install_active = False
    download._on_install_finished(False, "下载已取消", version_id)
    cancel_hidden = download.cancel_btn.isHidden()
    cancel_ok = cancel_visible and cancel_hidden
    print("download_cancel_button:", "OK" if cancel_ok else "FAIL")
    ok &= cancel_ok

    settings = window.settings_page
    threads = [settings.thread_combo.itemData(i) for i in range(settings.thread_combo.count())]
    settings_ok = (
        len(threads) >= 4
        and settings.cleanup_btn.isEnabled()
        and settings.source_combo.findData("auto") >= 0
    )
    print("settings_controls:", "OK" if settings_ok else "FAIL", threads)
    ok &= settings_ok

    sample = ModInfo(
        path=window.config.game_path / "versions" / "x" / "mods" / "TestMod.jar",
        name="TestMod",
        version="1.0",
        loader="Fabric",
        enabled=True,
        size=1024,
    )
    mod_row = ModRow(sample)
    mod_captured: list[object] = []
    mod_row.delete_requested.connect(mod_captured.append)
    mod_buttons = [b for b in mod_row.findChildren(QPushButton) if b.toolTip() == "删除此 Mod"]
    mod_ok = bool(mod_buttons) and not mod_buttons[0].testAttribute(
        Qt.WA_TransparentForMouseEvents
    )
    if mod_buttons:
        mod_buttons[0].click()
    mod_ok = mod_ok and mod_captured == [sample]
    print("mod_row_delete:", "OK" if mod_ok else "FAIL")
    ok &= mod_ok

    window.close()
    app.processEvents()
    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
