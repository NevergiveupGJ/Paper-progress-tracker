#!/usr/bin/env python3
"""Paper Progress Tracker, local server.

Serves the tracker page on http://127.0.0.1:47285 (the port is set in config.json) and keeps
your data as JSON files in ../data/<collection>/<id>.json, so it works with no internet.
Only this computer can reach it (it listens on 127.0.0.1 only). It runs on Windows, macOS
and Linux with Python 3.9 or newer and nothing else.

On start it
  - creates config.json from config.example.json when it is missing,
  - builds app/index.html from app/source.html when that is missing or older,
  - copies the example papers into ../data on the very first start,
  - keeps one backup of the data folder per day in ../backups (zip, checked every hour),
  - reads your paper folders with scan_folders.py (read only), at start and then every
    scanEveryHours hours (config.json, 0 means only at start).

Nothing here deletes a file. A "delete" moves the record into ../data/_trash with a time stamp.
On Mac and Linux the data, backups and local folders can be opened by your user account only.

The AI helper is optional and off by default. Its settings, and an API key if you add one,
are kept in ai.json in a settings folder of your user account, outside the tracker's folder
(see key_dir), so a tracker folder kept in OneDrive, iCloud Drive or Dropbox never uploads
the key. The file can be read only by you where the system supports it. The key goes only to
the AI service you chose. It is never sent to the page and never written to the log, and
neither is what you ask.
"""
import hashlib
import http.client
import importlib
import json
import os
import re
import runpy
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
DATA = ROOT / "data"
BACKUPS = ROOT / "backups"
LOCAL = ROOT / "local"  # this computer only (git ignores it): the launcher's log and process id, working copies
SCAN_TMP = LOCAL / "tmp"  # working copies for the folder read
CONFIG_FILE = ROOT / "config.json"
CONFIG_EXAMPLE = ROOT / "config.example.json"
HOST = "127.0.0.1"
DEFAULT_PORT = 47285
PORT = DEFAULT_PORT  # set from config.json (or the PD_PORT variable) in main()
CONFIG_DEFAULTS = {"names": [], "papersRoot": "~/Documents/Papers", "port": DEFAULT_PORT, "scanEveryHours": 6,
                   "journalListName": "", "onlineUrl": ""}
PUBLIC_CONFIG = ("names", "papersRoot", "journalListName", "onlineUrl", "scanEveryHours")  # what the page may read

COLLECTIONS = {"papers", "scan", "context", "plan", "focus", "config", "journals", "venues", "timelog", "week", "cfp"}
ID_RE = re.compile(r"^(?!\.)[A-Za-z0-9_\-.~:@+]{1,200}\Z")  # no leading dot (.tmp-* files are not records). \Z, since $ also allows a final line break
WIN_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?\Z", re.I)  # names Windows keeps for devices
MAX_BODY = 300 * 1024
DOC_MAX = 256 * 1024  # the same limit as the online version of the page, so a record can move between the two
LOCK = threading.Lock()
CFG_LOCK = threading.Lock()
SCAN_LOCK = threading.Lock()
SCAN_AGAIN = threading.Event()  # a read was asked for after the last one started
SCAN_TICK = 600  # seconds between checks whether the next folder read is due
PICK_TIMEOUT = 3600  # a folder window left open this long counts as cancelled
TIMED_OUT = -2
HALF = re.compile("[\ud800-\udfff]")  # half of an emoji, left when text is cut at a fixed length
# folders that macOS shows as one file (apps, libraries, documents): never a paper's folder
PKG_EXT = (".app", ".bundle", ".framework", ".plugin", ".kext", ".appex", ".xpc", ".prefpane", ".pkg", ".mpkg", ".photoslibrary", ".photolibrary",
           ".musiclibrary", ".tvlibrary", ".imovielibrary", ".fcpbundle", ".logicx", ".band", ".rtfd", ".pages", ".numbers", ".key",
           ".xcodeproj", ".xcworkspace", ".playground", ".scptd")
QUOTE_PAIRS = ('""', "''", "\u201c\u201d", "\u2018\u2019")
# a local link opens a folder, or a file of one of these kinds (documents, sheets, slides, pictures, sound, video, data).
# Any other file, or one with no ending, could be a program, a script, a shortcut or an installer, so it opens from the file
# manager only. Left out on purpose: Office files with macros (.docm .xlsm .pptm .dot) and R data (.rds .rdata), which can
# run code when opened
LINK_EXT = frozenset((
    ".docx", ".doc", ".dotx", ".odt", ".rtf", ".txt", ".text", ".md", ".markdown", ".tex", ".bib", ".ris", ".enw",
    ".nbib", ".pdf", ".epub", ".pages", ".scrivx", ".xlsx", ".xls", ".csv", ".tsv", ".ods", ".numbers", ".pptx",
    ".ppt", ".ppsx", ".odp", ".key", ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".heic", ".bmp", ".webp",
    ".mp3", ".m4a", ".wav", ".aac", ".mp4", ".mov", ".m4v", ".avi", ".sav", ".spv", ".dta", ".nvp",
    ".nvpx", ".mat", ".ipynb", ".rmd", ".qmd", ".json", ".xml", ".eml", ".msg", ".log",
    ".edf", ".bdf", ".set", ".fdt", ".vhdr", ".vmrk", ".eeg", ".cnt", ".evt"))  # EEG data files open in their own program
DOC_PKG = (".pages", ".numbers", ".key", ".rtfd", ".scriv")  # folders a Mac shows as one document, opened in their own app
# a refused open, by detail: (what the page is told, the words in the log, which never names the file)
REFUSED = {
    "outside": ("Only files and folders in your user folder, your papers folder or this paper's folders open from here.", "outside your folders"),
    "kind": ("Only documents and folders open from here, so no program starts by accident. Copy the path and open it yourself.",
             "not a document or a folder"),
    "alias": ("This is an alias. Link the original file instead.", "an alias"),
    "open_with": ("This file is set to open in a special way. Open it in Finder.", "set to open in another app"),
    "unchecked": ("This file could not be checked. Open it in Finder.", "its settings could not be read"),
}
LINK_HOST = re.compile(r"^[a-z0-9][a-z0-9._-]{0,252}\Z")  # the computer in a link to a shared folder (\\server\share)
WIN_OBJECT = re.compile(r"\{[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\}\Z", re.I)  # a folder name Windows opens as a system object

# optional AI helper
AI_PROVIDERS = ("none", "anthropic", "openai", "ollama")
AI_DEFAULTS = {
    "anthropic": {"model": "claude-sonnet-5-5", "baseUrl": "https://api.anthropic.com/v1"},
    "openai": {"model": "", "baseUrl": "https://api.openai.com/v1"},  # any service that speaks the OpenAI chat format
    # Ollama on this computer, no key. 127.0.0.1, not localhost: Windows tries ::1 first, where Ollama does not
    # listen, and waits about 2 seconds for the refusal on every request
    "ollama": {"model": "", "baseUrl": "http://127.0.0.1:11434/v1"},
}
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# the models that take fallbacks "default" (on Anthropic's own address only). Other models are asked without it
ANTHROPIC_FALLBACK_MODELS = ("claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
AI_TIMEOUT = 60
AI_MAX_PROMPT = 100000  # characters of system text and prompt together
AI_BODY_MAX = 512 * 1024
AI_TOKENS = 4096
AI_MAX_TOKENS = 16000
AI_REPLY_MAX = 4 * 1024 * 1024
AI_LOCK = threading.Lock()
AI_BUSY = threading.BoundedSemaphore(2)  # at most two AI requests at a time, so a stuck page can not run up a bill
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}\Z")
KEY_RE = re.compile(r"^[\x21-\x7e]{8,512}\Z")  # printable, no spaces or line breaks
KEYLIKE = re.compile(r"\b(?:sk|pk|rk|ak|key)[-_][A-Za-z0-9_\-*.]{4,}|[A-Za-z0-9_\-]{32,}", re.I)
TOO_LONG = re.compile(r"too long|too large|context.length|context window|maximum context|too many tokens", re.I)
THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.S)  # some local models write their reasoning first
CERT_HELP = ("Python could not check the secure connection. On a Mac with Python from python.org, "
             "run Install Certificates.command in the Python folder, then try again.")


def plat():
    """"windows", "mac" or "linux" (any other Unix counts as linux)."""
    if os.name == "nt":
        return "windows"
    return "mac" if sys.platform == "darwin" else "linux"


def key_dir():
    """The folder for the AI settings and key: a settings folder of your user account, outside the tracker's
    folder. These places are not synced by OneDrive or iCloud Drive, and a Windows user's AppData can be read
    by that user (and administrators) only."""
    p = plat()
    if p == "windows":
        base = os.environ.get("LOCALAPPDATA") or ""
        return (Path(base) if os.path.isabs(base) else Path.home() / "AppData" / "Local") / "PaperProgressTracker"
    if p == "mac":
        return Path.home() / "Library" / "Application Support" / "PaperProgressTracker"
    base = os.environ.get("XDG_CONFIG_HOME") or ""
    return (Path(base) if os.path.isabs(base) else Path.home() / ".config") / "paper-progress-tracker"


