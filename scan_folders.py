#!/usr/bin/env python3
"""Paper Progress Tracker folder scan.

Reads the paper documents exported from the Paper Progress Tracker artifact database,
looks at each paper's folders on disk, and writes one scan document per paper
(plus _run and _new) as JSON files ready to upload to the `scan` collection.

READ-ONLY on your files: it lists, stats and reads files, and writes only to
<out_dir>. It never renames, moves, overwrites or deletes anything. It only
reports facts (files, dates, versions, word counts) and keyword-based stage
suggestions. It never changes a paper's stages: you apply suggestions in
the app. Many researchers keep old versions as v01, v02, v03, so the latest version is
chosen by version number, not by save time alone.

Usage: python3 scan_folders.py <state_dir> <out_dir>
  <state_dir>/papers/*.json  exported papers collection
  <state_dir>/scan/*.json    previous scan collection (optional)
"""
import json, os, re, sys, zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

def _config():
    p = Path(__file__).resolve().parent / "config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


CONFIG = _config()
# where new paper folders appear (optional). The scan lists folders here that no paper uses yet.
PAPERS_ROOT = Path(os.path.expanduser(CONFIG.get("papersRoot") or "~/Documents/Papers"))
MAX_DEPTH = 4
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "_backups", ".ipynb_checkpoints"}
STAGE = {"idea": 0, "lit": 1, "data": 2, "analysis": 3, "format": 8, "submitted": 10, "revision": 11, "accepted": 12}
ANALYSIS_EXT = {".out", ".inp", ".sav", ".sps", ".r", ".rmd", ".ipynb", ".py", ".m", ".do", ".csv", ".xlsx"}
NOT_MANUSCRIPT = re.compile(r"response|reviewer|comment|receipt|codebook|coding|template|synopsis|table|consent|survey|\bcv\b|cv_|resume|guidance|\bguide\b|\bnotes?\b|memo|\breferences?\b|supporting material|format+?ing for|guideline|instruction", re.I)
TZ = datetime.now().astimezone().tzinfo  # local time zone


MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
VER_RE = re.compile(r"(?<![a-z])(?:v|version|ver|draft|revision|rev)[\s_\-.]*(\d{1,3})(?!\d)", re.I)


