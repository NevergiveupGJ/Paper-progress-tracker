#!/usr/bin/env python3
"""Paper Progress Tracker folder scan.

Looks at each paper's folders on disk and writes one scan document per paper
(plus _run and _new) as JSON files. The local server runs it on start and
every few hours. You can also run it by hand.

READ-ONLY on your files: it lists, stats and reads files, and writes only to
<out_dir>. It never renames, moves, overwrites or deletes anything. It only
reports facts (files, dates, versions, word counts) and keyword-based stage
suggestions. It never changes a paper's stages. You apply suggestions in the
app. Many researchers keep old versions as v01, v02, v03, so the latest version
is chosen by version number, not by save time alone.

Cloud files that are not on this computer yet (OneDrive, iCloud Drive or
Dropbox files kept online only) are listed but never opened, because opening
one would download it.

Works on Windows, macOS and Linux with Python 3.9 or newer and nothing else.

Usage: python3 scan_folders.py <state_dir> <out_dir>
  <state_dir>/papers/*.json  the papers (for example the app's data folder)
  <state_dir>/scan/*.json    previous scan results (optional)
"""
import html, json, os, re, stat, sys, time, zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PAPERS_ROOT = "~/Documents/Papers"
MAX_DEPTH = 4
# compared in lower case. The last two are Windows system folders at the top of a drive
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "_backups", ".ipynb_checkpoints", "$recycle.bin", "system volume information"}
SKIP_FILES = {"desktop.ini", "thumbs.db"}
STAGE = {"idea": 0, "lit": 1, "data": 2, "analysis": 3, "format": 8, "submitted": 10, "revision": 11, "accepted": 12}
ANALYSIS_EXT = {".out", ".inp", ".sav", ".sps", ".r", ".rmd", ".ipynb", ".py", ".m", ".do", ".csv", ".xlsx"}
# format+?ing also matches the common misspelling "formating"
NOT_MANUSCRIPT = re.compile(r"response|reviewer|comment|receipt|codebook|template|synopsis|consent|\bcv\b|cv_|resume|guidance|\bguide\b|\bnotes?\b|(?<![a-z])memos?(?![a-z])|(?<![a-z])tables?(?![a-z])|\breferences?\b|supporting material|format+?ing for|guideline|instructions?(?![a-z])", re.I)
# these words also appear in real titles (Peer-review coding, A survey of): exclude only when the name does not say paper, manuscript ...
SOFT_NOT_MS = re.compile(r"coding|survey", re.I)
IS_MS = re.compile(r"paper|manuscript|draft|chapter|article", re.I)

# Cloud files kept online only. Reading one makes the system download it, so the scan never opens them.
SF_DATALESS = 0x40000000  # macOS st_flags (iCloud Drive, OneDrive, Dropbox)
FILE_ATTRIBUTE_OFFLINE = 0x1000  # Windows st_file_attributes
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000  # OneDrive "online-only" files
WIN_CLOUD = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
WIN_HIDDEN = 0x2 | 0x4  # hidden or system files on Windows, like dot files elsewhere
MON_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
MON = rf"(?<![a-z])({MONTHS})(?:uary|ruary|ch|il|e|y|ust|t|tember|ember|ober)?(?![a-z])"  # a month word, not 'dec' in Decision
# a real month and day with one separator used twice, so a year range (2025-2026, 2025_2026, 20252026) is never read as a date
D8 = r"(?<!\d)(20\d{2})([-_.]?)(0[1-9]|1[0-2])\2(0[1-9]|[12]\d|3[01])(?!\d)"
# a short academic year (2025-26, AY25-26) when the second year follows the first: the 26 is a year, not a day. Not Conf2026_27 Jul.
AY = r"(?<![a-z\d])([afs]y[\s_\-]?(?:20)?|20)(\d\d)([-_–])(\d\d)(?!\d)"
VER_RE = re.compile(r"(?<![a-z])(?:v|version|ver|draft|revision|rev)[\s_\-.]*(\d{1,3})(?:\.(\d{1,3}))?(?!\d)", re.I)