AI_DIR = key_dir()
AI_FILE = AI_DIR / "ai.json"
# which copy of the tracker this is, so the launcher of one copy never reports, opens or stops another copy
# (an older version, or a second download) that runs on the same port
INSTANCE = hashlib.sha256(os.path.normcase(str(ROOT)).encode("utf-8", "surrogatepass")).hexdigest()[:16]


def local_now():
    return datetime.now().astimezone()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log(msg):
    """One short line in the server's log. Never file contents, keys or what was asked of the AI."""
    print(local_now().strftime("%Y-%m-%d %H:%M:%S"), msg, file=sys.stderr, flush=True)


def short_err(s, n=160):
    """The line of a system error that says what went wrong, short and plain, for the log and the page."""
    lines = [x.strip() for x in (s or "").splitlines() if x.strip()]
    s = next((x for x in reversed(lines) if "error" in x.lower()), lines[-1] if lines else "")
    s = re.sub(r"^\d+:\d+: |execution error: | for the (file|URL) .*", "", " ".join(s.split())).replace(";", ",").replace("\u2014", "-")
    return s[:n - 3] + "..." if len(s) > n else s


def which(name):
    return shutil.which(name)


def run_tool(cmd, timeout=None, env=None):
    """Run a system tool and wait for it: (return code, output, error text).
    Code -1 when it could not start, TIMED_OUT when it gave no answer in time."""
    kw = {}
    if plat() == "windows" and getattr(subprocess, "CREATE_NO_WINDOW", 0):
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # no console window flashes up
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env, **kw)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return TIMED_OUT, "", f"no answer after {timeout} seconds"
    except (OSError, ValueError) as e:
        return -1, "", str(e)


def launch(cmd, wait=5):
    """Start a program that may keep running (xdg-open on some desktops): (return code, error text).
    Still running after `wait` seconds counts as started. Its error text goes to a temp file, not a pipe,
    so a file manager it starts can not hold this request open."""
    with tempfile.TemporaryFile() as err:
        try:
            pr = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)
        except (OSError, ValueError) as e:
            return -1, str(e)
        try:
            rc = pr.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            threading.Thread(target=pr.wait, daemon=True).start()  # collect it when it ends
            return 0, ""
        err.seek(0)
        return rc, err.read(4096).decode("utf-8", "replace")


# ---------- settings (config.json) ----------

def ensure_config():
    """First start: create config.json from config.example.json, so nobody has to copy it by hand."""
    if CONFIG_FILE.exists():
        return
    try:
        if CONFIG_EXAMPLE.is_file():
            text, src = CONFIG_EXAMPLE.read_text(encoding="utf-8"), "from config.example.json"
        else:
            text, src = json.dumps(CONFIG_DEFAULTS, indent=2) + "\n", "with default settings"
        with open(CONFIG_FILE, "x", encoding="utf-8", newline="\n") as f:  # "x": never over a file made meanwhile
            f.write(text)
        log(f"created config.json {src}")
    except FileExistsError:
        pass
    except OSError as e:
        log(f"could not create config.json: {e}")


def read_config():
    """config.json with every field checked. A missing or broken field gets its default."""
    out = dict(CONFIG_DEFAULTS)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))  # -sig: Notepad may add a byte order mark
    except FileNotFoundError:
        return out
    except (OSError, ValueError) as e:
        log(f"config.json could not be read, so the defaults are used: {short_err(str(e))}")
        return out
    if not isinstance(raw, dict):
        return out
    names = raw.get("names")
    if isinstance(names, str):
        names = [names]
    if isinstance(names, list):
        out["names"] = [n.strip() for n in names if isinstance(n, str) and n.strip()][:20]
    if isinstance(raw.get("papersRoot"), str):
        out["papersRoot"] = raw["papersRoot"].strip()
    port = raw.get("port")
    if isinstance(port, str) and port.strip().isdigit():
        port = int(port.strip())
    if isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535:
        out["port"] = port
    h = raw.get("scanEveryHours")
    if isinstance(h, (int, float)) and not isinstance(h, bool) and 0 <= h <= 24 * 366:
        out["scanEveryHours"] = h
    if isinstance(raw.get("journalListName"), str):
        out["journalListName"] = raw["journalListName"][:200]
    u = raw.get("onlineUrl")
    if isinstance(u, str) and re.match(r"^https?://[^\s\"'<>]+\Z", u.strip(), re.I):  # a web address only, the page shows it as a link
        out["onlineUrl"] = u.strip()
    return out


_CFG = {"mtime": None, "data": None}


def config_mtime():
    try:
        return CONFIG_FILE.stat().st_mtime_ns
    except OSError:
        return None


def get_config():
    """The settings, read again when config.json changed, so an edit counts without a restart (except the port)."""
    m = config_mtime()
    with CFG_LOCK:
        if _CFG["data"] is None or _CFG["mtime"] != m:
            _CFG["data"], _CFG["mtime"] = read_config(), m
        return dict(_CFG["data"])


def choose_port(cfg):
    env = os.environ.get("PD_PORT", "").strip()
    if env.isdigit() and 1024 <= int(env) <= 65535:
        return int(env)
    return cfg["port"]


def build_page():
    """Build app/index.html from app/source.html when it is missing or older, so starting the server is enough."""
    src, out, builder = APP / "source.html", APP / "index.html", APP / "build_local.py"
    try:
        if src.is_file() and builder.is_file() and (not out.is_file() or src.stat().st_mtime > out.stat().st_mtime):
            runpy.run_path(str(builder), run_name="__main__")
    except Exception as e:
        log(f"could not build index.html: {e}")


# ---------- paths, on every system ----------

def path_key(p, pm=None):
    """A path spelled the way the system compares it: normalised, and in lower case on Windows."""
    pm = pm or os.path
    return pm.normcase(pm.normpath(p))


def under(f, d, pm=None):
    """True when path f is folder d or lies inside it (by spelling, letter case ignored on Windows)."""
    pm = pm or os.path
    if not f or not d:
        return False
    s = pm.sep
    return (path_key(f, pm).rstrip(s) + s).startswith(path_key(d, pm).rstrip(s) + s)


def same_path(f, d, pm=None):
    """True when both spell the same path ("C/" is "C", and on Windows "c:\\A" is "C:\\a")."""
    return bool(f) and bool(d) and path_key(f, pm) == path_key(d, pm)


def same_dir(a, b):
    """True when both paths name one existing folder, whatever the letter case or symlinks."""
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def clean_pasted(raw, win=None, pm=None):
    """The pasted folder path, tidied but not yet checked on disk: ("", path) or ("invalid_argument", None).
    Spaces and quotes around it, ~ and a trailing slash are fine. On Windows also C:/ with forward
    slashes, %USERPROFILE% and \\\\server\\share paths."""
    win = plat() == "windows" if win is None else win
    pm = pm or os.path
    if not isinstance(raw, str):
        return "invalid_argument", None
    p = raw.strip()
    for a, b in QUOTE_PAIRS:
        if len(p) >= 2 and p[0] == a and p[-1] == b:
            p = p[1:-1].strip()
            break
    if not p or "\0" in p or len(p) > 4096:
        return "invalid_argument", None
    if win and "%" in p:
        p = pm.expandvars(p)
    p = pm.expanduser(p)
    if win:
        if not pm.splitdrive(p)[0] or not pm.isabs(p):  # "C:Papers" and "\Papers" depend on the current drive and folder
            return "invalid_argument", None
        return "", pm.normpath(p)
    if not pm.isabs(p):
        return "invalid_argument", None
    return "", "/" + pm.normpath(p).lstrip("/")  # normpath keeps a leading "//", which is the same folder as "/"


def chain_to(path, bases, pm=None, stat=None):
    """The folders from path up to the first of bases that holds it (path first, the base left out), or None
    when none of bases holds it. A base is not inside itself. Compared by file identity where the system has
    one, so other letter case or a symlinked spelling still counts, and by spelling otherwise."""
    pm = pm or os.path
    stat = stat or os.stat
    marks = []
    for b in bases:
        try:
            st = stat(b)
            st = st if st.st_ino else None  # some drives report no file number, then only the spelling counts
        except (OSError, ValueError):
            st = None
        marks.append((path_key(b, pm), st))

    def is_base(d):
        if any(k == path_key(d, pm) for k, _ in marks):
            return True
        if not any(s for _, s in marks):
            return False
        try:
            ds = stat(d)
        except (OSError, ValueError):
            return False
        return any(s is not None and ds.st_ino == s.st_ino and ds.st_dev == s.st_dev for _, s in marks)

    chain, d = [path], pm.dirname(path)
    while True:
        if is_base(d):
            return chain
        up = pm.dirname(d)
        if up == d:
            return None
        chain.append(d)
        d = up


