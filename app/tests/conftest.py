import os

import pytest

# Headless Qt for automated tests; set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class DialogRecorder:
    """Replaces modal dialogs during tests. ``confirm_answer`` controls confirm() results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.confirm_answer = True
        self.open_file = None
        self.open_files: list = []
        self.save_file = None
        self.new_passphrase = None
        self.passphrase = None

    def _record(self, kind):
        def fn(_parent, title, text, *rest):
            self.calls.append((kind, title, text))
            if kind == "confirm":
                return self.confirm_answer
            return None

        return fn

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture(autouse=True)
def dialog_log(monkeypatch, tmp_path):
    from imagetrace import paths
    from imagetrace.ui import dialogs

    rec = DialogRecorder()
    monkeypatch.setattr(dialogs, "info", rec._record("info"))
    monkeypatch.setattr(dialogs, "error", rec._record("error"))
    monkeypatch.setattr(dialogs, "confirm", rec._record("confirm"))
    monkeypatch.setattr(dialogs, "choose_open_file", lambda *a, **k: rec.open_file)
    monkeypatch.setattr(dialogs, "choose_open_files", lambda *a, **k: list(rec.open_files))
    monkeypatch.setattr(dialogs, "choose_save_file", lambda *a, **k: rec.save_file)
    monkeypatch.setattr(dialogs, "ask_new_passphrase", lambda *a, **k: rec.new_passphrase)
    monkeypatch.setattr(dialogs, "ask_passphrase", lambda *a, **k: rec.passphrase)
    # never read or write the real machine-local config during tests
    monkeypatch.setattr(paths, "local_config_path", lambda: tmp_path / "imagetrace.local.json")
    monkeypatch.delenv(paths.ENV_DATA_ROOT, raising=False)
    return rec
