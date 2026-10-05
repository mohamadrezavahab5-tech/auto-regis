import run_app


def test_free_threaded_runner_restarts_with_sibling_python(monkeypatch, tmp_path):
    source_python = tmp_path / "python3.14t.exe"
    regular_python = tmp_path / "python.exe"
    source_python.touch()
    regular_python.touch()
    launched = []

    monkeypatch.setattr(run_app.sys, "_is_gil_enabled", lambda: False, raising=False)
    monkeypatch.setattr(run_app.sys, "executable", str(source_python))
    monkeypatch.setattr(run_app.sys, "argv", ["run_app.py", "--safe-argument"])
    monkeypatch.setattr(run_app.os, "execv", lambda path, args: launched.append((path, args)))

    run_app._ensure_gil_enabled_python()

    assert launched == [(str(regular_python), [str(regular_python), "run_app.py", "--safe-argument"])]
