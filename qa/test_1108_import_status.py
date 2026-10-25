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

    app = QApplication.instance() or QApplication([])
    from darkitten.config import APP_VERSION, Config
    from darkitten.ui.download_center import DownloadTask, TASK_RUNNING
    from darkitten.ui.main_window import MainWindow
    from darkitten.ui.widgets import ChibiCompanion

    # 加载器页必须使用独立上下文，不能复用模组页台词。
    check("loader phrases installed", "loaders" in ChibiCompanion.PHRASES)
    check(
        "developer loader phrases installed",
        "loaders" in ChibiCompanion.DEVELOPER_PHRASES,
    )
    check(
        "loader phrases differ from mods",
        not set(ChibiCompanion.PHRASES["loaders"])
        & set(ChibiCompanion.PHRASES["mods"]),
    )
    check(
        "developer loader phrases differ from mods",
        not set(ChibiCompanion.DEVELOPER_PHRASES["loaders"])
        & set(ChibiCompanion.DEVELOPER_PHRASES["mods"]),
    )

    with tempfile.TemporaryDirectory(prefix="dkl_import_status_") as temp:
        root = Path(temp)
        config = Config(game_dir=str(root / "game"))
        config.update_auto_check = False
        window = MainWindow(config)
        window.show()
        app.processEvents()

        companions = window.loader_page.findChildren(ChibiCompanion)
        check("loader page companion", len(companions) == 1, str(len(companions)))
        check(
            "loader page uses loaders context",
            companions[0]._context == "loaders",
            companions[0]._context,
        )

        center = window.download_center
        task = DownloadTask(
            id=9001,
            title="导入整合包 · TestPack",
            content_type="modpack",
            source="",
            target=root / "game" / "versions" / "TestPack",
            progress=42,
            message="放入 mods/a.jar",
            status=TASK_RUNNING,
        )
        center._tasks[task.id] = task
        center._order.append(task.id)

        window._sync_import_status()
        check(
            "import status wording",
            "导入整合包 TestPack" in window.status_text.text(),
            window.status_text.text(),
        )
        check(
            "import status has no install wording",
            "安装" not in window.status_text.text(),
            window.status_text.text(),
        )
        check("import progress visible", window.progress_bar.isVisible())
        check("import progress value", window.progress_bar.value() == 42)
        check(
            "import cancel button",
            window.action_cancel_btn.text() == "取消导入",
            window.action_cancel_btn.text(),
        )
        check("import cancel visible", window.action_cancel_btn.isVisible())
        check("import speed hidden", not window.speed_label.isVisible())

        # 进度信号必须直接驱动底栏，而不是只更新下载中心。
        center._on_progress(task.id, 55, "放入 mods/b.jar")
        app.processEvents()
        check("import progress signal", window.progress_bar.value() == 55)
        check(
            "import progress message",
            "放入 mods/b.jar" in window.status_text.text(),
            window.status_text.text(),
        )

        # 后台更新检查完成时不能覆盖导入文案，否则会出现
        # “已是最新版本”和“取消导入”按钮同时存在的错位状态。
        window.set_status("已是最新版本 v1.10.8", "success")
        app.processEvents()
        check(
            "background status cannot overwrite import",
            "导入整合包 TestPack" in window.status_text.text(),
            window.status_text.text(),
        )
        check(
            "background status keeps import progress",
            window.progress_bar.value() == 55,
            str(window.progress_bar.value()),
        )
        check(
            "background status keeps import cancel",
            window.action_cancel_btn.text() == "取消导入",
            window.action_cancel_btn.text(),
        )

        # 同时存在安装任务时，导入优先占用底栏。
        window.install_active = True
        window._install_version = "1.21.11"
        window._install_task = None
        window._on_install_progress(20, "正在补全资源文件 1/2")
        app.processEvents()
        check(
            "import outranks install",
            "导入整合包 TestPack" in window.status_text.text(),
            window.status_text.text(),
        )
        check("import keeps progress", window.progress_bar.value() == 55)
        check(
            "import keeps cancel button",
            window.action_cancel_btn.text() == "取消导入",
            window.action_cancel_btn.text(),
        )

        center._on_finished(task.id, True, "导入完成", "TestPack")
        app.processEvents()
        check(
            "install restored after import",
            "1.21.11" in window.status_text.text()
            and "补全资源文件" in window.status_text.text(),
            window.status_text.text(),
        )
        check(
            "install cancel restored",
            window.action_cancel_btn.text() == "取消安装",
            window.action_cancel_btn.text(),
        )
        check("install progress restored", window.progress_bar.value() == 20)
        check("install speed restored", window.speed_label.isVisible())

        window._on_install_finished(True, "done")
        app.processEvents()
        check("progress hidden after all", not window.progress_bar.isVisible())
        check("cancel hidden after all", not window.action_cancel_btn.isVisible())

        # 取消按钮必须取消导入任务，而不是底层安装任务。
        window.install_active = False
        cancelled: list[bool] = []
        second = DownloadTask(
            id=9002,
            title="导入版本 · SecondPack",
            content_type="version-import",
            source="",
            target=root / "game" / "versions" / "SecondPack",
            progress=10,
            message="放入版本文件…",
            status=TASK_RUNNING,
            cancel_cb=lambda: cancelled.append(True),
        )
        center._tasks[second.id] = second
        center._order.append(second.id)
        window._sync_import_status()
        window.cancel_current_action()
        app.processEvents()
        check("import cancel callback", cancelled == [True])
        check(
            "import cancelling wording",
            "正在取消" in window.status_text.text(),
            window.status_text.text(),
        )
        check("import cancelling disabled", not window.action_cancel_btn.isEnabled())
        center._on_finished(second.id, False, "取消", "")
        app.processEvents()
        check("import finished hides progress", not window.progress_bar.isVisible())
        check("import finished hides cancel", not window.action_cancel_btn.isVisible())

        latest_text = f"已是最新版本 v{APP_VERSION}"
        window.set_status(latest_text, "success")
        app.processEvents()
        check(
            "normal status resumes after import",
            window.status_text.text() == latest_text,
            window.status_text.text(),
        )

        window.close()
        window.deleteLater()
        app.processEvents()
    print("all import status checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
