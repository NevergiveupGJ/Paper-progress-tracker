# Changelog

What changed in Paper Progress Tracker, newest first.

## Version 2 (October 2026)

### Updating from version 1

Download version 2 into a new folder. Stop the old tracker, as its own README describes. Copy `data/`, `backups/` and `config.json` from the old folder into the new one, then start the new tracker. Your papers and marks carry over. Papers from version 1 are moved to the new outlines automatically, and your earlier marks keep their meaning.

### Windows, Mac and Linux

- The tracker now runs on Windows. Double-click `start-windows.bat`.
- New stop files. The tracker runs in the background, and you stop it with `stop-windows.bat`, `stop.command` or `stop-linux.sh`. Linux has `start-linux.sh`.
- Choose folder, Open folder and Open latest version work on Windows and Linux as well as Mac.
- `config.json` is created for you on first start. You no longer copy it by hand.
- New settings `port` (default 47285) and `scanEveryHours` (default 6).
- The README now has step-by-step start guides for Windows and Mac, including the Windows SmartScreen and Mac Gatekeeper messages.

### Outlines by paper type

- The fixed 13 squares are replaced by outlines that fit the paper type, with short initials on each square. There are outlines for an empirical paper, a systematic review, a conceptual paper or chapter, and an editorial.
- Empirical and conceptual papers have a Literature section.
- New grant proposal outline, from checking the call to Awarded. A grant says Clarifications where a paper says R&R.

### Today and the next 30 days

- **Today's one thing.** One broad line for today, if you want one.
- **30-day goal.** One specific goal, how much it matters (0, 5 or 10), the squares that finish it, and three small supporting actions to tick each day. A Life 10 day never counts as missed. A review at the end.
- **Your record.** A card with up to three real wins from your own records.

### Writing

- **Routines per stage.** The same short steps each time a paper reaches a stage, with a note on how strong the evidence is. Add a step as a piece with one tap.
- **Pick-up note.** Write where to start next time and start 25 minutes from there.
- **Timer.** Each session is logged in the new time log, and finished pieces show in the week's summary.

### Time and reflection

- **Time log** in the style of Lyubishchev. Net time per paper from the timer, plus other time you add by hand. Core and other work are counted apart.
- **Weekly plan and promise.** Core hours you plan, and one promise for one paper.
- **Writing slot.** Your writing days and start time, and how many slots you kept.
- **Weekly and monthly reflection.**

### Links

- Links to files and folders on your computer. Paste a path in a paper's details, and the tracker opens it from there.
- Only folders and documents open from a link, and only inside your folders. Programs and scripts never open.

### Folders

- If the folder window cannot open, you can paste the folder path instead.
- Choosing a new folder keeps the paper's other folders.
- Folders are read again when you change a paper's folder, and every few hours while the tracker runs.

### Optional AI helper

- New AI helper, off by default. Choose Claude (Anthropic), OpenAI or another OpenAI-compatible service, or Ollama on your own computer. It suggests 25-minute pieces for a stage.
- It replaces the "Break down with Claude" button of version 1, which worked only when the page ran inside claude.ai.
- Your key is kept in a settings folder of your user account, outside the tracker's folder. So it is never in your data, your backups, git or a synced tracker folder. The page never receives it.

### Safety

- The daily backup is checked every hour, so a tracker that runs for days still gets one backup per day. A half-written backup never counts as the day's backup.
- The tracker refuses requests that come from other websites, and other websites cannot show it inside their pages.
- Files are saved in a safer way, so a cut-off save never leaves a broken record.
- On Mac and Linux, the data, backups and local folders can be opened by your own user account only.
- The start and stop files work only with the copy in their own folder. They never open or stop an older version or a second copy that uses the same port.

### Documentation

- New README for Windows and Mac users.
- New optional template for Claude Code users, [claude-code-daily-task.md](claude-code-daily-task.md).

## Version 1 (29 September 2026)

First public version, for Mac.

- One line per paper with 13 stage squares.
- One paper in focus, with Main author and Co-author areas.
- One done button per paper with Undo.
- 25-minute pieces with a coach, and a 25-minute timer with breaks.
- Encouragement after each finished stage, with sources.
- Read-only folder reading, links, and a journal list you load yourself.
