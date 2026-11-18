"""Tests : BaseWorker (signaux, annulation, processus, parallélisme, progression)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import contextlib
import logging
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from azimut.core import workers
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


def _process_alive(pid: int) -> bool:
    """Vrai si le processus existe encore et n'est pas un zombie (Linux : /proc ; ailleurs : kill 0)."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace").split()[2]
    except (OSError, IndexError):
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    return state != "Z"


@pytest.mark.skipif(sys.platform == "win32", reason="groupes de processus POSIX (CORE-1)")
def test_cancel_kills_grandchild_of_a_wrapper(qapp):
    """Le binaire fourni par l'utilisateur peut être un script qui lance le vrai outil : l'annulation doit
    aussi arrêter ce petit-fils, sinon il reste orphelin et continue d'écrire dans la sortie (CORE-1)."""
    pids: list[int] = []

    def work(w):
        code = ("import subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "print(child.pid, flush=True)\n"
                "time.sleep(60)\n")
        result = w.run_process([PY, "-c", code], on_stdout_line=lambda line: pids.append(int(line)))
        w.check_cancelled()
        return result

    worker = FuncWorker(work)
    rec = Recorder(worker)
    worker.start()
    assert wait_until(lambda: pids), "le petit-fils n'a pas démarré"
    grandchild = pids[0]
    worker.cancel()
    assert worker.wait(4000) and rec.cancelled == 1
    assert wait_until(lambda: not _process_alive(grandchild), timeout=5), "le petit-fils survit à l'annulation"


@pytest.mark.skipif(sys.platform == "win32", reason="groupes de processus POSIX (CORE-1)")
def test_run_process_returns_when_a_detached_grandchild_holds_the_pipe(qapp, tmp_path):
    """Un petit-fils détaché (setsid) échappe au groupe et garde stdout ouvert : run_process ne doit pas
    rester bloqué à la fermeture des flux, sinon le worker ne rend jamais la main (CORE-1)."""
    script = tmp_path / "petit_fils.py"
    script.write_text("import os, sys, time\nos.setsid()\nopen(sys.argv[1], 'w').write('ok')\n"
                      "time.sleep(20)\n", encoding="utf-8")
    marker = tmp_path / "detache"
    pids: list[int] = []
    code = ("import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(60)\n")

    def work(w):
        result = w.run_process([PY, "-c", code, str(script), str(marker)],
                               on_stdout_line=lambda line: pids.append(int(line)))
        w.check_cancelled()
        return result

    worker = FuncWorker(work)
    rec = Recorder(worker)
    worker.start()
    assert wait_until(lambda: pids and marker.exists()), "le petit-fils détaché n'a pas démarré"
    grandchild = pids[0]
    worker.cancel()
    stopped = worker.wait(8000)
    if not stopped:                                          # nettoyage : ne pas laisser un fil bloqué
        with contextlib.suppress(OSError):
            os.kill(grandchild, signal.SIGKILL)
        worker.wait(10000)
    assert stopped, "run_process reste bloqué par un tuyau tenu ouvert par un petit-fils détaché"
    assert rec.cancelled == 1


def test_cleanup_branch_has_signal_imported():
    """La branche de nettoyage de CORE-1 tue le petit-fils avec ``signal.SIGKILL`` ; elle n'est atteinte que si
    le worker reste bloqué, donc le nom doit exister dans le module (F821 : l'import manquait)."""
    assert "signal" in globals(), "tests/core/test_workers.py doit importer signal pour sa branche de nettoyage"


def _no_such_group(pid: int) -> int:
    """Aucun vrai groupe : un test de la branche Windows ne doit jamais envoyer de signal système."""
    raise OSError(f"PID factice {pid} : aucun groupe")


def test_terminate_tree_uses_taskkill_without_force_on_windows(monkeypatch):
    """Windows : TerminateProcess ne voit que l'enfant direct, _terminate_tree tente taskkill /T."""
    calls, terminated = [], []

    class Proc:
        pid = 111

        def poll(self):
            return None

        def terminate(self):
            terminated.append(True)

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(workers.os, "getpgid", _no_such_group)
    monkeypatch.setattr(workers, "_IS_WINDOWS", True)
    monkeypatch.setattr(workers.subprocess, "run", fake_run)
    assert workers._terminate_tree(Proc()) is True
    assert calls[0][0] == ["taskkill", "/T", "/PID", "111"]
    assert terminated == []


def test_terminate_tree_falls_back_to_terminate_when_taskkill_fails(monkeypatch):
    """taskkill absent ou refusé : repli sur TerminateProcess, sans exception."""
    terminated = []

    class Proc:
        pid = 112

        def poll(self):
            return None

        def terminate(self):
            terminated.append(True)

    monkeypatch.setattr(workers.os, "getpgid", _no_such_group)
    monkeypatch.setattr(workers, "_IS_WINDOWS", True)
    monkeypatch.setattr(workers.subprocess, "run",
                        lambda args, **kwargs: subprocess.CompletedProcess(args, 1))
    assert workers._terminate_tree(Proc()) is True
    assert terminated == [True]


def test_kill_tree_uses_taskkill_force_on_windows(monkeypatch):
    """Le kill forcé vise le sous-arbre (taskkill /F) et retombe sur kill() si taskkill échoue."""
    calls, killed = [], []

    class Proc:
        pid = 222

        def poll(self):
            return None

        def kill(self):
            killed.append(True)

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(workers.os, "getpgid", _no_such_group)
    monkeypatch.setattr(workers, "_IS_WINDOWS", True)
    monkeypatch.setattr(workers.subprocess, "run", fake_run)
    workers._kill_tree([Proc()])
    assert calls == [["taskkill", "/T", "/F", "/PID", "222"]]
    assert killed == [True]


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


