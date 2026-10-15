# Optional daily task for Claude Code users

This is an optional template. You do not need it to use Paper Progress Tracker.

If you use [Claude Code](https://claude.com/claude-code), you can let it read your paper folders once a day and write a short "context card" for each paper. The tracker shows the card in the paper's details, under "What I know about this paper". A card says what the paper is about, who the authors are, your place in the author line, and what the files show about its status.

The tracker's own folder reading already finds the latest version, word counts and your author position. The daily task adds a plain-language summary on top.

## Before you start

- **You need Claude Code** installed and signed in, on the same computer as the tracker and your paper folders.
- **The task must run on your own computer.** A task that runs in the cloud cannot see your folders.
- **Privacy.** The tracker itself sends nothing anywhere. This daily task is different. Claude Code sends what it reads from your files to Anthropic, so it can write the cards. Only set it up if you are fine with that for these folders. Leave out folders with data you may not share, for example participant data.
- **It uses your Claude Code plan or API credit** each time it runs.

## How it fits together

The tracker keeps each record as a JSON file in its `data/` folder.

| File | Who writes it |
|---|---|
| `data/papers/<id>.json` | The tracker. One file per paper, with its title and folders. The task only reads these. |
| `data/context/<id>.json` | **The daily task.** One context card per paper. |
| everything else in `data/` | The tracker. The task never touches these. |

The tracker checks the files every few seconds, so a new card shows up without a restart. It also works when the tracker is not running. The card appears the next time you start it.

## Context card format

One file per paper, named after the paper's `id`, for example `data/context/P01.json`. Every field is optional. Use `"unknown"` when the files do not say, and the tracker hides that line.

```json
{
  "manuscriptTitle": "Example: Classroom discourse and student agency",
  "about": "A mixed-methods study of how classroom talk relates to student agency.",
  "authors": ["Jane Doe", "Alex Example"],
  "yourPosition": "1st of 2",
  "corresponding": true,
  "venue": "unknown",
  "latestVersion": "v03",
  "statusEvidence": "Draft v03 has full Methods and Results. No submission receipt found.",
  "folderStatus": "ok",
  "folderExplanation": "",
  "suggestedFolder": "",
  "relatedPapers": [{"id": "P03", "reason": "Uses the same survey data."}],
  "sources": ["Paper_v03.docx", "Notes.docx"],
  "checkedAt": "2026-10-05T08:00:00Z"
}
```

| Field | What to put in it |
|---|---|
| `manuscriptTitle` | The title as it appears in the latest manuscript. |
| `about` | One or two plain sentences on what the paper is about. |
| `authors` | The author line, in order. Names that match `names` in your `config.json` are highlighted. |
| `yourPosition` | Your place in the author line, for example "2nd of 4". |
| `corresponding` | `true` when you are the corresponding author. |
| `venue` | The target journal or conference named in the files. |
| `latestVersion` | The latest version, for example "v03" or "final". |
| `statusEvidence` | What the files show about the status, in one or two sentences. Facts only. |
| `folderStatus` | `"ok"` when the folder belongs to this paper, `"mismatch"` when it holds a different paper, `"missing"` when it does not exist. |
| `folderExplanation` | One sentence when `folderStatus` is not ok. |
| `suggestedFolder` | The full path of a better folder, if you found one. The tracker offers a button to use it. |
| `relatedPapers` | Other papers in the tracker that share data or a topic, by their `id`, with a short reason. |
| `sources` | The file names the card is based on. |
| `checkedAt` | When the card was written, as an ISO date and time. |

## The prompt

Save the text below as `local/daily-task.md` inside the tracker's folder, and replace the two placeholders.

- `<TRACKER_FOLDER>` is the full path of the tracker's folder, for example `~/Documents/paper-progress-tracker`.
- `<PAPERS_ROOT>` is the folder that holds your paper folders, for example `~/Documents/Papers`.

```text
You keep the context cards of Paper Progress Tracker up to date.

Tracker folder: <TRACKER_FOLDER>
Paper folders live under: <PAPERS_ROOT>

Rules
1. Read only. Never create, change, move, rename or delete anything in a paper's
   folder or anywhere under <PAPERS_ROOT>.
2. Write only inside <TRACKER_FOLDER>/data/context/. Never change any other file
   in <TRACKER_FOLDER>. Never write to data/papers or data/plan.
3. Skip files that are kept only in the cloud (for example OneDrive or iCloud files
   that are not downloaded). Opening them downloads them.
4. Report only what the files show. If you are not sure, write "unknown".
   Never guess a status, a venue or a decision.
5. Keep every text field short, one or two plain sentences.

Steps
1. Read every file in <TRACKER_FOLDER>/data/papers/. Each file is one paper.
   Skip papers with "deleted": true or "archived": true.
2. The paper's "folders" list holds its folders. The first one is the main folder.
   An entry that starts with "-" is a subfolder to skip.
   If the paper has "onlineCollab": true, the current text is in a shared online
   document, so do not judge status or versions from the local files.
3. Check that the main folder belongs to this paper. Set folderStatus to "ok",
   "mismatch" or "missing". If it is not ok, explain in folderExplanation, and if you
   find the right folder under <PAPERS_ROOT>, put its full path in suggestedFolder.
4. Look at the newest files: the latest manuscript, reviewer letters, decision or
   submission emails saved as files, and response letters.
5. Write the card as JSON in the format described under "Context card format" in
   <TRACKER_FOLDER>/docs/claude-code-daily-task.md, to
   <TRACKER_FOLDER>/data/context/<id>.json. Write each card whole, in one write.
   Do not build it up in several edits. Check that it is valid JSON.
6. If a card exists and no file in the paper's folders changed since its
   checkedAt, leave the card as it is.
7. At the end, list the cards you wrote and the papers you skipped, with one
   reason each.
```

## Run it once by hand

Before you schedule anything, run the task once and look at the result. Open a terminal in the tracker's folder and run

```bash
claude -p "Follow the instructions in local/daily-task.md exactly." --add-dir "<PAPERS_ROOT>" --allowedTools "Read" "Glob" "Grep" "Edit(./data/context/**)"
```

`--add-dir` lets Claude Code look in your paper folders. `--allowedTools` lets it read files and change files only in `data/context/`. Writing a card counts as an edit. Without that permission a run that nobody watches cannot write the cards.

Then open the tracker and look at a paper's details. Check a few cards against what you know. If something is wrong, make the prompt more specific and run it again.

## Schedule it

Choose one way. Whichever you choose, the task must run on your own computer.

### If your Claude Code app offers local scheduled tasks

Some Claude Code setups can run a prompt on a schedule on your computer. Create a daily task, give it the prompt "Follow the instructions in local/daily-task.md exactly", and set its working folder to the tracker's folder. Give it access to `<PAPERS_ROOT>` as well. Do not use a task that runs in the cloud.

### Mac or Linux, with cron

Run `crontab -e` and add one line. This one runs every day at 08:00.

```bash
0 8 * * * cd "<TRACKER_FOLDER>" && "<PATH_TO_CLAUDE>" -p "Follow the instructions in local/daily-task.md exactly." --add-dir "<PAPERS_ROOT>" --allowedTools "Read" "Glob" "Grep" "Edit(./data/context/**)" >> local/daily-task.log 2>&1
```

- `<PATH_TO_CLAUDE>` is the full path that `which claude` prints. cron does not know your usual paths.
- On a Mac, folders such as Documents and Desktop are protected. cron may need Full Disk Access in System Settings, under Privacy & Security, before it can read them.

### Windows, with Task Scheduler

Save these lines as `local\daily-task.bat` in the tracker's folder.

```bat
@echo off
cd /d "<TRACKER_FOLDER>"
claude -p "Follow the instructions in local/daily-task.md exactly." --add-dir "<PAPERS_ROOT>" --allowedTools "Read" "Glob" "Grep" "Edit(./data/context/**)" >> local\daily-task.log 2>&1
```

Then open Task Scheduler, choose Create Basic Task, pick Daily and a time, choose Start a program, and select `local\daily-task.bat`.

### A note on flags

Command-line flags and permission rules can change between Claude Code versions. Check `claude --help` and the Claude Code documentation on permissions. After the first scheduled run, look at `local/daily-task.log` and at the cards in the tracker.

## Turn it off

Delete the scheduled task, the cron line or the Task Scheduler entry. The cards you already have stay in `data/context/`. To remove them, stop the tracker and delete the files in that folder.
