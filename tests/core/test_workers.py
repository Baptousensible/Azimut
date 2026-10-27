"""Tests : BaseWorker (signaux, annulation, processus, parallélisme, progression)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging
import sys
import threading
import time

import pytest
from PySide6.QtCore import Qt

from azimut.core.workers import LEVELS, BaseWorker, CancelledError, ProcessResult

DIRECT = Qt.ConnectionType.DirectConnection
PY = sys.executable


class Recorder:
    """Enregistre les signaux d'un worker (connexions directes : aucune boucle d'événements requise)."""

    def __init__(self, worker: BaseWorker) -> None:
        self.progress: list = []
        self.logs: list = []
        self.statuses: list = []
        self.completed: list = []
        self.failed: list = []
        self.cancelled = 0
        worker.progress.connect(lambda d, t, s: self.progress.append((d, t, s)), DIRECT)
        worker.log.connect(lambda lv, tx: self.logs.append((lv, tx)), DIRECT)
        worker.status.connect(lambda s: self.statuses.append(s), DIRECT)
        worker.completed.connect(lambda r: self.completed.append(r), DIRECT)
        worker.failed.connect(lambda m: self.failed.append(m), DIRECT)
        worker.cancelled.connect(self._on_cancelled, DIRECT)

    def _on_cancelled(self) -> None:
        self.cancelled += 1


class FuncWorker(BaseWorker):
    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def do_work(self):
        return self._fn(self)


def run_to_end(fn, timeout_ms: int = 15000):
    worker = FuncWorker(fn)
    rec = Recorder(worker)
    worker.start()
    assert worker.wait(timeout_ms), "le worker ne s'est pas terminé"
    return worker, rec


def wait_until(cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_levels_constant():
    assert LEVELS == ("INFO", "OK", "WARN", "ERR")


def test_defaults(qapp):
    w = BaseWorker()
    assert (w.ok_count, w.error_count, w.summary) == (0, 0, "")
    assert not w.is_cancelled()
    with pytest.raises(NotImplementedError):
        w.do_work()


def test_completed_receives_result(qapp):
    def work(w):
        w.ok_count, w.summary = 3, "fini"
        return {"n": 42}

    worker, rec = run_to_end(work)
    assert rec.completed == [{"n": 42}] and rec.failed == [] and rec.cancelled == 0
    assert (worker.ok_count, worker.summary) == (3, "fini")


def test_unexpected_exception_emits_failed(qapp):
    def work(w):
        raise ValueError("boum")

    _, rec = run_to_end(work)
    assert rec.failed == ["boum"] and rec.completed == [] and rec.cancelled == 0
    errs = [t for lv, t in rec.logs if lv == "ERR"]
    assert errs and "Traceback" in errs[0] and "boum" in errs[0]


def test_cancelled_error_emits_cancelled(qapp):
    def work(w):
        w.cancel()
        w.check_cancelled()
        return "jamais"

    _, rec = run_to_end(work)
    assert rec.cancelled == 1 and rec.completed == [] and rec.failed == []


def test_finished_is_emitted_last(qapp):
    order: list[str] = []

    def work(w):
        return 1

    worker = FuncWorker(work)
    worker.completed.connect(lambda r: order.append("completed"), DIRECT)
    worker.finished.connect(lambda: order.append("finished"), DIRECT)
    worker.start()
    assert worker.wait(10000)
    assert order == ["completed", "finished"]


def test_cancel_during_run_process(qapp):
    def work(w):
        result = w.run_process([PY, "-c", "import time; time.sleep(30)"])
        w.check_cancelled()
        return result

    worker = FuncWorker(work)
    rec = Recorder(worker)
    worker.start()
    assert wait_until(lambda: len(worker._procs) > 0)                 # le processus est lancé
    t0 = time.monotonic()
    worker.cancel()
    assert worker.wait(4000), "annulation trop lente"
    assert time.monotonic() - t0 < 4
    assert rec.cancelled == 1 and rec.completed == [] and rec.failed == []
    assert worker._procs == set()                                     # registre vidé


def test_run_process_output_callback_and_threads(qapp):
    ids: dict[str, int] = {}
    lines: list[str] = []

    def on_line(line: str) -> None:
        lines.append(line)
        ids.setdefault("callback", threading.get_ident())

    def work(w):
        ids["work"] = threading.get_ident()
        code = "import sys; print('a'); print('b'); sys.stderr.write('err'); sys.exit(3)"
        return w.run_process([PY, "-c", code], on_stdout_line=on_line)

    _, rec = run_to_end(work)
    res = rec.completed[0]
    assert isinstance(res, ProcessResult)
    assert (res.returncode, res.stdout, res.stderr) == (3, "a\nb\n", "err")
    assert not res.timed_out and not res.cancelled
    assert lines == ["a", "b"]
    assert ids["callback"] == ids["work"]                             # rappel exécuté dans le thread du worker


def test_run_process_timeout_on_silent_process(qapp):
    w = BaseWorker()
    t0 = time.monotonic()
    res = w.run_process([PY, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert time.monotonic() - t0 < 8
    assert res.timed_out and not res.cancelled and res.returncode != 0


def test_run_process_already_cancelled_does_not_launch(qapp, tmp_path):
    w = BaseWorker()
    w.cancel()
    res = w.run_process([str(tmp_path / "ne-doit-pas-etre-lance")])
    assert res == ProcessResult(-1, "", "", cancelled=True)


def test_run_process_missing_executable(qapp, tmp_path):
    w = BaseWorker()
    rec = Recorder(w)
    res = w.run_process([str(tmp_path / "introuvable-xyz")])
    assert res.returncode == -1 and "introuvable-xyz" in res.stderr
    assert any(lv == "ERR" for lv, _ in rec.logs)


def test_run_process_large_output_no_deadlock_and_stderr_cap(qapp):
    code = "import sys; sys.stderr.write('x' * 300000); sys.stdout.write('y' * 300000)"
    w = BaseWorker()
    res = w.run_process([PY, "-c", code], timeout=30)
    assert res.returncode == 0 and not res.timed_out
    assert 60000 <= len(res.stderr) <= 64 * 1024
    assert len(res.stdout.strip()) == 300000


def test_run_process_cwd_and_env(qapp, tmp_path):
    w = BaseWorker()
    env = dict(os.environ, AZIMUT_TEST="valeur")
    code = "import os; print(os.getcwd()); print(os.environ['AZIMUT_TEST'])"
    res = w.run_process([PY, "-c", code], cwd=tmp_path, env=env)
    out = res.stdout.splitlines()
    assert os.path.realpath(out[0]) == os.path.realpath(str(tmp_path)) and out[1] == "valeur"


def test_callback_exception_stops_process(qapp):
    w = BaseWorker()

    def boom(line: str) -> None:
        raise CancelledError()

    code = "import time; print('go', flush=True); time.sleep(30)"
    t0 = time.monotonic()
    with pytest.raises(CancelledError):
        w.run_process([PY, "-c", code], on_stdout_line=boom)
    assert time.monotonic() - t0 < 10
    assert w._procs == set()


def test_map_parallel_keeps_order_and_logs_failures(qapp):
    def fn(x: int) -> int:
        time.sleep(0.01 * (8 - x))                                    # les derniers finissent d'abord
        if x == 3:
            raise ValueError("trois")
        return x * x

    def work(w):
        return w.map_parallel(fn, list(range(8)), workers=4, label="Calcul")

    _, rec = run_to_end(work)
    assert rec.completed == [[0, 1, 4, None, 16, 25, 36, 49]]
    warns = [t for lv, t in rec.logs if lv == "WARN"]
    assert len(warns) == 1 and "trois" in warns[0]
    assert rec.progress[-1] == (8, 8, "Calcul")


def test_map_parallel_empty_and_cancel(qapp):
    assert BaseWorker().map_parallel(lambda x: x, []) == []

    def work(w):
        threading.Timer(0.3, w.cancel).start()
        return w.map_parallel(lambda x: time.sleep(0.5), list(range(40)), workers=2)

    t0 = time.monotonic()
    _, rec = run_to_end(work)
    assert rec.cancelled == 1 and rec.completed == []
    assert time.monotonic() - t0 < 8                                  # 40 × 0,5 s / 2 = 10 s sans annulation


def test_emit_progress_is_throttled(qapp):
    def work(w):
        for i in range(500):
            w.emit_progress(i, 1000, "x")
        w.emit_progress(1000, 1000, "fin")

    _, rec = run_to_end(work)
    assert rec.progress[0] == (0, 1000, "x")
    assert rec.progress[-1] == (1000, 1000, "fin")
    assert len(rec.progress) < 50


def test_emit_progress_always_emits_completion(qapp):
    w = BaseWorker()
    rec = Recorder(w)
    w.emit_progress(5, 10, "a")
    w.emit_progress(6, 10, "b")                                       # trop proche : ignoré
    w.emit_progress(10, 10, "c")                                      # fin : toujours émis
    assert rec.progress == [(5, 10, "a"), (10, 10, "c")]


def test_emit_log_and_status(qapp, caplog):
    w = BaseWorker()
    rec = Recorder(w)
    with caplog.at_level(logging.INFO, logger="azimut"):
        w.emit_log("WARN", "attention")
        w.emit_log("BIZARRE", "inconnu")                              # niveau inconnu → INFO
        w.emit_status("En cours…")
    assert rec.logs == [("WARN", "attention"), ("INFO", "inconnu")]
    assert rec.statuses == ["En cours…"]
    assert "attention" in caplog.text and "inconnu" in caplog.text