# ---- config, time and paths ------------------------------------------------
def load_config(path=None):
    """config.json in the app's root folder. Every field is optional. A file saved with a BOM (Windows Notepad) is fine."""
    p = Path(path) if path else HERE / "config.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def papers_root(cfg):
    """The folder where new paper folders appear (papersRoot in config.json)."""
    v = cfg.get("papersRoot")
    v = v.strip() if isinstance(v, str) and v.strip() else DEFAULT_PAPERS_ROOT
    return Path(os.path.expanduser(v))


def local_dt(ts):
    """A file time as local time. Odd times (before 1970 on Windows) still give a date."""
    try:
        return datetime.fromtimestamp(ts).astimezone()
    except (OverflowError, OSError, ValueError):
        try:
            return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ts)
        except (OverflowError, ValueError):
            return datetime(1970, 1, 1, tzinfo=timezone.utc)


def iso(ts):
    return local_dt(ts).isoformat(timespec="seconds")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def day_month(ts):
    """5 Oct. Built by hand: strftime has no day without a leading zero on Windows, and its month names follow the language setting."""
    d = local_dt(ts)
    return f"{d.day} {MON_ABBR[d.month - 1]}"


def _norm(p):
    """A path for comparing only: tidy separators, and on Windows ignore letter case."""
    return os.path.normcase(os.path.normpath(p))


def _inside(child, parent):
    """child lies inside parent. Both already went through _norm. Works for C:\\ and / too."""
    return child.startswith(parent.rstrip(os.sep) + os.sep)


def _folder(f):
    """A paper's folder as a full path (~ expanded), or None when it is not a full path."""
    if not isinstance(f, str) or not f.strip():
        return None
    p = os.path.expanduser(f.strip())
    return p if os.path.isabs(p) else None


def _stat(path):
    return os.stat(path)


def cloud_only(st):
    """True for a cloud file that is not on this computer yet (macOS dataless flag or Windows recall attributes)."""
    return bool((getattr(st, "st_flags", 0) or 0) & SF_DATALESS or (getattr(st, "st_file_attributes", 0) or 0) & WIN_CLOUD)


def _hidden(st):
    return bool((getattr(st, "st_file_attributes", 0) or 0) & WIN_HIDDEN)


def _born(st):
    """When a folder was made, or None where the system does not keep it (most Linux file systems)."""
    born = getattr(st, "st_birthtime", None)  # macOS, and Windows from Python 3.12
    if born is None and os.name == "nt":
        born = getattr(st, "st_ctime", None)  # Windows before Python 3.12 keeps the creation time here
    return born


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


# ---- versions --------------------------------------------------------------
def _date(y, m, d):
    try:
        return datetime(int(y), int(m), int(d)).strftime("%Y%m%d")
    except ValueError:
        return None


def not_manuscript(name):
    # a paper-type word, not draft: a survey or coding scheme can be a draft too
    return bool(NOT_MANUSCRIPT.search(name) or (SOFT_NOT_MS.search(name) and not re.search(r"paper|manuscript|chapter|article", name, re.I)))


