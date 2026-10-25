from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} failed {detail}")
    print(f"ok  {label}" + (f" · {detail}" if detail else ""))


def main() -> int:
    from PySide6.QtWidgets import QApplication

    import darkitten.ui.main_window as main_window_module
    from darkitten.config import Config
    from darkitten.ui.main_window import MainWindow
    from darkitten.updater import ReleaseInfo

    app = QApplication.instance() or QApplication([])
    captured: list[str] = []

    class FakeDesktopServices:
        @staticmethod
        def openUrl(url) -> None:
            captured.append(url.toString())

    with tempfile.TemporaryDirectory(prefix="dkl_site_url_") as temp:
        root = Path(temp)
        config = Config(game_dir=str(root / "game"))
        config.update_auto_check = False
        config.update_url = "https://lfelkeea.github.io/darkitten-launcher/update.json"
        window = MainWindow(config)
        window.config.save = lambda: None
        window.show()
        app.processEvents()

        original_desktop = main_window_module.QDesktopServices
        main_window_module.QDesktopServices = FakeDesktopServices
        try:
            # 用户截图路径：检查更新结果为“已是最新版本”后，release 被清空，
            # 此时点下载站必须回退到带子路径的站点根地址。
            window.last_update_release = None
            window._update_check_ready(None, "", False)
            app.processEvents()
            check("latest clears release", window.last_update_release is None)
            captured.clear()
            window.settings_page.site_btn.click()
            app.processEvents()
            check(
                "default site keeps subpath",
                captured == ["https://lfelkeea.github.io/darkitten-launcher/"],
                str(captured),
            )

            # 自定义更新源同样要保留清单所在目录，而不是只取域名根。
            window.config.update_url = "https://example.com/dkl/update.json"
            captured.clear()
            window.open_update_site()
            check(
                "custom site keeps subpath",
                captured == ["https://example.com/dkl/"],
                str(captured),
            )

            window.config.update_url = "https://example.com/update.json"
            captured.clear()
            window.open_update_site()
            check(
                "root manifest maps to root",
                captured == ["https://example.com/"],
                str(captured),
            )

            # 清单已提供 page_url 时优先使用清单值。
            window.last_update_release = ReleaseInfo(
                version="9.9.9",
                notes=[],
                download_url="https://example.com/dkl/update.zip",
                page_url="https://example.com/from-manifest/",
                sha256="0" * 64,
                size=1,
                released="2026-10-25",
                manifest_url="https://example.com/dkl/update.json",
            )
            captured.clear()
            window.open_update_site()
            check(
                "manifest page_url wins",
                captured == ["https://example.com/from-manifest/"],
                str(captured),
            )

            window.last_update_release = None
            window.config.update_url = (
                "https://lfelkeea.github.io/darkitten-launcher/update.json"
            )
            window.open_update_site()
            check(
                "default recovered",
                captured[-1] == "https://lfelkeea.github.io/darkitten-launcher/",
                captured[-1],
            )
        finally:
            main_window_module.QDesktopServices = original_desktop

        window.close()
        window.deleteLater()
        app.processEvents()

    print("all 1.11.1 download site URL checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
