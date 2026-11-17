"""Windows-native (and portable) desk supervisor: what run.sh does on the box.
Starts main.py and publisher.py with this interpreter, writes logs/<name>.pid, restarts a child
that exits (backoff 5s -> 60s), and is single-instance via logs/supervisor.lock.
On Windows the children are put in a kill-on-close Job object, so ending the supervisor
(Scheduled Task "End", reboot, crash) never leaves an orphan desk polling Telegram.
Run:  .venv\\Scripts\\pythonw.exe -X utf8 supervisor.py   (the GrokDesk Scheduled Task does this)"""
import datetime as dt, os, signal, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = Path(os.environ.get("DESK_LOG_DIR", ROOT / "logs"))
SCRIPTS = ("main.py", "publisher.py")
MOVED = ROOT / "MOVED_TO"
# children run under console python.exe (with CREATE_NO_WINDOW) even when the supervisor is pythonw.exe
_exe = Path(sys.executable)
CHILD_PY = str(_exe.with_name("python.exe")) if _exe.name.lower() == "pythonw.exe" else sys.executable

def say(msg):
    with open(LOGS / "supervisor.log", "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")

def lock():
    """Hold an exclusive lock for the whole run; a second supervisor exits at once."""
    f = open(LOGS / "supervisor.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return f

def job_object():
    if os.name != "nt": return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    h = k32.CreateJobObjectW(None, None)
    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]
    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]
    info = EXT(); info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(wintypes.HANDLE(h), 9, ctypes.byref(info), ctypes.sizeof(info))
    return (k32, h)

def assign(job, proc):
    if not job: return
    k32, h = job
    from ctypes import wintypes
    if not k32.AssignProcessToJobObject(wintypes.HANDLE(h), wintypes.HANDLE(int(proc._handle))):
        say(f"warn: job assign failed for pid {proc.pid}")

def start(name, job):
    base = name[:-3]
    out = open(LOGS / f"{base}.out", "ab")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    flags = 0x08000000 if os.name == "nt" else 0           # CREATE_NO_WINDOW
    p = subprocess.Popen([CHILD_PY, "-X", "utf8", name], cwd=ROOT, stdout=out, stderr=out,
                         stdin=subprocess.DEVNULL, env=env, creationflags=flags)
    out.close(); assign(job, p)
    (LOGS / f"{base}.pid").write_text(str(p.pid))
    say(f"started {name} pid {p.pid}")
    return p

def main():
    LOGS.mkdir(exist_ok=True)
    if MOVED.exists():
        say(f"MOVED_TO present: desk runs on {MOVED.read_text().strip()}; not starting"); return 0
    lk = lock()
    if lk is None:
        say("another supervisor holds the lock; exiting"); return 0
    (LOGS / "supervisor.pid").write_text(str(os.getpid()))
    job = job_object()
    procs = {n: start(n, job) for n in SCRIPTS}
    backoff = {n: 5 for n in SCRIPTS}; started = {n: time.time() for n in SCRIPTS}
    stopping = []
    def _stop(*_): stopping.append(1)
    for s in (signal.SIGTERM, signal.SIGINT):
        try: signal.signal(s, _stop)
        except (ValueError, OSError): pass
    say(f"supervisor up pid {os.getpid()} python {sys.version.split()[0]}")
    while not stopping:
        time.sleep(2)
        for n, p in list(procs.items()):
            rc = p.poll()
            if rc is None: continue
            up = time.time() - started[n]
            if up > 300: backoff[n] = 5
            say(f"{n} pid {p.pid} exited rc={rc} after {int(up)}s; restart in {backoff[n]}s")
            time.sleep(backoff[n]); backoff[n] = min(60, backoff[n] * 2)
            procs[n] = start(n, job); started[n] = time.time()
    for p in procs.values():
        try: p.terminate()
        except OSError: pass
    say("supervisor stop")
    return 0

if __name__ == "__main__":
    sys.exit(main())
