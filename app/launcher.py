#!/usr/bin/env python3
"""Start, stop or check Paper Progress Tracker on this computer.

The start and stop files (start.command and stop.command on a Mac,
start-windows.bat and stop-windows.bat on Windows, start-linux.sh and
stop-linux.sh on Linux) find Python and run this file. It works the same way
on all three systems and needs only Python 3.9 or newer.

  python3 app/launcher.py start    build the page, start the server if it is not running, open the browser
  python3 app/launcher.py stop     stop the server
  python3 app/launcher.py status   say whether it is running

On first start it creates config.json from config.example.json. The server's
messages go to local/server.log. Set PD_NO_BROWSER=1 to start without opening
a browser.
"""
import hashlib, json, ntpath, os, re, signal, socket, subprocess, sys, time, urllib.request, webbrowser
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
LOCAL = ROOT / "local"  # this computer only, never committed: the server's process id and its log
# which copy of the tracker this is, worked out the same way as in server.py (INSTANCE)
INSTANCE = hashlib.sha256(os.path.normcase(str(ROOT)).encode("utf-8", "surrogatepass")).hexdigest()[:16]
PID_FILE = LOCAL / "server.pid"
LOG_FILE = LOCAL / "server.log"
OLD_LOG = LOCAL / "server.old.log"
CONFIG = ROOT / "config.json"
EXAMPLE = ROOT / "config.example.json"
DEFAULT_PORT = 47285
DEFAULT_CONFIG = {"names": [], "papersRoot": "~/Documents/Papers", "port": DEFAULT_PORT, "scanEveryHours": 6}
PYTHON_URL = "https://www.python.org/downloads/"
START_WAIT = 20  # seconds the server gets to answer
SYSTEM = sys.platform  # always read through this name, so the tests can act as another system
_isfile = os.path.isfile


def windows():
    return SYSTEM.startswith("win")


def stop_file():
    return "stop-windows.bat" if windows() else "stop.command" if SYSTEM == "darwin" else "stop-linux.sh"


def say(msg=""):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
    except OSError:
        pass


# ---- settings ------------------------------------------------------------------
def ensure_config():
    """Create config.json from config.example.json on first start, so nobody has to copy it by hand. Never replaces one."""
    if CONFIG.exists():
        return False
    try:
        data = EXAMPLE.read_bytes() if EXAMPLE.is_file() else (json.dumps(DEFAULT_CONFIG, indent=2) + "\n").encode("utf-8")
        with open(CONFIG, "xb") as f:
            f.write(data)
    except FileExistsError:
        return False
    except OSError as e:
        say(f"Could not create config.json ({e.strerror or e}). The default settings are used.")
        return False
    say("Created config.json with the default settings. Open it in a text editor to add your name and your papers folder.")
    return True


def read_config():
    """config.json as a dict. A mistake in it is explained, and the defaults are used until it is fixed."""
    try:
        text = CONFIG.read_text(encoding="utf-8-sig")  # Windows Notepad may add a BOM
    except FileNotFoundError:
        return {}
    except UnicodeDecodeError:
        say("config.json is not saved as UTF-8. Save it again as UTF-8 in your text editor. The default settings are used for now.")
        return {}
    except OSError as e:
        say(f"Could not read config.json ({e.strerror or e}). The default settings are used for now.")
        return {}
    try:
        cfg = json.loads(text)
    except ValueError as e:
        where = f" on line {e.lineno}" if getattr(e, "lineno", None) else ""
        say(f"config.json has a mistake{where}, so the default settings are used for now.")
        if "\\" in text:
            say('In folder paths use / instead of \\, for example "C:/Users/Jane/Documents/Papers".')
        return {}
    if not isinstance(cfg, dict):
        say("config.json should hold one { ... } block. The default settings are used for now.")
        return {}
    return cfg