def allowed_bases():
    """Where a pasted folder may lie: inside your home folder, or inside papersRoot from config.json
    (for papers on another drive). Never a whole drive."""
    out = [os.path.realpath(str(Path.home()))]
    pr = get_config().get("papersRoot")
    if pr:
        r = os.path.realpath(os.path.expanduser(pr))
        if os.path.isdir(r) and os.path.dirname(r) != r:
            out.append(r)
    return out


def is_bundle(chain):
    """True when a folder in chain is something macOS shows as one file (an app, a library, a Pages file)."""
    return any(os.path.basename(x).lower().endswith(PKG_EXT) for x in chain) or \
        any(os.path.exists(os.path.join(x, "Contents", n)) for x in chain for n in ("Info.plist", "PkgInfo"))


def pasted_folder(raw):
    """The folder path pasted on the page: ("", path) when it is a real folder inside your home folder (or
    inside papersRoot), else (code, None). Resolved through symlinks before that check."""
    code, p = clean_pasted(raw)
    if code:
        return code, None
    if plat() != "windows" and not os.path.isdir(p) and "\\" in p:
        q = os.path.normpath(re.sub(r"\\(.)", r"\1", p))  # copied from a terminal, where a space is written "\ "
        if os.path.isdir(q):
            p = q
    if not os.path.isdir(p):
        return "not_found", None
    try:
        chain = chain_to(os.path.realpath(p), allowed_bases())
    except (OSError, ValueError):
        return "invalid_argument", None
    if chain is None or (plat() == "mac" and is_bundle(chain)):
        return "invalid_argument", None
    return "", p


# ---------- links to files and folders on this computer ----------

def link_key(url):
    """A local link as the page saves it (file:///C:/..., file://server/share/... or file:///home/..., each name
    %-encoded), as (computer, drive, names). None when it is not such a link. The names are decoded and otherwise
    kept as they are, so one key names exactly one path."""
    if not isinstance(url, str) or len(url) > 16384 or url[:7].lower() != "file://":
        return None
    rest = url[7:]
    i = rest.find("/")
    host, rest = (rest.lower(), "") if i < 0 else (rest[:i].lower(), rest[i:])
    host = "" if host == "localhost" else host
    if (host and not LINK_HOST.match(host)) or "?" in rest or "#" in rest:
        return None
    try:
        names = [urllib.parse.unquote(s, errors="strict") for s in rest.split("/") if s]
    except UnicodeDecodeError:
        return None
    drive = names.pop(0).upper() if not host and names and re.match(r"^[A-Za-z]:\Z", names[0]) else ""
    if not names or any(s in (".", "..") or re.search(r"[\x00-\x1f\x7f/\\]", s) for s in names):
        return None  # never a whole drive, and no name that climbs up or hides a separator
    return host, drive, tuple(names)


def link_path(key, win=None):
    """The path on this computer for a link key: ("", path), ("other_computer", None) for a path of another system
    (a Windows path on a Mac or Linux, a Mac or Linux path on Windows), or ("invalid_argument", None)."""
    win = plat() == "windows" if win is None else win
    host, drive, names = key
    if not win:
        return ("other_computer", None) if host or drive else ("", "/" + "/".join(names))
    if not host and not drive:
        return "other_computer", None
    # a ":" opens a hidden part of a file, Windows drops a final dot or space (so "x.exe." is x.exe), CON or NUL is a
    # device, and a name that ends in {...} can be a system object. None of them is a paper's file
    if any(":" in s or s[-1] in ". " or WIN_RESERVED.match(s) or WIN_OBJECT.search(s) for s in names):
        return "invalid_argument", None
    return "", ("\\\\" + host + "\\" if host else drive + "\\") + "\\".join(names)


# ---------- a Mac file that would open some other way than its name says ----------

def mac_libc():
    """This Mac's C library through ctypes, with getxattr and listxattr set up. Python has no os.getxattr on macOS."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.getxattr.restype = libc.listxattr.restype = ctypes.c_ssize_t
    libc.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    libc.listxattr.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    return ctypes, libc


def finder_flags(path):
    """The Finder flags of a file on a Mac (0 when it has none), from libc, else from /usr/bin/xattr."""
    info = b""
    try:
        ctypes, libc = mac_libc()
        buf = ctypes.create_string_buffer(64)
        n = libc.getxattr(os.fsencode(path), b"com.apple.FinderInfo", buf, 64, 0, 0)
        info = buf.raw[:n] if n > 0 else b""
    except Exception:
        rc, out, _ = run_tool(["/usr/bin/xattr", "-px", "com.apple.FinderInfo", path], timeout=10)
        try:
            info = bytes.fromhex(out) if rc == 0 else b""
        except ValueError:
            info = b""
    return info[8] if len(info) > 8 else 0


def is_alias(real):
    """True for a Finder alias on a Mac. It is a plain file, so resolving symlinks does not follow it, yet opening it opens
    what it points to: an app, a script or a folder anywhere. Two signs: the bookmark header, or the alias flag in the Finder info."""
    if not os.path.isfile(real):  # an alias is a file. A folder, a pipe or a device is not read here
        return False
    try:
        st = os.stat(real)
        # a cloud file not downloaded yet (OneDrive, iCloud) is not read when it is bigger than any alias (a few KB):
        # reading it would download all of it first. Its Finder flags are still checked below
        if not (getattr(st, "st_flags", 0) & 0x40000000 and st.st_size > 1 << 20):  # SF_DATALESS
            with open(real, "rb") as f:
                head = f.read(16)
                if head[:4] == b"book" and head[8:12] == b"mark":  # bookmark header, whatever the 4 bytes between
                    return True
    except OSError:
        pass
    return bool(finder_flags(real) & 0x80)  # kIsAlias


def xattr_names(path):
    """The names of the extended attributes of path itself on a Mac (a symlink is not followed), or None when they could
    not be read. From libc, else from /usr/bin/xattr."""
    try:
        ctypes, libc = mac_libc()
        p = os.fsencode(path)
        for _ in range(3):  # the list can grow between asking its size and reading it
            n = libc.listxattr(p, None, 0, 1)  # 1: XATTR_NOFOLLOW
            if n <= 0:
                return [] if n == 0 else None
            buf = ctypes.create_string_buffer(n)
            m = libc.listxattr(p, buf, n, 1)
            if m >= 0:
                return [x.decode("utf-8", "replace") for x in buf.raw[:m].split(b"\0") if x]
        return None
    except Exception:
        rc, out, _ = run_tool(["/usr/bin/xattr", "-s", path], timeout=10)
        return out.splitlines() if rc == 0 else None


def open_with(path):
    """"open_with" when path carries its own Open with app on a Mac (Get Info in Finder keeps it in
    com.apple.LaunchServices.OpenWith): then even a .docx, .pdf or .csv opens in that app, which can be Terminal, Script
    Editor or Installer. "unchecked" when its extended attributes could not be read, so nothing opens unchecked. Else ""."""
    names = xattr_names(path)
    if names is None:
        return "unchecked"
    # also in other letter case or with flags after a "#": macOS ignores those names, but they are refused all the same
    return "open_with" if any(n.split("#")[0].lower().startswith("com.apple.launchservices") for n in names) else ""


def link_target(paper, full, url):
    """What a click on one of the paper's local links opens: ("", "", path, "folder" or "file"), or (code, detail,
    None, None). The link must be saved on this paper. It must name a folder, or a file of a kind in LINK_EXT, inside
    your home folder, inside papersRoot or inside one of the paper's folders (full), with symlinks resolved, as for a
    pasted folder. On a Mac never an app, nothing inside an app, no Finder alias, and nothing with an Open with app
    of its own, on the link or on what it points to."""
    key = link_key(url)
    saved = paper.get("links") if isinstance(paper.get("links"), list) else []
    if key is None or key not in [link_key(x.get("url")) for x in saved if isinstance(x, dict)]:
        return "invalid_argument", "not_saved", None, None
    code, path = link_path(key)
    if code:
        return "invalid_argument", "other_computer" if code == "other_computer" else "", None, None
    real = os.path.realpath(path)
    if not os.path.exists(real):
        return "not_found", "", None, None
    own = [r for r in (os.path.realpath(f) for f in full if f) if os.path.isdir(r) and os.path.dirname(r) != r]
    try:
        chain = chain_to(real, allowed_bases() + own)
    except (OSError, ValueError):
        chain = None
    if chain is None and any(same_dir(real, r) for r in own):
        chain = [real]  # a folder this paper follows, also on another drive
    if chain is None:
        return "refused", "outside", None, None
    mac = plat() == "mac"
    if mac and is_bundle(chain[1:]):
        return "refused", "kind", None, None
    if mac and (is_alias(path) or is_alias(real)):
        return "refused", "alias", None, None
    if os.path.isdir(real):
        app = any(os.path.lexists(os.path.join(real, "Contents", n)) for n in ("Info.plist", "PkgInfo", "MacOS"))
        doc = mac and not app and real.lower().endswith(DOC_PKG)
        if mac and not doc and (app or is_bundle([real])):
            return "refused", "kind", None, None
        kind = "file" if doc else "folder"
    elif not os.path.isfile(real) or any(os.path.splitext(n)[1].lower() not in LINK_EXT for n in (os.path.basename(path), os.path.basename(real))):
        return "refused", "kind", None, None  # also a file with no ending, which a Mac opens by its x bit or Finder type code
    else:
        kind = "file"
    why = open_with(path) or open_with(real) if mac else ""  # the link itself (a symlink is checked as it is) and what opens
    if why:
        return "refused", why, None, None
    return "", "", real, kind


def new_folder_list(old, chosen, pm=None):
    """The paper's folder list with chosen as its main folder. Your other folders stay. An exclude ("-" and a
    path) counts for every folder in the scan, so one on or above the new folder is dropped, and so are the old
    main folder's excludes unless they lie inside the new one."""
    main = next((f for f in old if isinstance(f, str) and f.strip() and not f.startswith("-")), None)

    def drop(f):
        if not isinstance(f, str):
            return False
        if not f.startswith("-"):
            return same_path(f, main, pm) or same_path(f, chosen, pm)
        x = f[1:]
        return under(chosen, x, pm) or (under(x, main, pm) and not under(x, chosen, pm))
    return [chosen] + [f for f in old if not drop(f)]


