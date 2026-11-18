"""Exécuteur de substitution (PAS pytest) : environnement sans réseau, sans pytest ni PySide6.
Sert uniquement à exécuter les tests qui ne dépendent pas de Qt. Hors dépôt, non livré.
Usage : python minipytest.py [-k motif] fichier_ou_dossier...
"""
from __future__ import annotations
import contextlib, importlib.util, inspect, io, itertools, logging, os, re, sys, tempfile, traceback, types
from pathlib import Path

class Skipped(Exception): ...
class _Approx:
    def __init__(s, v, rel=None, abs=None): s.v, s.rel, s.abs = v, rel, abs
    def __eq__(s, o):
        tol = s.abs if s.abs is not None else abs(s.v) * (s.rel if s.rel is not None else 1e-6)
        return abs(o - s.v) <= max(tol, 1e-12)
class _Mark:
    def __init__(s): s.calls = []
    def __getattr__(s, name):
        def deco(*a, **k):
            if len(a) == 1 and callable(a[0]) and not k and name not in ("skipif", "parametrize"):
                return a[0]
            def apply(fn):
                fn.__dict__.setdefault("_marks", []).append((name, a, k)); return fn
            return apply
        return deco
@contextlib.contextmanager
def _raises(exc, match=None):
    info = types.SimpleNamespace(value=None)
    try:
        yield info
    except exc as e:
        info.value = e
        if match is not None and not re.search(match, str(e)):
            raise AssertionError(f"message {str(e)!r} ne correspond pas à {match!r}")
    else:
        raise AssertionError(f"DID NOT RAISE {exc}")
def _fixture(fn=None, *, autouse=False, scope="function", params=None, **_):
    def deco(f):
        f._fixture = dict(autouse=autouse, scope=scope, params=params); return f
    return deco(fn) if fn else deco
def _importorskip(name, *a, **k):
    try: return importlib.import_module(name)
    except ImportError: raise Skipped(f"importorskip({name})")
def _skip(reason=""): raise Skipped(reason)
fake = types.ModuleType("pytest")
fake.fixture, fake.raises, fake.importorskip, fake.skip, fake.approx = _fixture, _raises, _importorskip, _skip, _Approx
fake.mark = _Mark()
sys.modules["pytest"] = fake

class MonkeyPatch:
    def __init__(s): s._u = []
    def setattr(s, t, n=None, v=None, raising=True):
        if isinstance(t, str):
            mod, _, attr = t.rpartition("."); v = n; n = attr
            t = importlib.import_module(mod) if mod else None
        old = getattr(t, n, _M := object())
        s._u.append(("a", t, n, old)); setattr(t, n, v)
    def delattr(s, t, n, raising=True):
        s._u.append(("a", t, n, getattr(t, n))); delattr(t, n)
    def setenv(s, k, v): s._u.append(("e", k, os.environ.get(k))); os.environ[k] = str(v)
    def delenv(s, k, raising=True): s._u.append(("e", k, os.environ.get(k))); os.environ.pop(k, None)
    def setitem(s, d, k, v): s._u.append(("i", d, k, d.get(k, _MISSING))); d[k] = v
    def delitem(s, d, k): s._u.append(("i", d, k, d.get(k, _MISSING))); d.pop(k, None)
    def chdir(s, p): s._u.append(("c", os.getcwd())); os.chdir(p)
    def syspath_prepend(s, p): s._u.append(("p", list(sys.path))); sys.path.insert(0, str(p))
    @contextlib.contextmanager
    def context(s):
        m = MonkeyPatch()
        try: yield m
        finally: m.undo()
    def undo(s):
        for u in reversed(s._u):
            if u[0] == "a":
                if u[3].__class__ is object: 
                    with contextlib.suppress(AttributeError): delattr(u[1], u[2])
                else: setattr(u[1], u[2], u[3])
            elif u[0] == "e":
                (os.environ.__setitem__(u[1], u[2]) if u[2] is not None else os.environ.pop(u[1], None))
            elif u[0] == "i":
                (u[1].__setitem__(u[2], u[3]) if u[3] is not _MISSING else u[1].pop(u[2], None))
            elif u[0] == "c": os.chdir(u[1])
            elif u[0] == "p": sys.path[:] = u[1]
        s._u.clear()
_MISSING = object()

class TmpFactory:
    def __init__(s): s.base = Path(tempfile.mkdtemp(prefix="minipytest_")); s.n = 0
    def mktemp(s, name, numbered=True):
        s.n += 1; p = s.base / f"{name}{s.n}"; p.mkdir(parents=True); return p
    def getbasetemp(s): return s.base

class _Cap:
    def __init__(s): s.o, s.e = io.StringIO(), io.StringIO(); s._po, s._pe = sys.stdout, sys.stderr
    def start(s): sys.stdout, sys.stderr = s.o, s.e
    def stop(s): sys.stdout, sys.stderr = s._po, s._pe
    def readouterr(s):
        r = types.SimpleNamespace(out=s.o.getvalue(), err=s.e.getvalue()); s.o.seek(0); s.o.truncate(); s.e.seek(0); s.e.truncate(); return r
class _Caplog(logging.Handler):
    def __init__(s): super().__init__(0); s.records = []
    def emit(s, r): s.records.append(r)
    @property
    def text(s): return "\n".join(r.getMessage() for r in s.records)
    def set_level(s, lvl, logger=None): logging.getLogger(logger).setLevel(lvl)
    def clear(s): s.records.clear()

