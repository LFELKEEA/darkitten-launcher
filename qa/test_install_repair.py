from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stellar.installer import Installer
from stellar.versions import check_installation, resolve_version

ROOT = Path(tempfile.gettempdir()) / "stellar_shots" / "game"
VERSION = "1.21.1"


def main() -> int:
    root = ROOT
    resolved = resolve_version(root, VERSION)
    before = check_installation(root, resolved, check_assets=True)
    print(f"before: missing={len(before)}", flush=True)

    started = time.time()
    last = {"t": 0.0}

    def progress(percent: int, message: str) -> None:
        now = time.time()
        if now - last["t"] >= 2 or percent >= 100:
            last["t"] = now
            print(f"[{now - started:6.1f}s] {percent:3d}% {message}", flush=True)

    installer = Installer(
        root,
        source="auto",
        threads=8,
        progress_cb=progress,
        log_cb=lambda m: print(f"  LOG {m}", flush=True),
    )
    installer.install(VERSION, check_assets=True)
    elapsed = time.time() - started

    after = check_installation(root, resolve_version(root, VERSION), check_assets=True)
    print(f"elapsed={elapsed:.1f}s", flush=True)
    print(f"after: missing={len(after)}", flush=True)
    if after:
        print("missing sample:", after[:10], flush=True)
    ok = not after and not installer.cancel_event.is_set()
    print("REPAIR_OK" if ok else "REPAIR_FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
