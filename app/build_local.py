#!/usr/bin/env python3
"""Build the page the local server shows (app/index.html) from app/source.html.

source.html has no <html> or <head>, so the same file can also run as a Claude
artifact. This wraps it into a full page. The start files run it on every
start. Run it by hand only after you edit source.html while the server runs.
"""
import os, tempfile, time
from pathlib import Path

APP = Path(__file__).resolve().parent
HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="icon" href="/logo.png">
<style>html{color-scheme:light dark}body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>
</head>
<body>
"""


def build():
    """Write app/index.html in one step, so the server never sends half a page. Same bytes (LF, UTF-8) on every system."""
    src = (APP / "source.html").read_text(encoding="utf-8")
    page = (HEAD + src + "\n</body>\n</html>\n").encode("utf-8")
    target = APP / "index.html"
    fd, tmp = tempfile.mkstemp(dir=str(APP), prefix=".index-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(page)
        os.chmod(tmp, 0o644)  # a temp file starts private to you. The page is an ordinary file.
        for attempt in range(10):
            try:
                os.replace(tmp, target)
                break
            except PermissionError:  # Windows: the server is reading the old page this very moment
                if attempt == 9:
                    raise
                time.sleep(0.2)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


if __name__ == "__main__":
    print("built", build())