# ── Blocage, priorité, garde mémoire ──────────────────────────────────────────
def test_run_process_stall_detection_kills_frozen_process(qapp):
    w = BaseWorker()
    t0 = time.monotonic()
    res = w.run_process([PY, "-c", "import time; time.sleep(30)"], stall_timeout=0.6, activity=lambda: 0)
    assert time.monotonic() - t0 < 8
    assert res.stalled and res.timed_out and not res.cancelled and res.returncode != 0


def test_run_process_progress_prevents_stall(qapp):
    counter = {"n": 0}

    def activity():
        counter["n"] += 1                       # la valeur change à chaque sondage : le processus « avance »
        return counter["n"]

    w = BaseWorker()
    res = w.run_process([PY, "-c", "import time; time.sleep(1.2)"], stall_timeout=0.5, activity=activity)
    assert not res.stalled and res.returncode == 0


@pytest.mark.skipif(sys.platform == "win32", reason="nice Unix")
def test_run_process_nice_lowers_priority(qapp):
    w = BaseWorker()
    code = "import os; print(os.nice(0))"
    base = int(w.run_process([PY, "-c", code]).stdout)
    low = int(w.run_process([PY, "-c", code], nice=10).stdout)
    assert low >= base + 10 or low == 19


def test_run_process_can_skip_stdout_capture(qapp):
    """Un appelant qui ne lit pas stdout ne le garde pas en mémoire (CORE-4) ; le rappel
    reçoit toujours toutes les lignes."""
    lines: list[str] = []
    w = BaseWorker()
    code = "import sys; sys.stdout.write('y' * 2000000 + '\\n'); sys.stderr.write('err')"
    res = w.run_process([PY, "-c", code], on_stdout_line=lines.append, capture_stdout=False)
    assert res.returncode == 0 and res.stdout == ""
    assert res.stderr.strip() == "err"
    assert "".join(lines).strip() == "y" * 2_000_000


def test_memory_guard_helpers():
    from azimut.core import sysinfo
    ms = sysinfo.MemoryStatus
    assert sysinfo.memory_ok(None)
    assert sysinfo.memory_ok(ms(50.0, 50.0))
    assert not sysinfo.memory_ok(ms(80.0, 20.0))                      # trop utilisée
    assert not sysinfo.memory_ok(ms(60.0, 10.0))                      # réserve insuffisante
    status = sysinfo.memory_status()
    assert status is None or (0 <= status.used_pct <= 100 and 0 <= status.available_pct <= 100)


def test_stream_read_and_close_failures_are_logged(caplog):
    """Un flux déjà cassé reste ignoré, mais laisse une trace au lieu d'un `pass` muet (LINT-001)."""

    class FluxCasse:
        def close(self):
            raise OSError("flux fermé")

        def readline(self, *args):
            raise OSError("stdout cassé")

        def read1(self, *args):
            raise OSError("stderr cassé")

    lines: queue.Queue = queue.Queue()
    buf = bytearray()
    with caplog.at_level(logging.DEBUG, logger="azimut"):
        workers._close_quietly(FluxCasse())
        workers._pump_stdout(FluxCasse(), lines)
        workers._drain_stderr(FluxCasse(), buf)

    messages = [record.getMessage() for record in caplog.records]
    assert any("Fermeture de flux" in m for m in messages)
    assert any("stdout" in m for m in messages)
    assert any("stderr" in m for m in messages)
    assert lines.get_nowait() is None                   # la sentinelle est toujours posée


def test_process_tree_failures_are_logged(monkeypatch, caplog):
    """Quand le groupe est déjà disparu, chaque repli (SIGTERM, SIGKILL, kill) est journalisé (LINT-001)."""

    class ProcFantome:
        pid = 4242

        def __init__(self):
            self.terminated = 0
            self.killed = 0
            self.kill_error = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated += 1

        def kill(self):
            self.killed += 1
            if self.kill_error:
                raise OSError("déjà terminé")

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("fantome", timeout)

    proc = ProcFantome()
    monkeypatch.setattr(workers.os, "getpgid", lambda pid: 99)

    def groupe_disparu(*args):
        raise OSError("groupe disparu")

    monkeypatch.setattr(workers.os, "killpg", groupe_disparu)

    with caplog.at_level(logging.DEBUG, logger="azimut"):
        assert workers._terminate_tree(proc) is True
        workers._kill_tree([proc])
        workers._stop_and_reap(proc)

    messages = [record.getMessage() for record in caplog.records]
    assert any("SIGTERM" in m for m in messages)
    assert any("SIGKILL" in m for m in messages)
    assert any(record.levelno == logging.WARNING and "SIGKILL" in m
               for record, m in zip(caplog.records, messages, strict=True))

    caplog.clear()
    proc.kill_error = True
    with caplog.at_level(logging.DEBUG, logger="azimut"):
        workers._kill_tree([proc])
    assert any("kill()" in record.getMessage() for record in caplog.records)


@pytest.mark.skipif(sys.platform == "win32", reason="priorité de processus POSIX (LINT-001)")
def test_nice_failure_is_logged(qapp, monkeypatch, caplog):
    """Un `nice` refusé par l'OS n'empêche pas la commande, mais n'est plus avalé sans trace (LINT-001)."""

    def refus(*args):
        raise OSError("priorité refusée")

    monkeypatch.setattr(workers.os, "getpriority", refus)
    with caplog.at_level(logging.DEBUG, logger="azimut"):
        worker, rec = run_to_end(lambda w: w.run_process([PY, "-c", "print('ok')"], nice=5))

    assert rec.completed and rec.completed[0].returncode == 0
    assert any("Priorité" in record.getMessage() for record in caplog.records)
