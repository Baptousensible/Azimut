"""Tests du garde-fou de veille système."""
from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from azimut.core import power, workers
from azimut.core.workers import BaseWorker


class FakeBackend:
    def __init__(self) -> None:
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1


def test_guard_holds_one_backend_for_nested_workers():
    backend = FakeBackend()
    guard = power._SleepGuard(factory=lambda: backend)
    guard.acquire()
    guard.acquire()
    assert backend.stops == 0
    guard.release()
    assert backend.stops == 0                         # le second worker protège encore
    guard.release()
    assert backend.stops == 1


def test_guard_is_released_when_a_worker_raises(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(power, "_guard", power._SleepGuard(factory=lambda: backend))
    with pytest.raises(RuntimeError):
        with power.keep_awake():
            raise RuntimeError("boum")
    assert backend.stops == 1


def test_macos_uses_caffeinate(monkeypatch):
    monkeypatch.setattr(power.sys, "platform", "darwin")
    monkeypatch.setattr(power.shutil, "which", lambda name: f"/usr/bin/{name}")
    commands: list[list[str]] = []
    monkeypatch.setattr(power, "_ProcessBackend", lambda command: commands.append(command) or FakeBackend())
    assert power._start_backend() is not None
    assert commands == [["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]]


def test_linux_uses_systemd_inhibit(monkeypatch):
    monkeypatch.setattr(power.sys, "platform", "linux")
    monkeypatch.setattr(power.shutil, "which", lambda name: f"/usr/bin/{name}")
    commands: list[list[str]] = []
    monkeypatch.setattr(power, "_ProcessBackend", lambda command: commands.append(command) or FakeBackend())
    assert power._start_backend() is not None
    command = commands[0]
    assert command[:2] == ["/usr/bin/systemd-inhibit", "--what=sleep"]
    assert command[-1] == "/usr/bin/cat" and "--mode=block" in command


def test_windows_uses_execution_state(monkeypatch):
    monkeypatch.setattr(power.sys, "platform", "win32")
    backend = FakeBackend()
    monkeypatch.setattr(power, "_WindowsExecutionState", lambda: backend)
    assert power._start_backend() is backend


def test_unsupported_platform_has_no_backend(monkeypatch):
    monkeypatch.setattr(power.sys, "platform", "freebsd")
    assert power._start_backend() is None


def test_worker_enters_and_leaves_the_guard(qapp, monkeypatch):
    events: list[str] = []

    @contextmanager
    def fake_guard():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(workers, "keep_awake", fake_guard)

    class Worker(BaseWorker):
        def do_work(self):
            events.append("work")
            return "fini"

    worker = Worker()
    worker.start()
    assert worker.wait(5000)
    assert events == ["enter", "work", "exit"]


def test_worker_leaves_the_guard_after_a_failure(qapp, monkeypatch):
    events: list[str] = []

    @contextmanager
    def fake_guard():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(workers, "keep_awake", fake_guard)

    class Worker(BaseWorker):
        def do_work(self):
            raise ValueError("boum")

    worker = Worker()
    worker.start()
    assert worker.wait(5000)
    assert events == ["enter", "exit"]


def _run_simple_worker() -> str:
    """Exécute un worker minimal et renvoie son résumé (échec silencieux inclus)."""
    class Worker(BaseWorker):
        def do_work(self):
            self.ok_count = 1
            self.summary = "fini"
            return "fini"

    worker = Worker()
    worker.start()
    assert worker.wait(5000), "le worker est resté bloqué"
    assert worker.ok_count == 1, "le worker n'a pas exécuté son traitement"
    return worker.summary


@pytest.mark.parametrize(("platform", "missing"), [
    ("darwin", "caffeinate"),
    ("linux", "systemd-inhibit"),
    ("linux", "cat"),
])
def test_missing_platform_tool_does_not_block_worker(qapp, monkeypatch, platform, missing):
    """Un outil système absent ne doit ni empêcher le worker ni laisser le compteur à 1."""
    monkeypatch.setattr(power.sys, "platform", platform)
    monkeypatch.setattr(power.shutil, "which",
                        lambda name: None if name == missing else f"/usr/bin/{name}")
    guard = power._SleepGuard(factory=power._start_backend)
    monkeypatch.setattr(power, "_guard", guard)

    assert _run_simple_worker() == "fini"
    assert _run_simple_worker() == "fini"             # le garde-fou reste réutilisable
    assert guard._depth == 0 and guard._backend is None


def test_windows_api_refusal_does_not_block_worker(qapp, monkeypatch):
    """SetThreadExecutionState qui renvoie 0 : repli silencieux et compteur libéré."""
    monkeypatch.setattr(power.sys, "platform", "win32")
    calls: list[int] = []

    def set_state(flags):
        calls.append(flags)
        return 0                                       # échec documenté de l'API Windows

    set_state.argtypes = None
    set_state.restype = None
    kernel32 = SimpleNamespace(SetThreadExecutionState=set_state)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)

    guard = power._SleepGuard(factory=power._start_backend)
    monkeypatch.setattr(power, "_guard", guard)

    assert _run_simple_worker() == "fini"
    assert _run_simple_worker() == "fini"
    assert calls == [0x80000001, 0x80000001]           # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    assert guard._depth == 0 and guard._backend is None