def full_folder(f, pm=None):
    """A folder as stored on a paper, as a full and tidy path the system can open ("~" expanded as the folder
    reading does, and on Windows C:/ turned into C:\\), or None when it is not a full path."""
    pm = pm or os.path
    if not isinstance(f, str) or not f.strip() or f.startswith("-"):
        return None
    p = pm.expanduser(f.strip())
    if not pm.isabs(p) or (pm.sep == "\\" and not pm.splitdrive(p)[0]):  # Windows "\Papers" depends on the current drive
        return None
    return pm.normpath(p)


def default_start():
    """Where the folder window opens when the paper has no folder yet: papersRoot, else the home folder."""
    pr = os.path.expanduser(get_config().get("papersRoot") or "")
    return pr if pr and os.path.isdir(pr) else str(Path.home())


# ---------- the system's folder window and file manager ----------

# Windows: the standard folder window through PowerShell. The script never changes, the start folder and the
# title come in through environment variables, so no folder name can break it. Exit code 3 means cancelled.
WIN_PICKER = "; ".join([
    "$ErrorActionPreference = 'Stop'",
    "Add-Type -AssemblyName System.Windows.Forms",
    "[System.Windows.Forms.Application]::EnableVisualStyles()",
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog",
    "$d.Description = $env:PPT_PICK_TITLE",
    "$d.ShowNewFolderButton = $true",
    "if ($env:PPT_PICK_START -and (Test-Path -LiteralPath $env:PPT_PICK_START -PathType Container)) { $d.SelectedPath = $env:PPT_PICK_START }",
    "$o = New-Object System.Windows.Forms.Form",
    "$o.TopMost = $true",
    "$o.ShowInTaskbar = $false",
    "$r = $d.ShowDialog($o)",
    "$o.Dispose()",
    "if ($r -ne [System.Windows.Forms.DialogResult]::OK) { exit 3 }",
    "$b = [System.Text.Encoding]::UTF8.GetBytes($d.SelectedPath)",
    "$s = [Console]::OpenStandardOutput()",
    "$s.Write($b, 0, $b.Length)",
    "$s.Flush()",
    "exit 0",
])


def powershell():
    exe = which("powershell.exe") or which("powershell") or which("pwsh.exe") or which("pwsh")
    if not exe:
        p = os.path.join(os.environ.get("SystemRoot") or "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        exe = p if os.path.isfile(p) else None
    return exe


def picker_ways(start, title):
    """The ways to show this system's folder window, tried in order: [(name, command, environment, cancelled test)].
    Empty when there is none (no desktop, or no tool for it). Then the page offers to paste the path."""
    title = ("Choose the folder for " + title)[:120]
    p = plat()
    if p == "mac":
        # First the window osascript shows itself, which needs no permission to control Finder, then the older way through Finder
        s = start.replace("\\", "\\\\").replace('"', '\\"')  # AppleScript string, so a " in a folder name can not end it
        t = title.replace('"', "'").replace("\\", "")
        pick = f'set f to choose folder with prompt "{t}" default location (POSIX file "{s}")'
        cancelled = lambda rc, err: rc != 0 and "(-128)" in err  # noqa: E731
        ways = [("picker", ["activate", pick, "return POSIX path of f"]),
                ("Finder picker", ['tell application "Finder"', "activate", pick, "end tell", "return POSIX path of f"])]
        return [(n, ["osascript"] + sum([["-e", x] for x in script], []), None, cancelled) for n, script in ways]
    if p == "windows":
        exe = powershell()
        if not exe:
            return []
        env = dict(os.environ, PPT_PICK_START=start, PPT_PICK_TITLE=title)
        return [("folder window", [exe, "-NoProfile", "-NonInteractive", "-STA", "-Command", WIN_PICKER], env,
                 lambda rc, err: rc == 3)]
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return []  # no desktop, for example over ssh
    z, k = which("zenity"), which("kdialog")
    ways = []
    if z:
        ways.append(("zenity", [z, "--file-selection", "--directory", "--title=" + title, "--filename=" + start.rstrip("/") + "/"],
                     None, lambda rc, err: rc == 1))
    if k:
        ways.append(("kdialog", [k, "--title", title, "--getexistingdirectory", start], None, lambda rc, err: rc == 1))
    if "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        ways.reverse()
    return ways


def open_target(target, what):
    """Show a folder in the file manager, or open a file in its own app: (True, "") or (False, why)."""
    p = plat()
    if p == "windows":
        start = getattr(os, "startfile", None)
        if start is None:
            return False, "Windows could not open it."
        try:
            start(target)  # Explorer for a folder, the app set for .docx files for the Word file
            return True, ""
        except OSError as e:
            return False, short_err(getattr(e, "strerror", None) or str(e)) or "Windows could not open it."
    if p == "mac":
        # a folder goes to Finder by name, a file to its own app
        rc, _, err = run_tool(["open", "-a", "Finder", target] if what == "folder" else ["open", target], timeout=30)
        return (True, "") if rc == 0 else (False, short_err(err) or f"open stopped with code {rc}")
    exe = which("xdg-open")
    cmd = [exe, target] if exe else None
    if not cmd and which("gio"):
        cmd = [which("gio"), "open", target]
    if not cmd:
        return False, "No program to open files was found. Install xdg-utils."
    rc, err = launch(cmd)
    return (True, "") if rc == 0 else (False, short_err(err) or f"{os.path.basename(cmd[0])} stopped with code {rc}")


# ---------- the data store ----------

def doc_path(col, doc_id):
    if col not in COLLECTIONS or not isinstance(doc_id, str) or not ID_RE.match(doc_id):
        return None
    if plat() == "windows" and (":" in doc_id or WIN_RESERVED.match(doc_id)):
        return None  # a ":" would write into a hidden part of another file, and CON, NUL and the like are devices
    return DATA / col / f"{doc_id}.json"


def read_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def replace_file(tmp, p):
    """os.replace, tried again for a moment on Windows, where a virus scanner or backup tool may hold the file."""
    for i in range(10):
        try:
            return os.replace(tmp, p)
        except PermissionError:
            if plat() != "windows" or i == 9:
                raise
            time.sleep(0.1)


def write_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="backslashreplace") as f:  # a half emoji from another file is kept as \udXXX
            json.dump(obj, f, ensure_ascii=False, indent=1)
        replace_file(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def too_big(d):
    return len(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "backslashreplace")) > DOC_MAX