def version_info(name, mtime=None):
    """Read the version habit from a file name: v01/v2/Version 3/draft 4,
    final or camera ready, and dates like 20260909, Sep282026, 11 Aug 2026
    or Aug 14 (a date without a year takes the year the file was saved)."""
    stem = Path(name).stem.lower()
    m = VER_RE.search(stem)
    num = int(m.group(1)) if m else None
    year = datetime.fromtimestamp(mtime, TZ).year if mtime else None
    date = None
    d8 = re.search(r"(20\d{2})(\d{2})(\d{2})", stem)
    mdy = re.search(rf"({MONTHS})[a-z]*[\s_\-]?(\d{{1,2}})(?!\d)(?:[\s_\-,]*(20\d{{2}}))?", stem)
    dmy = re.search(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?[\s_\-]*({MONTHS})[a-z]*(?:[\s_\-,]*(20\d{{2}}))?", stem)
    packed = re.search(rf"({MONTHS})[a-z]*(\d{{2}})(20\d{{2}})", stem)
    if d8:
        date = d8.group(0)
    elif packed:
        date = f"{packed.group(3)}{MONTHS.split('|').index(packed.group(1)[:3]) + 1:02d}{packed.group(2)}"
    elif dmy or mdy:
        if dmy:
            day, mon, yr = dmy.group(1), dmy.group(2), dmy.group(3)
        else:
            mon, day, yr = mdy.group(1), mdy.group(2), mdy.group(3)
        yr = yr or (str(year) if year else "0000")
        date = f"{yr}{MONTHS.split('|').index(mon[:3]) + 1:02d}{int(day):02d}"
    final = bool(re.search(r"final|camera.?ready", stem))
    base = stem
    for pat in (VER_RE.pattern, r"20\d{6}", rf"({MONTHS})[a-z]*\d{{2}}20\d{{2}}",
                rf"(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?[\s_\-]*({MONTHS})[a-z]*(?:[\s_\-,]*20\d{{2}})?",
                rf"({MONTHS})[a-z]*[\s_\-]?\d{{1,2}}(?!\d)(?:[\s_\-,]*20\d{{2}})?",
                r"final|camera.?ready|submitted|revised|revision|draft", r"\bcopy\b( \d+)?", r"\(\d+\)"):
        base = re.sub(pat, " ", base, flags=re.I)
    base = re.sub(r"[^a-z0-9]+", " ", base).strip()
    if num is not None:
        label = f"v{num:02d}" + (" final" if final else "")
    elif final:
        label = "final"
    elif date:
        label = f"dated {date[6:8]}/{date[4:6]}/{date[:4]}"
    else:
        label = "no version number"
    return {"num": num, "date": date, "final": final, "base": base, "label": label}


def latest_version(docs):
    """Pick the document series touched most recently (top-level first), then
    its highest version. Never trusts save time alone."""
    series = {}
    for f in docs:
        vi = version_info(f["name"], f["mtime"])
        f = dict(f, vi=vi)
        key = (os.sep in f["rel"], vi["base"])
        series.setdefault(key, []).append(f)
    if not series:
        return None
    key = min(series, key=lambda k: (k[0], -max(x["mtime"] for x in series[k])))
    group = series[key]
    # common habit: v01, v02 ... then final. A file marked final outranks numbered drafts.
    rank = lambda f: (f["vi"]["final"], f["vi"]["num"] if f["vi"]["num"] is not None else -1, f["vi"]["date"] or "", f["mtime"])
    group.sort(key=rank, reverse=True)
    top = group[0]
    newest_saved = max(group, key=lambda f: f["mtime"])
    notes = []
    if newest_saved is not top:
        notes.append(f"{newest_saved['name']} was saved after {top['name']}.")
    finals = [f for f in group if f["vi"]["final"] and f is not top]
    if finals and not top["vi"]["final"]:
        notes.append(f"There is also a file marked final: {finals[0]['name']}.")
    newest_here = max(f["mtime"] for f in group)
    for k, g in series.items():
        if k[0] and not key[0] and max(f["mtime"] for f in g) > newest_here:
            versioned = [f for f in g if f["vi"]["num"] is not None or f["vi"]["final"] or f["vi"]["date"]]
            if versioned:
                v = max(versioned, key=lambda f: f["mtime"])
                notes.append(f"Newer versioned file in a subfolder: {v['rel']}.")
                break
    note = (" ".join(notes) + " Check which one is current.") if notes else ""
    return {"top": top, "prev": group[1] if len(group) > 1 else None, "count": len(group), "note": note}


# ---- authorship: where are you in the author line? -------------------------
def _name_pattern(names):
    """Your name as it appears in author lines: "Jane Doe", "Doe, J.", "J. Doe", "Doe Jane"."""
    parts = []
    for n in names or []:
        w = [x for x in re.split(r"\s+", str(n).strip()) if x]
        if not w:
            continue
        parts.append(r"\b" + r"\s+".join(map(re.escape, w)) + r"\b")
        if len(w) >= 2:
            first, last = w[0], w[-1]
            parts += [rf"\b{re.escape(last)},?\s+{re.escape(first[0])}\.?(?![a-z])", rf"\b{re.escape(first[0])}\.?\s?{re.escape(last)}\b",
                      rf"\b{re.escape(last)}\s+{re.escape(first)}\b"]
    return re.compile("|".join(parts) if parts else r"(?!x)x", re.I)


ME = _name_pattern(CONFIG.get("names"))
HEAD_STOP = re.compile(r"^(abstract|introduction|keywords?|background|1\.?\s+introduction)\b", re.I)
INST = re.compile(r"universit|institute|school|department|walk|road|email|e-mail|@|http|orcid|article|citation|editor|received|accepted|published|copyright|correspond|affiliat|faculty|college|centre|center|ministry|academy|lab", re.I)
NAME_LINE = re.compile(r"^[A-Z][A-Za-z\-'’.]+(?:\s+[A-Z][A-Za-z\-'’.]+){1,4}$")


def docx_paras(path, n=40):
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf8", "ignore")
    except Exception:
        return []
    xml = re.sub(r"<w:del\b.*?</w:del>", "", xml, flags=re.S)
    out = []
    for para in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        t = "".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", para)).strip()
        if t:
            out.append(t.replace("&amp;", "&"))
        if len(out) >= n:
            break
    return out


def _clean_name(t):
    return re.sub(r"[\d*†‡§,]+$", "", re.sub(r"^[\d*†‡§\s]+", "", t)).strip()


def authorship(path):
    """Your position in the manuscript's author block, or None when the file
    names no authors (e.g. anonymised for review)."""
    head = []
    for t in docx_paras(path)[:25]:
        if HEAD_STOP.match(t):
            break
        head.append(t)
    for i, t in enumerate(head):
        if not ME.search(t) or len(t) > 250 or INST.search(t):
            continue
        corr = "*" in t
        if re.search(r",|\band\b|&", t):
            chunks = [c.strip() for c in re.split(r",|\band\b|&", t) if re.search(r"[A-Za-z]{2}", c)]
            pos = next((k for k, c in enumerate(chunks) if ME.search(c)), 0)
            corr = bool(re.search(r"\*", chunks[pos])) if chunks else corr
            return {"position": pos + 1, "total": len(chunks), "corresponding": corr}
        names = [k for k, x in enumerate(head)
                 if NAME_LINE.match(_clean_name(x)) and not INST.search(x) and len(_clean_name(x).split()) <= 5]
        if i in names:
            block = [k for k in names if abs(k - i) <= 12]
            return {"position": block.index(i) + 1, "total": len(block), "corresponding": corr}
        return {"position": 1, "total": 1, "corresponding": corr}
    return None


def find_authorship(candidates, first=None):
    """Check the latest version first, then other Word files, newest first."""
    if not first:
        return None
    here = os.path.dirname(first["path"])
    # only this paper's own folder, never another paper's file further down
    order = [first] + [f for f in candidates if f is not first and os.path.dirname(f["path"]) == here]
    for f in order[:12]:
        a = authorship(f["path"])
        if a:
            a["file"] = f["name"]
            return a
    return None


def load_docs(folder):
    out = {}
    if not folder.is_dir():
        return out
    for f in folder.glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if isinstance(d, dict) and isinstance(d.get("data"), dict):
            d = d["data"]
        out[f.stem] = d
    return out


def iso(ts):
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds")


def walk(root):
    root = Path(root)
    if not root.is_dir():
        return None
    files = []
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = [x for x in dirnames if not x.startswith(".") and x not in SKIP_DIRS]
        if len(d.parts) - base_depth >= MAX_DEPTH:
            dirnames[:] = []
        for n in filenames:
            if n.startswith(".") or n.startswith("~$"):
                continue
            p = d / n
            try:
                st = p.stat()
            except OSError:
                continue
            files.append({"name": n, "path": str(p), "rel": str(p.relative_to(root)), "mtime": st.st_mtime})
    return files


def made_by_script(path):
    """True for Word files generated by python-docx (generated tables or notes), not your drafts."""
    try:
        with zipfile.ZipFile(path) as z:
            core = z.read("docProps/core.xml").decode("utf8", "ignore")
        return "python-docx" in core
    except Exception:
        return False


def docx_words(path):
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf8", "ignore")
    except Exception:
        return None
    # drop deleted tracked-change text, keep visible text runs
    xml = re.sub(r"<w:del\b.*?</w:del>", "", xml, flags=re.S)
    text = " ".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", xml))
    return len(re.findall(r"\w+", text))


def _ts(iso_date):
    try:
        return datetime.fromisoformat(str(iso_date)[:10]).replace(tzinfo=TZ).timestamp()
    except Exception:
        return None


def suggest(files, st, now, kind="Journal", milestones=None, mdir=None, mbase=None):
    """Keyword rules over file names. Each suggestion has a stable key so a
    dismissal in the app sticks. Nothing is suggested for accepted papers."""
    out = {}
    if st[STAGE["accepted"]] >= 1:
        return []
    ms = milestones or {}
    resub = _ts(ms.get("resubmitted")) if st[STAGE["revision"]] >= 1 else None

    def add(stage, value, reason, fname, new_round=False):
        if not new_round and st[stage] >= value:
            return
        key = f"{stage}:{value}:{'round:' if new_round else ''}{fname}"[:180]
        if (stage, value) not in out:
            out[(stage, value)] = {"key": key, "stage": stage, "value": value, "reason": reason, **({"newRound": True} if new_round else {})}

    for f in sorted(files, key=lambda x: -x["mtime"]):
        n = f["name"].lower()
        stem = Path(n).stem
        ext = Path(n).suffix
        review_file = ("decision" in n and ("manuscript" in n or "decision on" in n)) or \
            re.search(r"response to (the )?(reviewer|comment)", n) or re.search(r"reviewer.?comment", n) or re.match(r"reviewer\s*\d", stem)
        if review_file:
            if st[STAGE["revision"]] >= 1:
                # already resubmitted: only a review file newer than the resubmission means a new round
                if resub and f["mtime"] > resub + 86400:
                    add(STAGE["revision"], 0.5, f"New reviewer material after your resubmission: {f['name']}", f["name"], new_round=True)
            else:
                add(STAGE["submitted"], 1, f"Reviewer or decision material means it was submitted: {f['name']}", f["name"])
                if "decision" not in n:
                    add(STAGE["revision"], 0.5, f"A response or reviewer file suggests revision work: {f['name']}", f["name"])
        if re.search(r"\baccept", n) or re.search(r"\bproofs?\b|galley", n):
            add(STAGE["accepted"], 1, f"An acceptance or proof file is in the folder: {f['name']}", f["name"])
        if re.search(r"final|submitted|submission", stem) and ext in {".pdf", ".docx"} and not re.search(r"format+?ing for|template|guideline|instruction", stem):
            same = mdir and os.path.dirname(f["path"]) == mdir and (not mbase or version_info(f["name"], f["mtime"])["base"] == mbase)
            if same:
                add(STAGE["format"], 1, f"A final or submission version exists: {f['name']}", f["name"])
        if kind in ("Journal", "Conference", "Other") and ext in ANALYSIS_EXT and now - f["mtime"] < 14 * 86400 and st[STAGE["analysis"]] == 0:
            add(STAGE["analysis"], 0.5, f"Analysis files changed in the last 14 days, e.g. {f['name']}", f["name"])
        if kind in ("Journal", "Conference", "Other") and ext in {".csv", ".xlsx", ".sav"} and st[STAGE["data"]] == 0:
            add(STAGE["data"], 0.5, f"Data files are in the folder, e.g. {f['name']}", f["name"])
    return list(out.values())


def main(state_dir, out_dir):
    state_dir, out_dir = Path(state_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    papers = load_docs(state_dir / "papers")
    prev = load_docs(state_dir / "scan")
    now = datetime.now(TZ).timestamp()
    known = set()
    written = 0
    for pid, p in papers.items():
        raw = [f for f in (p.get("folders") or []) if isinstance(f, str) and f.strip()]
        excludes = [os.path.normpath(f[1:]) for f in raw if f.startswith("-")]
        folders = [f for f in raw if not f.startswith("-")]
        known.update(os.path.normpath(f) for f in folders)
        if not folders or p.get("archived") or p.get("deleted"):
            continue
        files, missing = [], []
        for f in folders:
            got = walk(f)
            if got is None:
                missing.append(Path(f).name)
            else:
                files.extend(x for x in got if not any(x["path"] == e or x["path"].startswith(e + os.sep) for e in excludes))
        st = [float(x or 0) for x in (p.get("stages") or [])][:13]
        st += [0.0] * (13 - len(st))
        doc = {"paperId": pid, "scannedAt": datetime.now(TZ).isoformat(timespec="seconds"), "missing": missing}
        if files:
            files.sort(key=lambda x: -x["mtime"])
            latest = files[0]
            doc["lastChange"] = iso(latest["mtime"])
            doc["changed7d"] = sum(1 for f in files if now - f["mtime"] < 7 * 86400)
            doc["recent"] = [{"name": f["name"], "path": f["path"], "modified": iso(f["mtime"])} for f in files[:6]]
            docs = [f for f in files if f["name"].lower().endswith(".docx") and not NOT_MANUSCRIPT.search(f["name"]) and not made_by_script(f["path"])]
            lv = latest_version(docs)
            if lv:
                m, pv = lv["top"], lv["prev"]
                words = docx_words(m["path"])
                pw = docx_words(pv["path"]) if pv else None
                doc["manuscript"] = {
                    "name": m["name"], "path": m["path"], "modified": iso(m["mtime"]), "version": m["vi"]["label"],
                    "versions": lv["count"], "words": words,
                    "prevName": pv["name"] if pv else "", "prevVersion": pv["vi"]["label"] if pv else "",
                    "delta": (words - pw) if (words is not None and pw is not None) else 0,
                    "note": lv["note"],
                }
            auth = find_authorship(docs, lv["top"] if lv else None)
            if auth:
                doc["authorship"] = auth
            days = int((now - latest["mtime"]) // 86400)
            when = datetime.fromtimestamp(latest["mtime"], TZ).strftime("%-d %b")
            if doc["changed7d"]:
                doc["summary"] = f"{doc['changed7d']} file{'s' if doc['changed7d'] > 1 else ''} changed this week, latest {latest['name']} ({when})"
            else:
                doc["summary"] = f"No file changes for {days} days, last was {latest['name']} ({when})"
            top = lv["top"] if lv else None
            if p.get("onlineCollab"):
                # this paper moved to a shared online document: local files are expected to be older
                doc["onlineCollab"] = True
                doc["suggestions"] = []
                if doc.get("manuscript"):
                    doc["manuscript"]["note"] = ""
                doc["summary"] = "Work continues in a shared online document. Local files: " + doc["summary"][0].lower() + doc["summary"][1:]
            else:
                doc["suggestions"] = suggest(files, st, now, p.get("type") or "Journal", p.get("milestones") or {},
                                         os.path.dirname(top["path"]) if top else None, top["vi"]["base"] if top else None)
        else:
            doc["summary"] = "Folder not found" if missing else "Folder is empty"
            doc["suggestions"] = []
        (out_dir / f"{pid}.json").write_text(json.dumps(doc, ensure_ascii=False))
        written += 1

    # new top-level folders in papersRoot (all files from the last 30 days)
    new = []
    if PAPERS_ROOT.is_dir():
        for d in sorted(PAPERS_ROOT.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if os.path.normpath(str(d)) in known:
                continue
            inner = walk(d) or []
            newest = max((f["mtime"] for f in inner), default=0)
            first = min((f["mtime"] for f in inner), default=0)
            # a folder whose files all appeared in the last 30 days
            if inner and now - first < 30 * 86400 and now - newest < 30 * 86400:
                new.append(str(d))
    (out_dir / "_new.json").write_text(json.dumps({"folders": new[:20]}, ensure_ascii=False))
    (out_dir / "_run.json").write_text(json.dumps({"lastRun": datetime.now(TZ).isoformat(timespec="seconds"), "papersScanned": written}))
    print(json.dumps({"papersScanned": written, "newFolders": len(new), "files": sorted(p.name for p in out_dir.glob("*.json"))}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
