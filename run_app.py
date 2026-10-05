import os
import sys


def _ensure_gil_enabled_python():
    gil_enabled = getattr(sys, "_is_gil_enabled", None)
    if gil_enabled is None or gil_enabled():
        return

    regular_python = os.path.join(os.path.dirname(sys.executable), "python.exe")
    if not os.path.isfile(regular_python):
        raise SystemExit(
            "AutoReview needs standard (GIL-enabled) Python 3.10+; install it and run run_app.py with that interpreter."
        )
    os.execv(regular_python, [regular_python, *sys.argv])


def main():
    _ensure_gil_enabled_python()
    from autoreview.app.main import main as app_main
    return app_main()

if __name__ == "__main__":
    sys.exit(main())
