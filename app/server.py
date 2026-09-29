#!/usr/bin/env python3
"""Paper Progress Tracker, local server.

Serves the Paper Progress Tracker page on http://127.0.0.1:47285 and keeps its data as
JSON files in ../data/<collection>/<id>.json, so the desk works with no
internet. Only this Mac can reach it (bound to 127.0.0.1).

On start it
  - keeps one backup of the data folder per day in ../backups (zip),
  - reads your paper folders with scan_folders.py (read-only) so the desk is
    current even offline.

Any tool that writes the same JSON files can keep it in sync. Nothing here deletes a file: a "delete" moves the
record into ../data/_trash with a time stamp.
"""
import json, os, re, shutil, subprocess, sys, tempfile, threading, zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST, PORT = "127.0.0.1", int(os.environ.get("PD_PORT", "47285"))
APP = Path(__file__).resolve().parent
ROOT = APP.parent
DATA = ROOT / "data"
BACKUPS = ROOT / "backups"
COLLECTIONS = {"papers", "scan", "context", "plan", "focus", "config", "journals", "venues"}
ID_RE = re.compile(r"^(?!\.\.?$)[A-Za-z0-9_\-.~:@+]{1,200}$")
MAX_BODY = 300 * 1024
TZ = datetime.now().astimezone().tzinfo  # local time zone
LOCK = threading.Lock()


def load_config():
    """config.json next to the app folder (copy config.example.json). Every field is optional."""
    for name in ("config.json", "config.example.json"):
        p = ROOT / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


CONFIG = load_config()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def doc_path(col, doc_id):
    if col not in COLLECTIONS or not ID_RE.match(doc_id):
        return None
    return DATA / col / f"{doc_id}.json"


def read_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


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
    target = BACKUPS / f"data_{datetime.now(TZ).strftime('%Y%m%d')}.zip"
    if target.exists():
        return
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in DATA.rglob("*.json"):
            if "_trash" not in f.parts:
                z.write(f, f.relative_to(DATA))


