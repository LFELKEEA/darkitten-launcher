from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from darkitten.config import APP_VERSION, Config
from darkitten.ui import theme
from darkitten.ui.main_window import MainWindow
from darkitten.ui.update_dialog import UpdateDialog
from darkitten.updater import ReleaseInfo


def make_release(version: str = "1.9.12") -> ReleaseInfo:
    return ReleaseInfo(
        version=version,
        notes=[
            "新增启动器更新检测与下载",
            "新增 DKL 下载站",
            "更新时保留游戏版本、Mod、账户、好友与设置",
        ],
        download_url="http://example.invalid/DKL-update.zip",
        page_url="http://example.invalid/",
        sha256="a" * 64,
        size=1024 * 1024,
        released="2026-10-23",
        manifest_url="http://example.invalid/update.json",
    )


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    cfg = Config.load()
    theme.set_current(cfg.theme)
    app.setStyleSheet(theme.stylesheet(cfg.theme))

    ok = True

    def check(name: str, condition: bool) -> None:
        nonlocal ok
        print(f"{name}: {'OK' if condition else 'FAIL'}")
        ok &= bool(condition)

    window = MainWindow(cfg)
    window.config.save = lambda: None
    window.show()
    app.processEvents()

    page = window.settings_page
    check(
        "settings_widgets",
        hasattr(page, "update_btn")
        and hasattr(page, "update_auto_switch")
        and hasattr(page, "update_url_edit")
        and page.update_url_edit.text() == window.config.update_url,
    )
    check(
        "settings_state",
        page.update_status.text() == window.update_state_text,
    )

    release = make_release()
    window.last_update_release = release
    window.update_state_text = f"发现新版本 v{release.version}"
    window.update_state_kind = "accent"
    window.refresh_update_state()
    app.processEvents()
    check(
        "settings_show_update",
        page.update_status.text() == f"发现新版本 v{release.version}",
    )

    dialog = UpdateDialog(window, release)
    dialog.show()
    app.processEvents()
    check(
        "dialog_layout",
        dialog.progress_box.isHidden()
        and dialog.primary_btn.text() == "下载并更新"
        and dialog.skip_btn.text() == "跳过此版本",
    )
    check("dialog_version", dialog.version_label.text() == f"v{release.version}")

    dialog._on_finished(True, "ok", r"C:\tmp\dkl_bug_work\fake-payload")
    app.processEvents()
    check(
        "download_ready",
        not dialog.progress_box.isHidden()
        and dialog.progress_bar.value() == 100
        and dialog.primary_btn.text() == "重启并更新"
        and not dialog.skip_btn.isEnabled(),
    )

    calls: list[tuple[str, str]] = []
    window.apply_update = lambda payload, version: calls.append((payload, version)) or False
    dialog.primary_btn.click()
    app.processEvents()
    check(
        "apply_click",
        calls == [(r"C:\tmp\dkl_bug_work\fake-payload", "1.9.12")]
        and dialog.primary_btn.isEnabled(),
    )
    dialog.reject()
    app.processEvents()

    skipped_config = window.config.update_skipped_version
    skip_dialog = UpdateDialog(window, release)
    skip_dialog._skip()
    app.processEvents()
    check(
        "skip_version",
        window.config.update_skipped_version == "1.9.12"
        and skip_dialog.result() == 0,
    )
    window.config.update_skipped_version = skipped_config

    opened: list[str] = []
    window.open_update_dialog = lambda rel: opened.append(rel.version)
    window.update_state_text = "尚未检查更新"
    window.update_state_kind = "idle"
    window._update_check_ready(None, "", False)
    app.processEvents()
    check(
        "up_to_date",
        window.update_state_kind == "success"
        and f"v{APP_VERSION}" in window.update_state_text
        and not opened,
    )

    window.config.update_skipped_version = "1.9.12"
    window._update_check_ready(release, "", False)
    app.processEvents()
    check(
        "auto_skip",
        window.update_state_kind == "accent" and not opened,
    )
    window._update_check_ready(release, "", True)
    app.processEvents()
    check("manual_ignores_skip", opened == ["1.9.12"])

    window.game_running = True
    check("block_update_while_game_runs", window.apply_update("x", "1.9.12") is False)
    window.game_running = False

    page.update_status.setText("")
    window.update_state_text = "测试状态"
    window.update_state_kind = "error"
    window.refresh_update_state()
    app.processEvents()
    check("refresh_state_label", page.update_status.text() == "测试状态")

    window.close()
    app.processEvents()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