def port_of(cfg):
    """PD_PORT if it is set, else "port" in config.json, else 47285."""
    for where, v in (("PD_PORT", os.environ.get("PD_PORT")), ("config.json", cfg.get("port"))):
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        n = None
        if isinstance(v, int) and not isinstance(v, bool):
            n = v
        elif isinstance(v, str) and v.strip().isdigit():
            n = int(v.strip())
        if n is not None and 1024 <= n <= 65535:
            return n
        say(f"The port in {where} ({v}) is not a whole number from 1024 to 65535, so it is not used.")
    return DEFAULT_PORT


# ---- is it running? --------------------------------------------------------------
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy, also on office computers


def ping_info(port, timeout=1.0):
    """What a Paper Progress Tracker on this port of this computer says about itself, or None when none answers."""
    try:
        with _DIRECT.open(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as r:
            d = json.loads(r.read(4096).decode("utf-8"))
            return d if r.status == 200 and isinstance(d, dict) and d.get("ok") is True else None
    except Exception:
        return None


def ping(port, timeout=1.0):
    """True when a Paper Progress Tracker answers on this port, this copy or another one."""
    return ping_info(port, timeout) is not None


def this_copy(info):
    """True when the tracker that answered is this copy (this folder). An older version or a copy in another
    folder answers too, but it is not this one."""
    return isinstance(info, dict) and info.get("instance") == INSTANCE


def port_taken(port):
    """True when this computer will not give the port to the server. Tested the way the server takes it, by
    binding to it, not by connecting: on Windows a connection to a free port is refused only after about 2
    seconds, so a short connection test would call a free port taken. A bind also fails for the port ranges
    Windows keeps for itself."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if windows():
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # as the server asks for it on Windows
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as the server does on Mac and Linux
        s.bind(("127.0.0.1", port))
    except OSError:
        return True
    finally:
        s.close()
    return False


def read_pid():
    try:
        d = json.loads(PID_FILE.read_text(encoding="utf-8"))
        pid = int(d.get("pid"))
        return {"pid": pid, "port": int(d.get("port") or 0)} if pid > 0 else None
    except Exception:
        return None


def write_pid(pid, port):
    LOCAL.mkdir(mode=0o700, exist_ok=True)  # yours only on Mac and Linux
    PID_FILE.write_text(json.dumps({"pid": pid, "port": port, "started": datetime.now().astimezone().isoformat(timespec="seconds")}), encoding="utf-8")


def clear_pid():
    try:
        PID_FILE.unlink()
    except OSError:
        pass


def run_tool(cmd, timeout=15):
    """Run a system tool and return what it printed, or None when it is missing or fails."""
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW: no console flashes up
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, **kw)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def alive(pid):
    if windows():
        return _win_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _win_alive(pid):
    """Windows: ask with OpenProcess. There os.kill(pid, 0) would send Ctrl+C instead of checking."""
    try:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # access denied means it exists
        try:
            code = ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:
        out = run_tool(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"])
        return bool(out) and f'"{int(pid)}"' in out


def command_line(pid):
    """(command, full): how the process was started. full is False when only the program name is known. (None, False) when unknown."""
    pid = int(pid)
    if windows():
        out = run_tool(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"], timeout=30)
        if out and out.strip():
            return out.strip(), True
        out = run_tool(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
        if out and f'"{pid}"' in out:
            return out.strip().splitlines()[0].split(",")[0].strip('"'), False  # for example pythonw.exe
        return None, False
    out = run_tool(["ps", "-ww", "-p", str(pid), "-o", "command="])
    return (out.strip(), True) if out and out.strip() else (None, False)


def is_our_server(pid):
    """True when the process runs app/server.py with Python, False when it is another program, None when the system cannot tell."""
    cmd, full = command_line(pid)
    if cmd is None:
        return None
    low = cmd.lower()
    if full:
        return "server.py" in low and "python" in low
    return None if low.startswith("python") else False


def parse_netstat(out, port):
    """Process ids listening on the port, from netstat -ano. The state word is translated on some Windows
    versions, so a listening line is one whose remote address is 0.0.0.0:0 or [::]:0."""
    pids = set()
    for line in (out or "").splitlines():
        f = line.split()
        if len(f) >= 4 and f[0].upper() == "TCP" and f[1].endswith(f":{port}") and f[2] in ("0.0.0.0:0", "[::]:0") and f[-1].isdigit():
            pids.add(int(f[-1]))
    return sorted(pids)


def parse_ss(out, port):
    """Process ids listening on the port, from ss -ltnp (Linux without lsof)."""
    pids = set()
    for line in (out or "").splitlines():
        f = line.split()
        if len(f) >= 5 and f[0] == "LISTEN" and f[3].endswith(f":{port}"):
            pids.update(int(x) for x in re.findall(r"pid=(\d+)", line))
    return sorted(pids)


def port_owner(port):
    """Process ids listening on this port, as far as the system's own tools tell."""
    if windows():
        return parse_netstat(run_tool(["netstat", "-ano", "-p", "TCP"]), port)
    out = run_tool(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
    if out:
        return sorted({int(x) for x in out.split() if x.isdigit()})
    return parse_ss(run_tool(["ss", "-ltnp"]), port)


def terminate(pid):
    """Stop one process and wait until it is gone. True when it is gone."""
    try:
        os.kill(pid, signal.SIGTERM)  # on Windows this ends the process (TerminateProcess)
    except OSError:
        pass
    for _ in range(50):
        if not alive(pid):
            return True
        time.sleep(0.1)
    if not windows():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.3)
    return not alive(pid)


# ---- start ----------------------------------------------------------------------
def server_command():
    """Python and the server file. On Windows pythonw.exe, so no console window stays open."""
    exe = sys.executable or ("python" if windows() else "python3")
    if windows():
        w = ntpath.join(ntpath.dirname(exe), "pythonw.exe")
        if _isfile(w):
            exe = w
    return [exe, str(APP / "server.py")]


def spawn_options(cmd):
    """Start the server so it keeps running after this window closes."""
    if windows():
        flags = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        if not ntpath.basename(cmd[0]).lower().startswith("pythonw"):
            flags |= 0x08000000  # CREATE_NO_WINDOW: python.exe runs without a console window
        return {"creationflags": flags}
    return {"start_new_session": True}  # its own session: closing the Terminal window does not stop it


def log_tail(n=8):
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [x for x in lines if x.strip()][-n:]


def start_server(port):
    LOCAL.mkdir(mode=0o700, exist_ok=True)  # yours only on Mac and Linux
    try:
        if LOG_FILE.exists():
            os.replace(LOG_FILE, OLD_LOG)  # keep the messages of the last run
    except OSError:
        pass
    cmd = server_command()
    env = dict(os.environ, PD_PORT=str(port), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    try:
        with open(LOG_FILE, "ab") as log:
            proc = subprocess.Popen(cmd, cwd=str(APP), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **spawn_options(cmd))
    except (OSError, ValueError) as e:
        say(f"Could not start the server ({e}).")
        return False
    end = time.time() + START_WAIT
    while time.time() < end:
        if ping(port, 0.5):
            if proc.poll() is None:  # this server answers, not one started a moment earlier by a second click
                write_pid(proc.pid, port)
            return True
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    if proc.poll() is None:
        write_pid(proc.pid, port)
        say(f"The server is slow to start. Wait a minute, then double-click the start file again. Its messages are in {LOG_FILE}.")
        return False
    say("The server stopped right away. Its last messages:")
    for line in log_tail():
        say("  " + line)
    return False


def open_browser(url):
    if os.environ.get("PD_NO_BROWSER"):
        return
    if SYSTEM.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        say(f"Open {url} in your browser.")  # no desktop here: a text browser would take over this terminal
        return
    if SYSTEM == "darwin" and run_tool(["open", url]) is not None:  # the Mac's own way, your default browser
        return
    try:
        if webbrowser.open(url):  # Windows: your default browser. Linux: xdg-open and friends
            return
    except Exception:
        pass
    say(f"Open {url} in your browser.")


def build_page():
    """app/index.html from app/source.html. When that fails, the page built last time is used."""
    try:
        if str(APP) not in sys.path:
            sys.path.insert(0, str(APP))
        import build_local
        build_local.build()
        return True
    except Exception as e:
        if (APP / "index.html").is_file():
            say(f"Could not rebuild the page ({e}). The page built last time is used.")
            return True
        say(f"Could not build the page ({e}). Check that app/source.html is there.")
        return False


def other_port(port):
    return port + 1 if port < 65535 else 48000


def say_other_copy(port):
    say(f"Another copy of Paper Progress Tracker already runs on port {port}. It is an older version or a copy in another folder.")
    say("The start and stop files work only with the copy in their own folder. Stop the other copy with the stop file in its folder,")
    say(f'or choose another port in config.json, for example "port": {other_port(port)}, and start again.')


def name_missing(cfg):
    names = cfg.get("names")
    return not names or names == ["Jane Doe"]


def start():
    created = ensure_config()
    cfg = read_config()
    port = port_of(cfg)
    url = f"http://127.0.0.1:{port}/"
    if not build_page():
        return 1
    info = ping_info(port)
    if this_copy(info):
        say(f"Paper Progress Tracker is already running at {url}")
    elif info:
        say_other_copy(port)
        return 1
    else:
        if port_taken(port):
            say(f"Another program already uses port {port}.")
            say(f'Choose another port in config.json, for example "port": {other_port(port)}, and start again.')
            return 1
        say("Starting Paper Progress Tracker ...")
        if not start_server(port):
            return 1
        say(f"Paper Progress Tracker is running at {url}")
    if not created and name_missing(cfg):
        say('Tip. Add your own name to "names" in config.json, so the tracker can find you in author lines.')
    open_browser(url)
    say(f"It keeps running in the background, also when you close this window. To stop it, double-click {stop_file()}.")
    return 0


def stop():
    port = port_of(read_config())
    rec, info = read_pid(), ping_info(port)
    mine = this_copy(info)
    pids = []
    if rec and alive(rec["pid"]):
        ours = is_our_server(rec["pid"])
        # a process id can be reused by another program after a restart, so an unknown one must still answer as this copy
        if ours or (ours is None and this_copy(ping_info(rec["port"] or port))):
            pids.append(rec["pid"])
    if mine:
        for pid in port_owner(port):
            # this copy answers on this port, so a Python program that cannot be checked further is it
            if pid not in pids and is_our_server(pid) is not False:
                pids.append(pid)
    if not pids:
        clear_pid()
        if mine:
            say(f"Paper Progress Tracker answers on port {port}, but its program could not be found.")
            say("Close it in Activity Monitor (Mac), Task Manager (Windows) or System Monitor (Linux), where it shows as Python.")
            return 1
        if info:  # never stop another copy, for example an older version or a second download in another folder
            say(f"This copy of Paper Progress Tracker is not running. Another copy runs on port {port}, from another folder or an older version.")
            say("To stop that one, use the stop file in its own folder.")
            return 1
        say("Paper Progress Tracker is not running.")
        return 0
    for pid in pids:
        terminate(pid)
    clear_pid()
    if not this_copy(ping_info(port, 0.5)):  # what counts: this copy no longer answers
        say("Paper Progress Tracker stopped. Your data is saved.")
        return 0
    say("Could not stop Paper Progress Tracker. Restarting the computer stops it.")
    return 1


def status():
    port = port_of(read_config())
    info = ping_info(port)
    if this_copy(info):
        say(f"Paper Progress Tracker is running at http://127.0.0.1:{port}/")
        return 0
    if info:
        say(f"This copy of Paper Progress Tracker is not running. Another copy runs on port {port}, from another folder or an older version.")
        return 3
    say("Paper Progress Tracker is not running.")
    return 3


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if sys.version_info < (3, 9):
        say(f"Paper Progress Tracker needs Python 3.9 or newer. This is Python {sys.version.split()[0]}.")
        say(f"Get a newer Python from {PYTHON_URL}")
        return 1
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    action = (argv[0] if argv else "start").lower().lstrip("-")
    actions = {"start": start, "stop": stop, "status": status}
    if action not in actions:
        say("Use: launcher.py start, launcher.py stop or launcher.py status")
        return 2
    try:
        return actions[action]()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