def merge(base, patch):
    """Same rule as the online store: nested objects merge, anything else replaces."""
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def daily_backup():
    if not DATA.is_dir():
        return
    BACKUPS.mkdir(parents=True, exist_ok=True)
    private_dir(BACKUPS)  # the zips hold the same records as data/
    target = BACKUPS / f"data_{local_now().strftime('%Y%m%d')}.zip"
    if target.exists():
        return
    fd, tmp = tempfile.mkstemp(dir=str(BACKUPS), prefix=".tmp-", suffix=".zip")
    os.close(fd)
    try:  # zip under a temp name, so a half-written zip never counts as the day's backup
        with LOCK, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in DATA.rglob("*.json"):
                if "_trash" not in f.parts and not f.name.startswith("."):
                    z.write(f, f.relative_to(DATA).as_posix())
        replace_file(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup_loop():
    """The server can run for days, so check every hour for the day's backup."""
    while True:
        try:
            daily_backup()
        except Exception as e:
            log(f"backup failed: {e}")
        time.sleep(3600)


# ---------- reading your folders ----------

_SCAN = {"mod": None, "cfg": None}


def scan_module():
    """scan_folders.py, loaded again after config.json changed (it reads your names and papersRoot when it loads)."""
    m = config_mtime()
    if _SCAN["mod"] is None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import scan_folders  # noqa
        _SCAN["mod"] = scan_folders
    elif _SCAN["cfg"] != m:
        _SCAN["mod"] = importlib.reload(_SCAN["mod"])
    _SCAN["cfg"] = m
    return _SCAN["mod"]


def read_folders():
    """Run the read-only folder scan against the local papers and store the result.
    One read at a time, so an older read can not finish last and overwrite a newer one.
    Requests that wait while a read runs share one new read, so typing in the folders box does not queue many."""
    SCAN_AGAIN.set()
    with SCAN_LOCK:
        if not SCAN_AGAIN.is_set():  # a read that started after this request already covers it
            return
        SCAN_AGAIN.clear()
        _read_folders()


def clear_scan_tmp():
    """Remove what a read left behind when the server was stopped in the middle of it."""
    if SCAN_TMP.is_dir():
        for d in SCAN_TMP.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
            else:
                try:
                    d.unlink()
                except OSError:
                    pass


def _read_folders():
    out = state = None
    try:
        scan = scan_module()
        # the working copies stay inside the tracker's own folder (local/tmp), never in the system's temp folder
        clear_scan_tmp()
        SCAN_TMP.mkdir(parents=True, exist_ok=True)
        private_dir(LOCAL)
        out = Path(tempfile.mkdtemp(prefix="scan-", dir=str(SCAN_TMP)))
        state = Path(tempfile.mkdtemp(prefix="state-", dir=str(SCAN_TMP)))
        for c in ("papers", "scan"):
            if (DATA / c).is_dir():
                shutil.copytree(DATA / c, state / c, ignore=shutil.ignore_patterns(".*"))
        scan.main(str(state), str(out))
        stamp = now_iso()
        with LOCK:
            for f in out.glob("*.json"):
                d = read_json(f)
                if isinstance(d, dict):
                    d["updatedAt"] = stamp
                    write_json(DATA / "scan" / f.name, d)
    except Exception as e:  # the tracker still works without a fresh scan
        log(f"folder read failed: {e}")
    finally:  # also after a failed read, so no copy of your papers stays behind
        for t in (out, state):
            if t:
                shutil.rmtree(t, ignore_errors=True)


def scan_loop():
    """Read the folders at start, then again every scanEveryHours hours (checked every few minutes, so a
    changed setting counts without a restart, and the time asleep on a laptop counts too)."""
    read_folders()
    last = time.time()
    while True:
        time.sleep(SCAN_TICK)
        h = get_config()["scanEveryHours"]
        if h > 0 and time.time() - last >= h * 3600:
            read_folders()
            last = time.time()


def set_main_folder(pid, paper, chosen):
    """Make chosen the paper's main folder (from the folder window or a pasted path), keep its other folders,
    and read the folders again."""
    with LOCK:
        cur = read_json(doc_path("papers", pid)) or paper
        old = cur.get("folders") if isinstance(cur.get("folders"), list) else []
        cur["folders"] = new_folder_list(old, chosen)
        cur["updatedAt"] = now_iso()
        write_json(doc_path("papers", pid), cur)
    threading.Thread(target=read_folders, daemon=True).start()
    return cur["folders"]


# ---------- the optional AI helper ----------

def private_dir(p):
    """Make a folder openable by you only (mode 700) on Mac and Linux, so other accounts on a shared computer
    can not read it. On Windows a folder inside your user folder is already yours only."""
    if plat() != "windows":
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass


def private_file(p):
    """Make a file readable by you only (mode 600). Windows has no such mode: there the file keeps the
    access rights of the folder it is in, which for a folder in your user folder means you only."""
    if plat() != "windows":
        try:
            if os.stat(p).st_mode & 0o077:
                os.chmod(p, 0o600)
        except OSError:
            pass


def load_ai():
    """The AI settings from ai.json in key_dir(), or the default (off) when there are none."""
    cfg = {"provider": "none", "providers": {}}
    try:
        d = json.loads(AI_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    private_file(AI_FILE)
    if not isinstance(d, dict):
        return cfg
    if d.get("provider") in AI_PROVIDERS:
        cfg["provider"] = d["provider"]
    ps = d.get("providers") if isinstance(d.get("providers"), dict) else {}
    for name in AI_DEFAULTS:
        v = ps.get(name)
        if isinstance(v, dict):
            cfg["providers"][name] = {f: v[f] for f in ("model", "baseUrl", "apiKey", "keyOrigin") if isinstance(v.get(f), str)}
    return cfg


def save_ai(cfg):
    AI_DIR.mkdir(parents=True, exist_ok=True)
    private_dir(AI_DIR)
    fd, tmp = tempfile.mkstemp(dir=str(AI_DIR), prefix=".tmp-", suffix=".json")  # made readable by you only (mode 600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        private_file(tmp)
        replace_file(tmp, AI_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    private_file(AI_FILE)


def clean_base_url(u):
    """The AI service address, tidied ("https://host[:port]/path", no trailing slash), or None when it is not one."""
    if not isinstance(u, str):
        return None
    u = u.strip()
    if not u or len(u) > 500 or any(c.isspace() or ord(c) < 32 for c in u):
        return None
    try:
        p = urllib.parse.urlsplit(u)
        p.port  # noqa: B018, a port that is not a number raises ValueError
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment:
        return None
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}"


def url_origin(u):
    try:
        p = urllib.parse.urlsplit(u)
        return f"{p.scheme.lower()}://{(p.hostname or '').lower()}:{p.port or (443 if p.scheme.lower() == 'https' else 80)}"
    except ValueError:
        return ""


def is_local_url(u):
    try:
        h = (urllib.parse.urlsplit(u).hostname or "").lower()
    except ValueError:
        return False
    return h in ("localhost", "::1") or h.startswith("127.") or h.endswith(".localhost")


def ai_settings(cfg):
    """Provider, model, address and key in use, defaults filled in, or None when the helper is off.
    A key saved for another address is left out, so a key never goes to a service it was not meant for."""
    prov = cfg.get("provider")
    if prov not in AI_DEFAULTS:
        return None
    ps = cfg["providers"].get(prov, {})
    base = ps.get("baseUrl") or AI_DEFAULTS[prov]["baseUrl"]
    key = ps.get("apiKey") or ""
    if key and ps.get("keyOrigin") != url_origin(base):
        key = ""
    return {"provider": prov, "model": ps.get("model") or AI_DEFAULTS[prov]["model"], "baseUrl": base, "apiKey": key}


def ai_problem(s):
    """Why the AI helper can not be used now, in one plain sentence, or "" when it is ready."""
    if not s:
        return "The AI helper is off. You can switch it on in the AI settings."
    if not s["model"]:
        return "Add a model name in the AI settings."
    needs_key = s["provider"] == "anthropic" or (s["provider"] == "openai" and not is_local_url(s["baseUrl"]))
    if needs_key and not s["apiKey"]:
        return "Add an API key in the AI settings."
    if s["apiKey"] and s["baseUrl"].startswith("http://") and not is_local_url(s["baseUrl"]):
        return "Use an https address. A key is never sent without encryption."
    return ""


def ai_view(cfg):
    """What the page may know about the AI settings. Never the key, only its last 4 characters."""
    s = ai_settings(cfg)
    if not s:
        return {"enabled": False, "provider": "none", "model": "", "baseUrl": "", "hasKey": False, "keyHint": ""}
    k = s["apiKey"]
    return {"enabled": not ai_problem(s), "provider": s["provider"], "model": s["model"], "baseUrl": s["baseUrl"],
            "hasKey": bool(k), "keyHint": k[-4:] if len(k) >= 12 else ""}


def ai_configure(body):
    """Save the AI settings sent by the page: (view, "") or (None, why).
    No apiKey keeps the saved key, "" removes it. A new address drops the old key, so it is not sent there."""
    prov = body.get("provider")
    if prov not in AI_PROVIDERS:
        return None, "Choose none, anthropic, openai or ollama."
    for f in ("model", "baseUrl", "apiKey"):
        if f in body and not isinstance(body[f], str):
            return None, f"{f} must be text."
    with AI_LOCK:
        cfg = load_ai()
        if prov == "none":
            if body.get("apiKey") == "":  # off, and forget every saved key
                for ps in cfg["providers"].values():
                    ps.pop("apiKey", None)
                    ps.pop("keyOrigin", None)
        else:
            ps = dict(cfg["providers"].get(prov, {}))
            if "model" in body:
                m = body["model"].strip()
                if m and not MODEL_RE.match(m):
                    return None, "That model name has characters a model name can not have."
                ps["model"] = m
            if "baseUrl" in body:
                u = body["baseUrl"].strip()
                if u:
                    u = clean_base_url(u)
                    if not u:
                        return None, "The address must start with https:// or http://."
                ps["baseUrl"] = u
            base = ps.get("baseUrl") or AI_DEFAULTS[prov]["baseUrl"]
            if "apiKey" in body:
                k = body["apiKey"].strip()
                if not k:
                    ps.pop("apiKey", None)
                    ps.pop("keyOrigin", None)
                elif not KEY_RE.match(k):
                    return None, "That does not look like an API key."
                else:
                    ps["apiKey"], ps["keyOrigin"] = k, url_origin(base)
            elif ps.get("apiKey") and ps.get("keyOrigin") != url_origin(base):
                ps.pop("apiKey", None)
                ps.pop("keyOrigin", None)
            if ps.get("apiKey") and base.startswith("http://") and not is_local_url(base):
                return None, "Use an https address. A key is never sent without encryption."
            cfg["providers"][prov] = ps
        cfg["provider"] = prov
        save_ai(cfg)
    return ai_view(cfg), ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the key to another address, so it is reported, never followed."""

    def redirect_request(self, *a, **k):
        return None


def ai_http(method, url, headers, payload=None):
    """One request to the AI service: (HTTP status, JSON answer or None, network problem or "").
    Status 0 when the service could not be reached."""
    handlers = [_NoRedirect()]
    if is_local_url(url):
        handlers.append(urllib.request.ProxyHandler({}))  # a proxy must not stand between you and a service on this computer
    opener = urllib.request.build_opener(*handlers)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with opener.open(req, timeout=AI_TIMEOUT) as r:
            status, raw = r.status, r.read(AI_REPLY_MAX + 1)
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            raw = e.read(65536) or b""
        except Exception:
            raw = b""
        finally:
            e.close()
    except urllib.error.URLError as e:
        r = e.reason
        if isinstance(r, (socket.timeout, TimeoutError)):
            return 0, None, "timeout"
        if isinstance(r, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(r):
            return 0, None, "certificate"
        return 0, None, "unreachable"
    except (socket.timeout, TimeoutError):
        return 0, None, "timeout"
    except ssl.SSLCertVerificationError:
        return 0, None, "certificate"
    except (OSError, http.client.HTTPException, ValueError):
        return 0, None, "unreachable"
    if len(raw) > AI_REPLY_MAX:
        return status, None, ""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except ValueError:
        obj = None
    return status, obj, ""


def scrub(s, key=""):
    """A message from the AI service made safe to show: no key or anything like one, short, plain punctuation."""
    s = s if isinstance(s, str) else ""
    if key:
        s = s.replace(key, "[key]")
    s = KEYLIKE.sub("[key]", " ".join(s.split())).replace(";", ",").replace("\u2014", "-").replace("\u2013", "-")
    return s[:197] + "..." if len(s) > 200 else s


def service_error(obj):
    """(message, code) from an error answer of Anthropic, OpenAI or Ollama."""
    if not isinstance(obj, dict):
        return "", ""
    e = obj.get("error")
    if isinstance(e, dict):
        code = e.get("code") or e.get("type") or ""
        return (e.get("message") if isinstance(e.get("message"), str) else ""), (code if isinstance(code, str) else "")
    if isinstance(e, str):
        return e, ""
    return (obj.get("message") if isinstance(obj.get("message"), str) else ""), ""


def ai_failure(s, status, obj, net):
    """(code, plain sentence) for a request that did not work."""
    if status == 0:
        if net == "timeout":
            return "network", f"The AI service gave no answer within {AI_TIMEOUT} seconds. Try again."
        if net == "certificate":
            return "network", CERT_HELP
        if s["provider"] == "ollama":
            return "network", f"Could not reach Ollama at {s['baseUrl']}. Start Ollama and try again."
        return "network", "Could not reach the AI service. Check the internet connection and the address in the AI settings."
    raw, code = service_error(obj)
    msg = scrub(raw, s["apiKey"])
    if status == 401:
        return "auth", "The AI service did not accept the API key. Check the key in the AI settings."
    if status == 403:
        return "auth", "The API key is not allowed to do this. Check the key and the model name."
    if status in (429, 529):
        if code == "insufficient_quota" or "quota" in raw.lower() or "credit" in raw.lower():
            return "rate_limit", "The AI account has no credit or quota left."
        return "rate_limit", "The AI service is busy or your usage limit is reached. Try again in a minute."
    if status == 413 or code == "context_length_exceeded" or TOO_LONG.search(raw):
        return "too_large", "The text is too long for this model. Try a shorter one."
    if status >= 500:
        return "network", f"The AI service had a problem (HTTP {status}). Try again later."
    if status == 404:
        return "bad_response", f"The AI service does not know model {s['model']}, or the address is wrong. Check both in the AI settings."
    if 300 <= status < 400:
        return "bad_response", "The address sends requests somewhere else. Check the address in the AI settings."
    if status == 200:
        return "bad_response", "The AI service sent an answer the tracker could not read."
    return "bad_response", f"The AI service refused the request (HTTP {status}). {msg}".strip()


def anthropic_text(obj):
    blocks = obj.get("content") if isinstance(obj, dict) else None
    if not isinstance(blocks, list):
        return None, "The AI service sent an answer the tracker could not read."
    text = "".join(b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)).strip()
    if text:
        return text, ""
    stop = obj.get("stop_reason")
    if stop == "refusal":
        return None, "The AI declined to answer this request."
    if stop == "max_tokens":
        return None, "The answer was cut off before it began. Allow more tokens."
    return None, "The AI sent an empty answer."


def openai_text(obj):
    ch = obj.get("choices") if isinstance(obj, dict) else None
    c0 = ch[0] if isinstance(ch, list) and ch and isinstance(ch[0], dict) else None
    msg = c0.get("message") if c0 else None
    if not isinstance(msg, dict):
        return None, "The AI service sent an answer the tracker could not read."
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    text = THINK.sub("", content).strip() if isinstance(content, str) else ""
    if text:
        return text, ""
    if msg.get("refusal") or c0.get("finish_reason") == "content_filter":
        return None, "The AI declined to answer this request."
    if c0.get("finish_reason") == "length":
        return None, "The answer was cut off before it began. Allow more tokens."
    return None, "The AI sent an empty answer."


def openai_headers(s):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if s["apiKey"]:
        h["Authorization"] = "Bearer " + s["apiKey"]
    return h


def anthropic_headers(s):
    return {"content-type": "application/json", "accept": "application/json", "anthropic-version": ANTHROPIC_VERSION, "x-api-key": s["apiKey"]}


def ai_request(s, system, prompt, max_tokens):
    """Ask the chosen AI service once: (text, "", "", status) or (None, code, detail, status)."""
    if s["provider"] == "anthropic":
        payload = {"model": s["model"], "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
        if system:
            payload["system"] = system
        # On Anthropic's own service, a request the model declines is tried again on the model Anthropic picks for it.
        # Only the newest models take this option. If the service still refuses the request (HTTP 400, which is not
        # billed), ask once more without the option, so the answer or the error is the plain request's own.
        fallback = urllib.parse.urlsplit(s["baseUrl"]).hostname == "api.anthropic.com" and s["model"] in ANTHROPIC_FALLBACK_MODELS
        for attempt in (1, 2):
            headers, body = anthropic_headers(s), dict(payload)
            if fallback:
                headers["anthropic-beta"] = ANTHROPIC_FALLBACK_BETA
                body["fallbacks"] = "default"
            status, obj, net = ai_http("POST", s["baseUrl"] + "/messages", headers, body)
            if attempt == 1 and fallback and status == 400:
                fallback = False
                continue
            break
        read = anthropic_text
    else:
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        # OpenAI's newer models take max_completion_tokens, most other services only max_tokens
        field = "max_completion_tokens" if urllib.parse.urlsplit(s["baseUrl"]).hostname == "api.openai.com" else "max_tokens"
        for attempt in (1, 2):
            payload = {"model": s["model"], "messages": msgs, field: max_tokens, "stream": False}
            status, obj, net = ai_http("POST", s["baseUrl"] + "/chat/completions", openai_headers(s), payload)
            other = "max_tokens" if field != "max_tokens" else "max_completion_tokens"
            m = service_error(obj)[0]
            if attempt == 1 and status == 400 and field in m and other in m:
                field = other  # the service asked for the other name, so ask once more with it
                continue
            break
        read = openai_text
    if status != 200 or obj is None:
        code, detail = ai_failure(s, status, obj, net)
        return None, code, detail, status
    text, why = read(obj)
    if text is None:
        return None, "bad_response", why, status
    return text[:AI_REPLY_MAX], "", "", status


def model_ids(obj):
    data = obj.get("data") if isinstance(obj, dict) else None
    if not isinstance(data, list):
        data = obj.get("models") if isinstance(obj, dict) and isinstance(obj.get("models"), list) else []
    return [m.get("id") or m.get("name") for m in data if isinstance(m, dict) and isinstance(m.get("id") or m.get("name"), str)]


def ai_test():
    """Check the AI settings with as little as possible ({ok, detail}). Anthropic and OpenAI: the model list,
    which costs nothing. Ollama: the models on this computer."""
    s = ai_settings(load_ai())
    if not s:
        return {"ok": False, "detail": "The AI helper is off."}, "off", 0
    prov, model = s["provider"], s["model"]
    if prov == "ollama":
        status, obj, net = ai_http("GET", s["baseUrl"] + "/models", openai_headers(s))
        if status != 200:
            return {"ok": False, "detail": ai_failure(s, status, obj, net)[1]}, "failed", status
        ids = model_ids(obj)
        have = ", ".join(ids[:8]) + (" and more" if len(ids) > 8 else "")
        if not ids:
            return {"ok": False, "detail": "Ollama is running but has no models yet. Get one with the command ollama pull and a model name."}, "no_models", status
        if not model:
            return {"ok": False, "detail": f"Ollama is running. Type one of its models as the model name. It has {have}."}, "no_model", status
        if model in ids or model + ":latest" in ids:
            return {"ok": True, "detail": f"Ollama is running and has {model}."}, "ok", status
        return {"ok": False, "detail": f"Ollama is running, but it does not have {model}. It has {have}."}, "missing_model", status
    problem = ai_problem(s)
    if problem:
        return {"ok": False, "detail": problem}, "not_ready", 0
    if prov == "anthropic":
        url = s["baseUrl"] + "/models/" + urllib.parse.quote(model, safe="")
        status, obj, net = ai_http("GET", url, anthropic_headers(s))
        if status == 200:
            return {"ok": True, "detail": f"Connected. The key works and {model} is available."}, "ok", status
        if status == 404:
            return {"ok": False, "detail": f"The service answered, but it does not know model {model}. Check the model name."}, "missing_model", status
        return {"ok": False, "detail": ai_failure(s, status, obj, net)[1]}, "failed", status
    status, obj, net = ai_http("GET", s["baseUrl"] + "/models", openai_headers(s))
    ids = model_ids(obj) if status == 200 else []
    if status == 200 and model in ids:
        return {"ok": True, "detail": f"Connected. The key works and {model} is available."}, "ok", status
    if status == 200 and ids:
        return {"ok": False, "detail": f"The key works, but {model} is not in this service's model list. Check the model name."}, "missing_model", status
    if status in (200, 404, 405):  # no usable model list here: ask one tiny question instead
        text, code, detail, status = ai_request(s, "", "Reply with the word OK.", 256)
        if text is not None:
            return {"ok": True, "detail": f"Connected. {model} answered."}, "ok", status
        return {"ok": False, "detail": detail}, code, status
    return {"ok": False, "detail": ai_failure(s, status, obj, net)[1]}, "failed", status


# ---------- the web server ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "PaperProgressTracker/2"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8", "backslashreplace")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        # no other website may show the tracker inside its own page (where a hidden frame could catch your clicks),
        # the browser takes each answer as the type it says it is, and links you open never carry the tracker's address
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def host_ok(self):
        """Only this computer's own names. A website that points its name at 127.0.0.1 (DNS rebinding) is refused."""
        return (self.headers.get("Host") or "").lower() in (f"{HOST}:{PORT}", f"localhost:{PORT}")

    def origin_ok(self):
        """The page's own fetch() always sends Origin. Other websites send theirs, so they can not post here."""
        return self.headers.get("Origin") in (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}")

    def length(self):
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return -1

    def body(self, limit):
        """The JSON object sent with a POST, or None when it is missing, too long or not an object."""
        try:
            n = self.length()
            if n > limit:
                self.drain(n)
                return None
            if n <= 0:
                return None
            b = json.loads(self.rfile.read(n).decode("utf-8"))
            if isinstance(b, dict):  # half an emoji becomes the replacement sign the page already shows
                b = json.loads(HALF.sub("\ufffd", json.dumps(b, ensure_ascii=False, allow_nan=False)))  # NaN, Infinity or 1e400 would break JSON.parse in the page
        except Exception:
            return None
        return b if isinstance(b, dict) else None

    def drain(self, n, cap=8 * 1024 * 1024):
        """Read and drop a body that is too long, so the page still gets the answer instead of a broken connection."""
        if n > cap:
            self.close_connection = True
            return
        while n > 0:
            chunk = self.rfile.read(min(n, 65536))
            if not chunk:
                break
            n -= len(chunk)

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, {"code": "forbidden"})
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if not parts or parts == ["index.html"]:
            page = APP / "index.html"
            if not page.is_file():
                return self.send(500, b"<p>The page is missing. Run app/build_local.py with Python, then reload.</p>", "text/html; charset=utf-8")
            return self.send(200, page.read_bytes(), "text/html; charset=utf-8")
        if parts == ["logo.png"] and (APP / "logo.png").exists():
            return self.send(200, (APP / "logo.png").read_bytes(), "image/png")
        if parts == ["api", "ping"]:
            return self.send(200, {"ok": True, "instance": INSTANCE})
        if parts == ["api", "config"]:
            cfg = get_config()
            return self.send(200, {k: cfg[k] for k in PUBLIC_CONFIG})
        if parts == ["api", "ai"]:
            return self.send(200, ai_view(load_ai()))
        if len(parts) == 3 and parts[:2] == ["api", "c"]:
            col = parts[2]
            if col not in COLLECTIONS:
                return self.send(400, {"code": "invalid_argument"})
            docs = []
            folder = DATA / col
            if folder.is_dir():
                for f in sorted(folder.glob("*.json")):
                    if f.name.startswith("."):  # a temp file left by a write that was cut off
                        continue
                    d = read_json(f)
                    if isinstance(d, dict):
                        docs.append({"id": f.stem, "data": d})
            return self.send(200, {"docs": docs})
        if len(parts) == 4 and parts[:2] == ["api", "d"]:
            p = doc_path(parts[2], _unq(parts[3]))
            if not p:
                return self.send(400, {"code": "invalid_argument"})
            d = read_json(p) if p.exists() else None
            return self.send(200, {"exists": isinstance(d, dict), "data": d if isinstance(d, dict) else None})
        return self.send(404, {"code": "not_found"})

    def do_POST(self):
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if not self.host_ok() or not self.origin_ok():
            return self.send(403, {"code": "forbidden"})
        if len(parts) == 2 and parts[0] == "api" and parts[1] in ("open", "choose-folder", "set-folder"):
            return self.finder(parts[1])
        if len(parts) == 3 and parts[:2] == ["api", "ai"] and parts[2] in ("config", "test", "ask"):
            return self.ai(parts[2])
        if len(parts) != 4 or parts[:2] != ["api", "d"]:
            return self.send(404, {"code": "not_found"})
        p = doc_path(parts[2], _unq(parts[3]))
        body = self.body(MAX_BODY)
        if not p or body is None:
            return self.send(400, {"code": "invalid_argument"})
        op, data = body.get("op"), body.get("data")
        with LOCK:
            cur = read_json(p) if p.exists() else None
            if op == "set" and isinstance(data, dict):
                if too_big(data):
                    return self.send(400, {"code": "invalid_argument"})
                write_json(p, data)
            elif op == "update" and isinstance(data, dict):
                new = merge(cur, data) if isinstance(cur, dict) else None
                if new is None or too_big(new):
                    return self.send(400, {"code": "invalid_argument"})
                write_json(p, new)
            elif op == "delete":
                if p.exists():
                    trash = DATA / "_trash" / p.parent.name
                    trash.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(trash / f"{p.stem}_{local_now().strftime('%Y%m%d-%H%M%S')}.json"))
            else:
                return self.send(400, {"code": "invalid_argument"})
        # the paper's folders changed (Undo after Choose folder, Remove folder, the folders box): read them again, so the
        # tracker does not keep showing a manuscript, author line or suggestions from a folder the paper no longer has
        if parts[2] == "papers" and op in ("set", "update") and "folders" in data and data["folders"] != (cur.get("folders") if isinstance(cur, dict) else None):
            threading.Thread(target=read_folders, daemon=True).start()
        return self.send(200, {"ok": True})

    def finder(self, action):
        """Open a paper's folder, its latest Word file or one of its local links, let you pick a new folder, or set
        the folder path you pasted. Only paths that belong to one of your papers can be opened. A failure is logged
        and sent back, never silent."""
        body = self.body(32768) or {}  # room for a long link, where each letter beyond A to Z takes up to 9 characters
        pid = str(body.get("id") or "")
        dp = doc_path("papers", pid)
        paper = read_json(dp) if dp and dp.exists() else None
        if not isinstance(paper, dict):
            return self.send(400, {"ok": False, "code": "invalid_argument"})
        allf = paper.get("folders") if isinstance(paper.get("folders"), list) else []
        folders = [f for f in allf if isinstance(f, str) and f.strip() and not f.startswith("-")]
        full = [full_folder(f) for f in folders]  # the same folders as full paths, None where one is not a full path
        if action == "open" and body.get("what") == "link":
            code, detail, target, kind = link_target(paper, full, body.get("url"))
            if code == "refused":
                return self.refuse(f"open link {pid}", detail)
            if code:
                return self.send(404 if code == "not_found" else 400, dict({"ok": False, "code": code}, **({"detail": detail} if detail else {})))
            if os.environ.get("PD_DRYRUN"):
                return self.send(200, {"ok": True, "opened": target})
            ok, why = open_target(target, kind)
            if not ok:
                log(f"open link {pid}: the system could not open it")
                return self.send(200, {"ok": False, "code": "failed", "detail": why})
            return self.send(200, {"ok": True, "opened": target})
        if action == "open":
            what = body.get("what")
            target = None
            if what == "folder" and full and full[0]:
                t = os.path.realpath(full[0])
                # a folder only: on a Mac "open" would launch an app bundle
                if os.path.isdir(t) and not (plat() == "mac" and is_bundle([t])):
                    target = full[0]
            elif what == "latest":
                sc = read_json(DATA / "scan" / f"{pid}.json")
                m = sc.get("manuscript") if isinstance(sc, dict) else None
                m = m.get("path") if isinstance(m, dict) else None
                t = os.path.realpath(m) if isinstance(m, str) and m else ""
                # only a Word file that really lies inside one of the paper's folders (symlinks resolved)
                if t.lower().endswith(".docx") and os.path.isfile(t) and \
                        any(f and under(t, os.path.realpath(f)) for f in full):
                    target = os.path.normpath(m)
            if not target or not os.path.exists(target):
                return self.send(404, {"ok": False, "code": "not_found"})
            # on a Mac the Word file, as for a link: no alias and no Open with app of its own. A folder goes to Finder by
            # name, which ignores both
            why = what == "latest" and plat() == "mac" and ("alias" if is_alias(target) or is_alias(t) else open_with(target) or open_with(t))
            if why:
                return self.refuse(f"open latest {pid}", why)
            if os.environ.get("PD_DRYRUN"):
                return self.send(200, {"ok": True, "opened": target})
            ok, detail = open_target(target, what)
            if not ok:
                log(f"open {what} {pid}: {detail}")
                return self.send(200, {"ok": False, "code": "failed", "detail": detail})
            return self.send(200, {"ok": True, "opened": target})
        if action == "set-folder":
            code, chosen = pasted_folder(body.get("path"))
            if code:
                return self.send(404 if code == "not_found" else 400, {"ok": False, "code": code})
            # one of the paper's folders typed in other letter case (or through a symlink) is still that folder: keep
            # its spelling, so the excludes inside it stay and it is not listed twice
            chosen = next((os.path.normpath(f) for f, p in zip(folders, full) if p and same_dir(p, chosen)), chosen)
            return self.send(200, {"ok": True, "folders": set_main_folder(pid, paper, chosen)})
        # choose-folder: the system's own folder window, starting at the current folder
        start = full[0] if full and full[0] and os.path.isdir(full[0]) else default_start()
        ways = picker_ways(start, str(paper.get("title") or pid))
        if os.environ.get("PD_DRYRUN"):
            return self.send(200, {"ok": True, "dryrun": [w[1] for w in ways]})
        if not ways:
            log(f"choose-folder {pid}: this computer has no folder window, the page offers to paste the path")
            return self.send(200, {"ok": False, "code": "blocked", "detail": "This computer has no folder window the tracker can open. Paste the folder path instead."})
        detail = ""
        for name, cmd, env, cancelled in ways:
            rc, out, err = run_tool(cmd, timeout=PICK_TIMEOUT, env=env)
            if rc == TIMED_OUT or cancelled(rc, err):  # you pressed Cancel: no second window
                return self.send(200, {"ok": False, "code": "cancelled"})
            chosen = os.path.normpath(out.strip()) if out.strip() else ""
            if rc == 0 and chosen and os.path.isabs(chosen) and os.path.isdir(chosen):
                return self.send(200, {"ok": True, "folders": set_main_folder(pid, paper, chosen)})
            detail = "The chosen folder could not be read." if rc == 0 else short_err(err) or f"{name} stopped with code {rc}"
            log(f"choose-folder {pid}: the {name} failed, code {rc}: {detail}")
            if rc == 0:  # a folder was picked, so no second window: the page offers to paste the path
                break
        log(f"choose-folder {pid}: no folder window worked, the page offers to paste the path")
        return self.send(200, {"ok": False, "code": "blocked", "detail": detail})

    def refuse(self, what, detail):
        """A refused open: one short line in the log (never the path) and a plain message for the page."""
        log(f"{what}: refused, {REFUSED[detail][1]}")
        return self.send(403, {"ok": False, "code": "refused", "detail": detail, "message": REFUSED[detail][0]})

    def ai(self, action):
        """The optional AI helper: settings, a connection test, and one question at a time."""
        if action == "ask" and self.length() > AI_BODY_MAX:
            self.drain(self.length())
            return self.send(200, {"ok": False, "code": "too_large", "detail": f"The text is too long for the AI helper. The limit is {AI_MAX_PROMPT:,} characters."})
        body = self.body(AI_BODY_MAX if action == "ask" else 8192)
        if action == "test":
            with_slot = AI_BUSY.acquire(blocking=False)
            if not with_slot:
                return self.send(200, {"ok": False, "detail": "Another AI request is still running. Try again when it is done."})
            try:
                res, what, status = ai_test()
            except Exception:
                res, what, status = {"ok": False, "detail": "The test stopped with an error."}, "error", 0
            finally:
                AI_BUSY.release()
            log(f"ai test {load_ai().get('provider')}: {what}" + (f", HTTP {status}" if status else ""))
            return self.send(200, res)
        if body is None:
            return self.send(400, {"ok": False, "code": "invalid_argument"})
        if action == "config":
            view, why = ai_configure(body)
            if not view:
                return self.send(400, {"ok": False, "code": "invalid_argument", "detail": why})
            log(f"ai settings saved: provider {view['provider']}")
            return self.send(200, view)
        # ask
        prompt, system, mt = body.get("prompt"), body.get("system"), body.get("maxTokens")
        if not isinstance(prompt, str) or not prompt.strip() or not (system is None or isinstance(system, str)):
            return self.send(400, {"ok": False, "code": "invalid_argument"})
        if mt is None:
            mt = AI_TOKENS
        elif isinstance(mt, bool) or not isinstance(mt, (int, float)) or mt != mt or mt < 1:
            return self.send(400, {"ok": False, "code": "invalid_argument"})
        mt = int(min(mt, AI_MAX_TOKENS))
        if len(prompt) + len(system or "") > AI_MAX_PROMPT:
            return self.send(200, {"ok": False, "code": "too_large", "detail": f"The text is too long for the AI helper. The limit is {AI_MAX_PROMPT:,} characters."})
        s = ai_settings(load_ai())
        problem = ai_problem(s)
        if problem:
            return self.send(200, {"ok": False, "code": "disabled", "detail": problem})
        if not AI_BUSY.acquire(blocking=False):
            return self.send(200, {"ok": False, "code": "rate_limit", "detail": "Another AI request is still running. Try again when it is done."})
        try:
            text, code, detail, status = ai_request(s, (system or "").strip(), prompt, mt)
        except Exception:  # never let an error text (which could hold a header) reach the log or the page
            text, code, detail, status = None, "bad_response", "The AI request stopped with an error.", 0
        finally:
            AI_BUSY.release()
        log(f"ai ask {s['provider']}: {code or 'ok'}" + (f", HTTP {status}" if status else ""))
        if text is None:
            return self.send(200, {"ok": False, "code": code, "detail": detail})
        return self.send(200, {"ok": True, "text": text})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        if plat() == "windows":
            # on Windows SO_REUSEADDR would let a second copy share the port, so ask for the port alone
            self.allow_reuse_address = False
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)  # skips the name lookup of HTTPServer, which can take seconds
        self.server_name, self.server_port = HOST, self.server_address[1]