def version_info(name, mtime=None):
    """Read the version habit from a file name: v01/v2/Version 3/draft 4,
    v1.10, final or camera ready, and dates like 20250115, 2025-01-15, Mar032025,
    3 Feb 2025 or Feb 7 (a date without a year takes the year the file was
    saved). A date that cannot exist (35 Dec) is ignored."""
    stem = Path(name).stem.lower()
    # read 2024-25 as 2024-2025, so 'Cohort 2024-25 Mar 3' is 3 Mar, not 25 Mar
    stem = re.sub(AY, lambda y: y.group(0) if int(y.group(4)) != (int(y.group(2)) + 1) % 100 else y.group(1) + y.group(2) + y.group(3) + "20" + y.group(4), stem)
    m = VER_RE.search(stem)
    num = int(m.group(1)) if m else None
    minor = int(m.group(2)) if m and m.group(2) else None
    year = local_dt(mtime).year if mtime else None
    date = None
    d8 = re.search(D8, stem)  # not the year in Conf2026_20250115
    mdy = re.search(rf"{MON}[\s_\-]?(\d{{1,2}})(?!\d)(?:[\s_\-,]*(20\d{{2}}))?", stem)
    dmy = re.search(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?[\s_\-]*{MON}(?:[\s_\-,]*(20\d{{2}}))?", stem)
    packed = re.search(rf"{MON}(\d{{2}})(20\d{{2}})", stem)
    if d8:
        date = _date(*d8.group(1, 3, 4))
    elif packed:
        date = _date(packed.group(3), MONTHS.split('|').index(packed.group(1)[:3]) + 1, packed.group(2))
    elif dmy or mdy:
        if dmy:
            day, mon, yr = dmy.group(1), dmy.group(2), dmy.group(3)
        else:
            mon, day, yr = mdy.group(1), mdy.group(2), mdy.group(3)
        yr = yr or (str(year) if year else "0000")
        date = _date(yr, MONTHS.split('|').index(mon[:3]) + 1, day)
    final = bool(re.search(r"final|camera.?ready", stem))
    base = stem
    for pat in (VER_RE.pattern, D8, rf"{MON}\d{{2}}20\d{{2}}",
                rf"(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?[\s_\-]*{MON}(?:[\s_\-,]*20\d{{2}})?",
                rf"{MON}[\s_\-]?\d{{1,2}}(?!\d)(?:[\s_\-,]*20\d{{2}})?",
                r"final|camera.?ready|submitted|revised|revision|draft", r"\bcopy\b( \d+)?", r"\(\d+\)"):
        base = re.sub(pat, " ", base, flags=re.I)
    base = re.sub(r"[^a-z0-9]+", " ", base).strip()
    if num is not None:
        label = f"v{num:02d}" + (f".{minor}" if minor is not None else "") + (" final" if final else "")
    elif final:
        label = "final"
    elif date:
        label = f"dated {date[6:8]}/{date[4:6]}/{date[:4]}"
    else:
        label = "no version number"
    return {"num": num, "minor": minor, "date": date, "final": final, "base": base, "label": label}


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
    # a common habit: v01, v02 ... then final. A file marked final outranks numbered drafts.
    rank = lambda f: (f["vi"]["final"], f["vi"]["num"] if f["vi"]["num"] is not None else -1, f["vi"]["minor"] if f["vi"]["minor"] is not None else -1, f["vi"]["date"] or "", f["mtime"])
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
    # a newer draft in a subfolder: versioned, or named like a paper (Drafts/Paper 2.docx), not any Word file (forms named after participants)
    newer = [f for k, g in series.items() if k[0] and not key[0] for f in g if f["mtime"] > newest_here
             and (f["vi"]["num"] is not None or f["vi"]["final"] or f["vi"]["date"] or IS_MS.search(f["name"]))]
    if newer:
        notes.append(f"Newer Word file in a subfolder: {max(newer, key=lambda f: f['mtime'])['rel']}.")
    note = (" ".join(notes) + " Check which one is current.") if notes else ""
    return {"top": top, "prev": group[1] if len(group) > 1 else None, "count": len(group), "note": note}


# ---- authorship: where are you in the author line? -------------------------
def _name_pattern(names):
    """Your name as it can appear in an author line, from "names" in config.json.
    "Jane Doe" also finds Doe, J. and J. Doe and Doe Jane and Doe, Jane and Jane A. Doe and J. A. Doe.
    List other spellings (a maiden name, a name in another script) as extra names."""
    if isinstance(names, str):
        names = [names]
    a, z = r"(?<![^\W\d_])", r"(?![^\W\d_])"  # no letter right before or after. A digit may follow (Jane Doe1, an affiliation mark)
    parts = []
    for n in names if isinstance(names, list) else []:
        w = [x for x in re.split(r"\s+", str(n).strip()) if x]
        if not w:
            continue
        parts.append(a + r"\s+".join(map(re.escape, w)) + z)
        if len(w) >= 2:
            first, last, i = re.escape(w[0]), re.escape(w[-1]), re.escape(w[0][0])
            parts += [rf"{a}{first}\s+(?:[A-Z]\.?\s+)+{last}{z}",  # Jane A. Doe
                      rf"{a}{i}\.?\s?(?:[A-Z]\.\s?)*{last}{z}",  # J. Doe, J. A. Doe
                      rf"{a}{last},?\s+{i}\.?{z}",  # Doe, J.
                      rf"{a}{last},?\s+{first}{z}"]  # Doe Jane, Doe, Jane
    return re.compile("|".join(parts) if parts else r"(?!x)x", re.I)


ME = _name_pattern(load_config().get("names"))
HEAD_STOP = re.compile(r"^(abstract|introduction|keywords?|background|1\.?\s+introduction)\b", re.I)
INST = re.compile(r"universit|institute|school|department|walk|road|street|avenue|email|e-mail|@|http|orcid|article|citation|editor|received|accepted|published|copyright|correspond|affiliat|faculty|college|centre|center|ministry|academy|lab", re.I)
NAME_LINE = re.compile(r"^[A-Z][A-Za-z\-'’.]+(?:\s+[A-Z][A-Za-z\-'’.]+){1,4}$")
PAIR = r"(?:(?:[Vv][ao]n|[Dd][aeiu]|[Dd]e[lnr]|[Ll][ae]|[Dd]os|[Tt]e[nr])\s+)*[A-Z][A-Za-z\-'’]+,\s*(?:[A-Z]\.[\s\-]*)+[*†‡§\d]*"  # APA style: Roe, R. L., Doe, J.*, & van Berg, K.
DEL = r"<w:del\b[^>]*?(?<!/)>.*?</w:del>"  # tracked deletions, but not a self-closing <w:del .../> (a deleted paragraph mark)


def docx_paras(path, n=40):
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf8", "ignore")
    except Exception:
        return []
    xml = re.sub(DEL, "", xml, flags=re.S)
    out = []
    for para in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        t = html.unescape("".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", para))).strip()
        if t:
            out.append(t)
        if len(out) >= n:
            break
    return out


def _clean_name(t):
    return re.sub(r"[\d*†‡§,]+$", "", re.sub(r"^[\d*†‡§\s]+", "", t)).strip()


def authorship(path):
    """Your position in the manuscript's author block, or None when the file
    names no authors (for example anonymised for review)."""
    head = []
    for t in docx_paras(path)[:25]:
        if HEAD_STOP.match(t):
            break
        head.append(t)
    for i, t in enumerate(head):
        if not ME.search(t) or len(t) > 250 or INST.search(t):
            continue
        corr = "*" in t
        pairs = re.findall(PAIR, t)
        if pairs and not re.sub(r"\band\b|[\s,;&*†‡§\d]", "", re.sub(PAIR, "", t)):
            chunks = pairs  # the whole line is Surname, I. pairs
        elif re.search(r",|;|\band\b|&", t):
            chunks = [c.strip() for c in re.split(r",|;|\band\b|&", t) if re.search(r"[A-Za-z]{2}", c)]
        else:
            chunks = None
        if chunks is not None:
            pos = next((k for k, c in enumerate(chunks) if ME.search(c)), None)
            if pos is None:
                continue  # your name is split oddly on this line: say nothing rather than guess 1st
            return {"position": pos + 1, "total": len(chunks), "corresponding": "*" in chunks[pos]}
        # one name per line. The first line is the title, which can look like a name (Why Feedback Matters)
        names = [k for k, x in enumerate(head)
                 if (k > 0 or ME.search(x)) and NAME_LINE.match(_clean_name(x)) and not INST.search(x) and len(_clean_name(x).split()) <= 5]
        if i in names:
            block = [k for k in names if abs(k - i) <= 12]
            return {"position": block.index(i) + 1, "total": len(block), "corresponding": corr}
        return {"position": 1, "total": 1, "corresponding": corr}
    return None


def find_authorship(candidates, first=None):
    """Check the latest version first, then other Word files, newest first."""
    if not first:
        return None
    here = _norm(os.path.dirname(first["path"]))
    # only this paper's own folder, never another paper's file further down
    order = [first] + [f for f in candidates if f is not first and _norm(os.path.dirname(f["path"])) == here]
    for f in [x for x in order if not x.get("cloudOnly")][:12]:
        a = authorship(f["path"])
        if a:
            a["file"] = f["name"]
            return a
    return None


# ---- reading folders -------------------------------------------------------
def load_docs(folder):
    out = {}
    if not folder.is_dir():
        return out
    for f in folder.glob("*.json"):
        if f.name.startswith("."):  # a temp file left by a write that was cut off
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and isinstance(d.get("data"), dict):
            d = d["data"]
        if isinstance(d, dict):
            out[f.stem] = d
    return out


def walk(root, bad=None):
    """Stat-only listing. None if the folder does not exist. Raises OSError if it
    exists but cannot be listed (permissions, a privacy block, a network drive
    that is gone). Subfolders that cannot be listed are added to bad."""
    root = Path(root)
    try:
        if not stat.S_ISDIR(_stat(root).st_mode):
            return None
    except (FileNotFoundError, NotADirectoryError, ValueError):  # ValueError: a NUL or a lone surrogate in the folder string
        return None
    except OSError as e:
        if getattr(e, "winerror", None) in (123, 161):  # Windows: the text cannot be a path (a stray : or ?)
            return None
        raise
    files, errors = [], []
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, onerror=errors.append):
        d = Path(dirpath)
        dirnames[:] = [x for x in dirnames if not x.startswith(".") and x.lower() not in SKIP_DIRS]
        if len(d.parts) - base_depth >= MAX_DEPTH:
            dirnames[:] = []
        for n in filenames:
            if n.startswith(".") or n.startswith("~$") or n.lower() in SKIP_FILES:
                continue
            p = d / n
            try:
                st = _stat(p)
            except (OSError, ValueError):
                continue
            if _hidden(st):
                continue
            files.append({"name": n, "path": str(p), "rel": str(p.relative_to(root)), "mtime": st.st_mtime, "cloudOnly": cloud_only(st)})
    if any(_norm(str(getattr(e, "filename", "") or "")) == _norm(str(root)) for e in errors):
        raise PermissionError(f"cannot list {root}")
    if bad is not None:
        bad.extend(_norm(str(e.filename)) for e in errors if getattr(e, "filename", None) and not isinstance(e, FileNotFoundError))
    return files


def made_by_script(path):
    """True for Word files generated by python-docx (tables or notes a script made), not your drafts."""
    try:
        with zipfile.ZipFile(path) as z:
            core = z.read("docProps/core.xml").decode("utf8", "ignore")
    except Exception:
        return False
    if "python-docx" not in core:
        return False
    # Word keeps the creator python-docx after you edit and save, but fills lastModifiedBy
    by = re.search(r"<cp:lastModifiedBy>([^<]*)</cp:lastModifiedBy>", core)
    return not by or not by.group(1).strip() or "python-docx" in by.group(1)


def docx_words(path):
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf8", "ignore")
    except Exception:
        return None
    # drop deleted tracked-change text, keep visible text runs
    xml = re.sub(DEL, "", xml, flags=re.S)
    # Word splits one word over several runs, so runs join with nothing. Paragraph ends, tabs and breaks separate words.
    text = "".join(" " if brk else t for t, brk in re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>|(</w:p>|<w:(?:tab|br|cr)\b[^>]*/>)", xml))
    return len(re.findall(r"\w+", html.unescape(text)))


def _ts(iso_date):
    """Local midnight of a YYYY-MM-DD date as a time stamp, or None."""
    try:
        return datetime.fromisoformat(str(iso_date)[:10]).timestamp()
    except Exception:
        return None


def suggest(files, st, now, kind="Journal", milestones=None, mdir=None, mbase=None, dismissed=None):
    """Keyword rules over file names. Each suggestion has a stable key so a
    dismissal in the app sticks. Nothing is suggested for accepted papers."""
    out = {}
    dis = [k for k in dismissed if isinstance(k, str)] if isinstance(dismissed, list) else []
    if st[STAGE["accepted"]] >= 1:
        return []
    ms = milestones if isinstance(milestones, dict) else {}
    resub = _ts(ms.get("resubmitted")) if st[STAGE["revision"]] >= 1 else None

    def add(stage, value, reason, fname, new_round=False):
        if not new_round and st[stage] >= value:
            return
        key = f"{stage}:{value}:{'round:' if new_round else ''}{fname}"[:180]
        if fname in ("data files", "analysis files") and any(k.startswith(f"{stage}:{value}:") for k in dis):
            return  # you said not yet, also under the older key that named the newest file
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
        if (re.search(r"\baccept", n) or re.search(r"\bproofs?\b|galley", n)) and \
                not re.search(r"acceptance[\s_\-]*(?:model|and[\s_\-]*use|scale|questionnaire|survey)|technology[\s_\-]*acceptance|acceptab|proof[\s_\-]*of\b(?![\s_\-]*accept)", n):
            add(STAGE["accepted"], 1, f"An acceptance or proof file is in the folder: {f['name']}", f["name"])
        if re.search(r"final|submitted|submission", stem) and ext in {".pdf", ".docx"} and not re.search(r"format+?ing for|template|guideline|instruction", stem):
            same = mdir and _norm(os.path.dirname(f["path"])) == _norm(mdir) and (not mbase or version_info(f["name"], f["mtime"])["base"] == mbase)
            if same:
                add(STAGE["format"], 1, f"A final or submission version exists: {f['name']}", f["name"])
        # no file names in these two: the newest data file can be named after a participant
        if kind in ("Journal", "Conference", "Other") and ext in ANALYSIS_EXT and now - f["mtime"] < 14 * 86400 and st[STAGE["analysis"]] == 0:
            add(STAGE["analysis"], 0.5, "Analysis files changed in the last 14 days.", "analysis files")
        if kind in ("Journal", "Conference", "Other") and ext in {".csv", ".xlsx", ".sav"} and st[STAGE["data"]] == 0:
            add(STAGE["data"], 0.5, "Data files are in the folder.", "data files")
    return list(out.values())


def _write(path, obj):
    """One result file in UTF-8 on every system. A broken character in a file name becomes ?."""
    text = json.dumps(obj, ensure_ascii=False).encode("utf-8", "replace").decode("utf-8")
    path.write_text(text, encoding="utf-8")


def main(state_dir, out_dir, config=None):
    """Scan every paper in <state_dir>/papers and write the results to out_dir. config: the settings, else config.json is read."""
    global ME
    cfg = load_config() if config is None else (config if isinstance(config, dict) else {})
    ME = _name_pattern(cfg.get("names"))
    root_dir = papers_root(cfg)
    state_dir, out_dir = Path(state_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    papers = load_docs(state_dir / "papers")
    prev = load_docs(state_dir / "scan")
    now = time.time()
    known = set()
    written, kept = 0, []
    for pid, p in papers.items():
        listed = p.get("folders") if isinstance(p.get("folders"), list) else []
        raw = [f for f in listed if isinstance(f, str) and f.strip()]
        excludes = [_norm(x) for x in (_folder(f[1:]) for f in raw if f.startswith("-")) if x]
        folders = [f for f in raw if not f.startswith("-")]
        fulls = [x for x in map(_folder, folders) if x]
        known.update(_norm(x) for x in fulls)
        if p.get("archived") or p.get("deleted"):
            continue
        if not folders:
            # the folder was removed: replace the old scan, so its author line, versions and suggestions go
            _write(out_dir / f"{pid}.json", {"paperId": pid, "scannedAt": now_iso(), "missing": [], "suggestions": []})
            written += 1
            continue
        files, missing, unreadable, seen, main_failed = [], [], [], set(), False
        pdoc = prev.get(pid) if isinstance(prev.get(pid), dict) else None
        mpath = str((pdoc.get("manuscript") or {}).get("path") or "") if pdoc and isinstance(pdoc.get("manuscript"), dict) else ""
        nm = _norm(mpath) if mpath else ""
        for f in folders:
            full, bad = _folder(f), []
            try:
                got = walk(full, bad) if full else None
            except OSError:
                unreadable.append(Path(f).name)
                main_failed = main_failed or f == folders[0] or (bool(nm) and _inside(nm, _norm(full)))  # or the last manuscript is in it
                continue
            if nm and any(_inside(nm, b) for b in bad):
                unreadable.append(Path(f).name)  # the last manuscript is in a subfolder that could not be read
                main_failed = True
            if got is None:
                missing.append(Path(f).name.encode("utf-8", "replace").decode())  # a lone surrogate cannot be written out
            else:
                for x in got:  # a folder and its parent can both be listed: count each file once
                    k = _norm(x["path"])
                    if k not in seen and not any(k == e or _inside(k, e) for e in excludes):
                        seen.add(k)
                        files.append(x)
        # keep it only if it was a read of these folders (not of the folder just changed away from), and only when the main folder failed or nothing was read
        if unreadable and pdoc and (not files or main_failed) and (not nm or any(_inside(nm, _norm(x)) for x in fulls)):
            # a folder could not be read: write the last good scan back (author line, versions), so the app keeps showing the same manuscript
            kept.append(pid)
            last = {k: v for k, v in pdoc.items() if k != "recent"}  # older scans listed full paths there
            # older scans named the newest data file (it can carry a participant name) in these keys and reasons
            if isinstance(last.get("suggestions"), list):
                last["suggestions"] = [x for x in last["suggestions"] if not (isinstance(x, dict) and re.match(r"[23]:0\.5:(?!data files$|analysis files$)", str(x.get("key"))))]
            _write(out_dir / f"{pid}.json", dict(last, paperId=pid, summary="Could not read the folder. This is the last good read."))
            continue
        st = [_num(x) for x in (p.get("stages") if isinstance(p.get("stages"), list) else [])][:13]
        st += [0.0] * (13 - len(st))
        doc = {"paperId": pid, "scannedAt": now_iso(), "missing": missing}
        if files:
            files.sort(key=lambda x: -x["mtime"])
            latest = files[0]
            doc["lastChange"] = iso(latest["mtime"])
            doc["changed7d"] = sum(1 for f in files if now - f["mtime"] < 7 * 86400)
            docs = [f for f in files if f["name"].lower().endswith(".docx") and not not_manuscript(f["name"]) and (f["cloudOnly"] or not made_by_script(f["path"]))]
            lv = latest_version(docs)
            if lv:
                m, pv = lv["top"], lv["prev"]
                words = None if m["cloudOnly"] else docx_words(m["path"])  # never download a cloud-only file
                pw = docx_words(pv["path"]) if pv and not pv["cloudOnly"] else None
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
            when = day_month(latest["mtime"])
            # no file names here: the newest file can be a data sheet named after a participant
            if doc["changed7d"]:
                doc["summary"] = f"{doc['changed7d']} file{'s' if doc['changed7d'] > 1 else ''} changed this week, the latest on {when}."
            else:
                doc["summary"] = f"No file changes for {days} days, the last on {when}."
            if unreadable:
                doc["summary"] += f" {'One folder' if len(unreadable) == 1 else 'Some folders'} could not be read."
            top = lv["top"] if lv else None
            if p.get("onlineCollab"):
                # the paper moved to a shared online document: local files are expected to be older
                doc["onlineCollab"] = True
                doc["suggestions"] = []
                if doc.get("manuscript"):
                    doc["manuscript"]["note"] = ""
                doc["summary"] = "Work continues in a shared online document. Local files: " + doc["summary"][0].lower() + doc["summary"][1:]
            else:
                doc["suggestions"] = suggest(files, st, now, p.get("type") or "Journal", p.get("milestones") or {},
                                             os.path.dirname(top["path"]) if top else None, top["vi"]["base"] if top else None, p.get("dismissed"))
        else:
            doc["summary"] = "Could not read the folder" if unreadable else "Folder not found" if missing else "Folder is empty"
            doc["suggestions"] = []
        _write(out_dir / f"{pid}.json", doc)
        written += 1

    # new top-level folders in papersRoot (made, or all files added, in the last 30 days)
    new = []
    try:
        tops = sorted(root_dir.iterdir()) if root_dir.is_dir() else []
    except OSError:
        tops = []
    for d in tops:
        try:
            if d.name.startswith(".") or d.name.lower() in SKIP_DIRS or not d.is_dir():
                continue
            dn = _norm(str(d))
            if any(k == dn or _inside(k, dn) for k in known):
                continue  # a paper already follows it or a folder inside it
            dst = _stat(d)
            if _hidden(dst):
                continue
            inner = walk(d) or []
            born = _born(dst)
        except OSError:
            continue
        newest = max((f["mtime"] for f in inner), default=0)
        first = min((f["mtime"] for f in inner), default=0)
        # a folder made in the last 30 days (moved files keep old dates), or one whose files all appeared in the last 30 days
        if inner and ((born and now - born < 30 * 86400) or (now - first < 30 * 86400 and now - newest < 30 * 86400)):
            new.append(str(d))
    _write(out_dir / "_new.json", {"folders": new[:20]})
    _write(out_dir / "_run.json", {"lastRun": now_iso(), "papersScanned": written})
    result = {"papersScanned": written, "newFolders": len(new), "keptLastScan": kept, "files": sorted(p.name for p in out_dir.glob("*.json"))}
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__.strip().split("\n\n")[-1])
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
