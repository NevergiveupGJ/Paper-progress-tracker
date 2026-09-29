#!/usr/bin/env python3
"""Build the offline page (app/index.html) from the same source as the online desk.

The online artifact file has no <html>/<head> (claude.ai adds them). This wraps
it into a full page for the local server. Run after every change to source.html.
"""
from pathlib import Path

APP = Path(__file__).resolve().parent
src = (APP / "source.html").read_text(encoding="utf-8")
head = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="icon" href="/logo.png">
<style>html{color-scheme:light dark}body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>
</head>
<body>
"""
(APP / "index.html").write_text(head + src + "\n</body>\n</html>\n", encoding="utf-8")
print("built", APP / "index.html")