def _unq(s):
    return urllib.parse.unquote(s)


def main():
    for s in (sys.stdout, sys.stderr):  # a Windows console that can not show a character gets \x.. instead of a crash
        try:
            s.reconfigure(errors="backslashreplace")
        except Exception:
            pass
    global PORT
    ensure_config()
    PORT = choose_port(get_config())
    try:
        httpd = Server((HOST, PORT), Handler)
    except OSError as e:
        print(f"Port {PORT} is in use, so Paper Progress Tracker could not start ({e.strerror or e}).\n"
              f"It may already be running. Open http://{HOST}:{PORT}/ in your browser, or set another port in config.json.",
              file=sys.stderr, flush=True)
        sys.exit(1)
    build_page()
    if not DATA.exists() and (ROOT / "sample-data").is_dir():
        shutil.copytree(ROOT / "sample-data", DATA)  # first start: example papers you can delete
    DATA.mkdir(parents=True, exist_ok=True)
    private_dir(DATA)  # your papers, notes and goals: not for other accounts on a shared computer
    threading.Thread(target=backup_loop, daemon=True).start()
    threading.Thread(target=scan_loop, daemon=True).start()
    print(f"Paper Progress Tracker is running at http://{HOST}:{PORT}/", flush=True)
    names = get_config()["names"]
    if not names or names == ["Jane Doe"]:
        print('Add your own name to "names" in config.json, so the tracker can find you in author lines.', flush=True)
    try:
        in_terminal = sys.stdout.isatty()
    except (AttributeError, ValueError):
        in_terminal = False
    if in_terminal:  # the start files send this text to local/server.log, where Ctrl+C means nothing
        print("Press Ctrl+C to stop it.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