def read_folders():
    """Run the read-only folder scan against the local papers and store the result."""
    try:
        sys.path.insert(0, str(ROOT))
        import scan_folders  # noqa
        out = Path(tempfile.mkdtemp(prefix="pd-scan-"))
        state = Path(tempfile.mkdtemp(prefix="pd-state-"))
        for c in ("papers", "scan"):
            if (DATA / c).is_dir():
                shutil.copytree(DATA / c, state / c)
        scan_folders.main(str(state), str(out))
        stamp = now_iso()
        with LOCK:
            for f in out.glob("*.json"):
                d = read_json(f)
                if isinstance(d, dict):
                    d["updatedAt"] = stamp
                    write_json(DATA / "scan" / f.name, d)
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(state, ignore_errors=True)
    except Exception as e:  # the desk still works without a fresh scan
        print("folder read failed:", e, file=sys.stderr)


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperProgressTracker/1"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if not parts or parts == ["index.html"]:
            return self.send(200, (APP / "index.html").read_bytes(), "text/html; charset=utf-8")
        if parts == ["logo.png"] and (APP / "logo.png").exists():
            return self.send(200, (APP / "logo.png").read_bytes(), "image/png")
        if parts == ["api", "ping"]:
            return self.send(200, {"ok": True})
        if parts == ["api", "config"]:
            return self.send(200, {k: CONFIG.get(k) for k in ("names", "onlineUrl", "journalListName") if k in CONFIG})
        if len(parts) == 3 and parts[:2] == ["api", "c"]:
            col = parts[2]
            if col not in COLLECTIONS:
                return self.send(400, {"code": "invalid_argument"})
            docs = []
            folder = DATA / col
            if folder.is_dir():
                for f in sorted(folder.glob("*.json")):
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
        if self.headers.get("Origin") not in (None, f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"):
            return self.send(403, {"code": "forbidden"})
        if len(parts) == 2 and parts[0] == "api" and parts[1] in ("open", "choose-folder"):
            return self.finder(parts[1])
        if len(parts) != 4 or parts[:2] != ["api", "d"]:
            return self.send(404, {"code": "not_found"})
        p = doc_path(parts[2], _unq(parts[3]))
        n = int(self.headers.get("Content-Length") or 0)
        if not p or n <= 0 or n > MAX_BODY:
            return self.send(400, {"code": "invalid_argument"})
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return self.send(400, {"code": "invalid_argument"})
        op, data = body.get("op"), body.get("data")
        with LOCK:
            cur = read_json(p) if p.exists() else None
            if op == "set" and isinstance(data, dict):
                write_json(p, data)
            elif op == "update" and isinstance(data, dict):
                if not isinstance(cur, dict):
                    return self.send(400, {"code": "invalid_argument"})
                write_json(p, merge(cur, data))
            elif op == "delete":
                if p.exists():
                    trash = DATA / "_trash" / p.parent.name
                    trash.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(trash / f"{p.stem}_{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}.json"))
            else:
                return self.send(400, {"code": "invalid_argument"})
        return self.send(200, {"ok": True})


    def finder(self, action):
        """Open a paper's folder or latest version in Finder, or let you pick a new folder.
        Only paths that belong to one of your papers can be opened."""
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8")) if 0 < n <= 4096 else {}
        except Exception:
            body = {}
        pid = str(body.get("id") or "")
        paper = read_json(doc_path("papers", pid)) if doc_path("papers", pid) and doc_path("papers", pid).exists() else None
        if not isinstance(paper, dict):
            return self.send(400, {"code": "invalid_argument"})
        folders = [f for f in (paper.get("folders") or []) if isinstance(f, str) and f.strip() and not f.startswith("-")]
        if action == "open":
            what = body.get("what")
            target = None
            if what == "folder" and folders:
                target = folders[0]
            elif what == "latest":
                sc = read_json(DATA / "scan" / f"{pid}.json") or {}
                m = (sc.get("manuscript") or {}).get("path")
                if m and any(os.path.abspath(m).startswith(os.path.abspath(f) + os.sep) for f in folders):
                    target = m
            if not target or not os.path.exists(target):
                return self.send(404, {"code": "not_found"})
            if os.environ.get("PD_DRYRUN"):
                return self.send(200, {"ok": True, "opened": target})
            if sys.platform == "darwin":
                subprocess.run(["open", target], check=False)
            elif os.name == "nt":
                os.startfile(target)  # noqa
            else:
                subprocess.run(["xdg-open", target], check=False)
            return self.send(200, {"ok": True, "opened": target})
        # choose-folder: the normal Mac folder picker, starting at the current folder
        start = folders[0] if folders and os.path.isdir(folders[0]) else os.path.expanduser(CONFIG.get("papersRoot") or "~")
        prompt = ("Choose the folder for " + (paper.get("title") or pid))[:120].replace('"', "'").replace("\\", "")
        script = ['tell application "Finder"', "activate",
                  f'set f to choose folder with prompt "{prompt}" default location (POSIX file "{start}")',
                  "end tell", "return POSIX path of f"]
        if os.environ.get("PD_DRYRUN"):
            return self.send(200, {"ok": True, "dryrun": script})
        if sys.platform != "darwin":
            return self.send(200, {"ok": False, "code": "unsupported"})
        r = subprocess.run(["osascript"] + sum([["-e", x] for x in script], []), capture_output=True, text=True)
        chosen = r.stdout.strip().rstrip("/")
        if r.returncode != 0 or not chosen or not os.path.isdir(chosen):
            return self.send(200, {"ok": False, "code": "cancelled"})
        with LOCK:
            cur = read_json(doc_path("papers", pid)) or paper
            keep = [f for f in (cur.get("folders") or []) if isinstance(f, str) and f.startswith("-") and f[1:].startswith(chosen + os.sep)]
            cur["folders"] = [chosen] + keep
            cur["updatedAt"] = now_iso()
            write_json(doc_path("papers", pid), cur)
        threading.Thread(target=read_folders, daemon=True).start()
        return self.send(200, {"ok": True, "folders": cur["folders"]})


def _unq(s):
    from urllib.parse import unquote
    return unquote(s)


def main():
    if not DATA.exists() and (ROOT / "sample-data").is_dir():
        shutil.copytree(ROOT / "sample-data", DATA)  # first start: example papers you can delete
    DATA.mkdir(parents=True, exist_ok=True)
    daily_backup()
    threading.Thread(target=read_folders, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Paper Progress Tracker offline copy on http://{HOST}:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
