"""Run the store bot and CloudSigma bot as one supervised service."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
PA_DIR = ROOT_DIR / "pa"


def terminate(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main():
    children = [
        subprocess.Popen(
            ["node", "."],
            cwd=PA_DIR,
            env=os.environ.copy(),
        ),
        subprocess.Popen(
            [sys.executable, "-u", "bot_core.py"],
            cwd=ROOT_DIR,
            env=os.environ.copy(),
        ),
    ]

    stopping = False

    def stop_all(_signum=None, _frame=None):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for child in reversed(children):
            terminate(child)

    signal.signal(signal.SIGINT, stop_all)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_all)

    try:
        while not stopping:
            for child in children:
                exit_code = child.poll()
                if exit_code is not None:
                    print(
                        f"[LAUNCHER] Process {child.args[0]} stopped "
                        f"with code {exit_code}. Stopping all services."
                    )
                    stop_all()
                    return exit_code
            time.sleep(1)
    finally:
        stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