def run_file(path: Path, factory: TmpFactory, kfilter, results):
    name = "t_" + re.sub(r"\W", "_", str(path))
    try:
        spec = importlib.util.spec_from_file_location(name, path); mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod; spec.loader.exec_module(mod)
    except ImportError as e:
        results.append((str(path), "(module entier)", "QT" if "PySide6" in str(e) or "PySide6" in str(e.name) else "ERR_IMPORT", f"import impossible : {e}")); return
    except Exception as e:
        results.append((str(path), "(module entier)", "ERR_IMPORT", f"{type(e).__name__}: {e}")); return
    fixtures = {n: o for n, o in vars(mod).items() if callable(o) and hasattr(o, "_fixture")}
    session_cache = {}
    pm = getattr(mod, "pytestmark", None)
    for tname, fn in list(vars(mod).items()):
        if not (tname.startswith("test_") and inspect.isfunction(fn)): continue
        if kfilter and not re.search(kfilter, tname): continue
        marks = list(fn.__dict__.get("_marks", []))
        params = [m for m in marks if m[0] == "parametrize"]
        combos = [{}]
        for _, a, k in params:
            names = [x.strip() for x in a[0].split(",")] if isinstance(a[0], str) else list(a[0])
            vals = a[1]; new = []
            for base in combos:
                for i, v in enumerate(vals):
                    v = v if (len(names) > 1) else (v,)
                    if hasattr(v, "values") and not isinstance(v, (tuple, list)): v = v
                    d = dict(base); d.update(zip(names, v if isinstance(v, (tuple, list)) else (v,))); d["__id"] = base.get("__id", "") + f"[{i}]"; new.append(d)
            combos = new
        for combo in combos:
            tid = tname + combo.get("__id", "")
            res = _run_one(mod, fn, fixtures, session_cache, factory, combo, marks)
            results.append((str(path), tid) + res)

def _run_one(mod, fn, fixtures, session_cache, factory, combo, marks):
    for n, a, k in marks:
        if n == "skipif" and a and a[0]: return ("SKIP", k.get("reason", ""))
    mp = MonkeyPatch(); finalizers = []; cap = None; cl = None
    home = factory.mktemp("home")
    for var, sub in (("HOME", ""), ("USERPROFILE", ""), ("APPDATA", "AppData/Roaming"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CONFIG_HOME", ".config")):
        mp.setenv(var, str(home / sub))
    live = {}
    def get(name):
        nonlocal cap, cl
        if name in combo: return combo[name]
        if name in live: return live[name]
        if name == "tmp_path": v = factory.mktemp("t")
        elif name == "tmp_path_factory": v = factory
        elif name == "monkeypatch": v = mp
        elif name in ("capsys", "capfd"): cap = _Cap(); cap.start(); v = cap
        elif name == "caplog": cl = _Caplog(); logging.getLogger().addHandler(cl); logging.getLogger().setLevel(0); v = cl
        elif name in fixtures:
            f = fixtures[name]; sc = f._fixture["scope"]
            if sc in ("session", "module") and name in session_cache: return session_cache[name]
            kwargs = {p: get(p) for p in inspect.signature(f).parameters}
            if inspect.isgeneratorfunction(f):
                g = f(**kwargs); v = next(g); finalizers.append(g)
            else: v = f(**kwargs)
            if sc in ("session", "module"): session_cache[name] = v
        else:
            raise LookupError(f"fixture introuvable : {name}")
        live[name] = v; return v
    try:
        try:
            for n, f in fixtures.items():
                if f._fixture["autouse"] and n not in ("_isolate_user_dirs",): get(n)
            kwargs = {p: get(p) for p in inspect.signature(fn).parameters}
            fn(**kwargs)
            return ("PASS", "")
        except Skipped as e: return ("SKIP", str(e))
        except LookupError as e: return ("NOFIX", str(e))
        except ImportError as e:
            return ("QT" if "PySide6" in str(e) else "FAIL", f"{type(e).__name__}: {e}")
        except AssertionError: return ("FAIL", traceback.format_exc(limit=6))
        except Exception: return ("FAIL", traceback.format_exc(limit=6))
    finally:
        if cap: cap.stop()
        if cl: logging.getLogger().removeHandler(cl)
        for g in reversed(finalizers):
            with contextlib.suppress(Exception): next(g, None)
        mp.undo()

if __name__ == "__main__":
    args = sys.argv[1:]; kf = None
    if args and args[0] == "-k": kf, args = args[1], args[2:]
    sys.path.insert(0, os.getcwd()); sys.path.insert(1, str(Path(__file__).parent / "stub"))
    files = []
    for a in args:
        p = Path(a); files += sorted(p.rglob("test_*.py")) if p.is_dir() else [p]
    factory, results = TmpFactory(), []
    for f in files: run_file(f, factory, kf, results)
    from collections import Counter
    c = Counter(r[2] for r in results)
    for r in results:
        if r[2] in ("FAIL", "ERR_IMPORT"): print(f"\n### {r[2]} {r[0]}::{r[1]}\n{r[3]}")
    per = {}
    for r in results: per.setdefault(r[0], Counter())[r[2]] += 1
    print("\n=== par fichier ===")
    for f, cc in per.items(): print(f"{f}: " + ", ".join(f"{k}={v}" for k, v in sorted(cc.items())))
    print("\n=== total ===", dict(c), "sur", len(results))
    sys.exit(1 if c.get("FAIL") or c.get("ERR_IMPORT") else 0)
