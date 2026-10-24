"""回归测试：游戏主进程退出后，即使子进程占着 stdout，也必须立刻通知界面。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QCoreApplication, QTimer

from darkitten.launch import GameProcess, LaunchPlan


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="dkl-game-finished-"))
    pid_file = workdir / "child.pid"
    script = workdir / "parent.py"
    script.write_text(
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "print('parent finished', flush=True)\n",
        encoding="utf-8",
    )

    app = QCoreApplication([])
    plan = LaunchPlan(
        command=[sys.executable, str(script)],
        cwd=str(workdir),
        java=None,  # type: ignore[arg-type]
        version=None,  # type: ignore[arg-type]
    )
    process = GameProcess(plan)
    started = time.monotonic()
    result: dict[str, object] = {"elapsed": None, "code": None, "output": []}

    def on_output(line: str) -> None:
        result["output"].append(line)

    def on_finished(code: int) -> None:
        result["elapsed"] = time.monotonic() - started
        result["code"] = code
        QTimer.singleShot(50, app.quit)

    process.output.connect(on_output)
    process.game_finished.connect(on_finished)
    process.start()

    def timeout() -> None:
        if result["elapsed"] is None:
            result["timeout"] = True
            app.quit()

    QTimer.singleShot(5000, timeout)
    app.exec()

    child_pid = ""
    try:
        child_pid = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if child_pid and sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", child_pid, "/F"],
            capture_output=True,
            creationflags=0x08000000,
        )

    elapsed = result["elapsed"]
    print(f"finished code={result['code']} elapsed={elapsed}")
    print("output:", result["output"])
    if result.get("timeout"):
        print("FAIL: game_finished was not emitted while the inherited stdout stayed open")
        return 1
    if result["code"] != 0:
        print("FAIL: unexpected exit code")
        return 1
    if elapsed is None or float(elapsed) > 3.5:
        print("FAIL: game_finished took too long")
        return 1
    print("PASS: game_finished fired promptly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
