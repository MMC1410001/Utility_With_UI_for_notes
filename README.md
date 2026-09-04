# notesgen

Turn a Udemy course into structured revision notes - so you can watch the
course instead of pausing every minute to write things down.

Paste a course URL, tick PDF / web page / Google Doc, and get it back. There is
a local web UI, a Chrome extension that captures transcripts from the course
page you are already signed in to, and the original command line underneath
both.

**What you get, per lecture:**

- **Summary** - objective, key concepts with definitions, takeaways
- **Cheat-sheet** - the syntax, commands and gotchas worth revisiting
- **Recall** - question/answer pairs for active revision
- **Code walkthrough** - the code the instructor actually talked through

Plus a per-section overview with diagrams, and a course-level index.

A real run: a 26-section, 183-lecture course became 202,000 words of notes and
61 diagrams across 28 documents.

> **Everything runs on your machine.** The server binds to `127.0.0.1`, your
> API keys stay in a local `.env`, and transcripts are read through your own
> browser session. Nothing is uploaded anywhere except the Google Drive folder
> you explicitly ask for.

---

## Contents

1. [Quickstart](#quickstart)
2. [The Chrome extension](#the-chrome-extension)
3. [Before you start](#1-before-you-start)
4. [Install](#2-install)
5. [Configure once, then one command](#2b-configure-once-then-one-command)
6. [Get the transcripts](#3-get-the-transcripts)
7. [Choose who writes the notes](#4-choose-who-writes-the-notes)
8. [Generate the notes](#5-generate-the-notes)
9. [Get your notes out](#6-get-your-notes-out)
10. [Google Docs setup, in full](#7-google-docs-setup-in-full)
11. [Command reference](#8-command-reference)
12. [Troubleshooting](#9-troubleshooting)
13. [How it works, and what it will not do](#10-how-it-works-and-what-it-will-not-do)
14. [Security](#security)
15. [Responsible use](#responsible-use)

---

## Quickstart

```bash
git clone https://github.com/<you>/notesgen.git
cd notesgen
python3 -m pip install -r requirements.txt
python3 -m notesgen serve
```

That opens <http://127.0.0.1:8787> and prints an access token. Paste a Udemy
course URL, choose what you want, and press **Generate notes**.

The page shows progress per lecture while it runs, the running cost, and
download links at the end. Re-exporting a course you have already generated is
free - only the model calls cost anything, and finished work is never redone.

```
$ python3 -m notesgen serve

  notesgen web UI  ->  http://127.0.0.1:8787/
  extension token  ->  k7Qx...redacted
  output           ->  /path/to/notesgen/output
```

Two ways to get a course in:

| | How it works | Needs |
|---|---|---|
| **Chrome extension** (recommended) | Reads transcripts from the course page you are already signed in to | Chrome |
| **Paste a URL** | Opens a real Chrome window and waits for you to sign in to Udemy | `setup --extra udemy` |

The extension is quicker and far more reliable. The URL path drives a browser
with Playwright, which means clearing a Cloudflare check and a sign-in prompt
every time.

---

## The Chrome extension

The extension exists for one reason: it already runs inside a browser where
you are signed in to Udemy. It reads the same undocumented endpoints the
command line uses, but with none of the automation detection, so there is no
Cloudflare challenge and no 10-minute login wait.

**Install it:**

1. Start the server: `python3 -m notesgen serve`. Copy the token it prints.
2. Open `chrome://extensions` and turn on **Developer mode** (top right).
3. Click **Load unpacked** and pick this repo's `extension/` folder.
4. Click the notesgen icon, open **Settings**, and paste the token.

**Use it:** open any Udemy course you are enrolled in. A **Generate notes**
button appears at the bottom right. Click it, or use the popup if you want to
change formats first.

The popup shows progress and download links. Closing it does not stop the run -
the capture and the job are owned by the extension's background worker, so you
can shut the popup, change tabs, and come back to it.

It is loaded unpacked rather than installed from the Web Store, so Chrome will
show a "Developer mode extensions" notice. That is expected.

**If the popup says the server is not found**, check `notesgen serve` is
running and the address in Settings matches. Use `127.0.0.1`, not `localhost` -
they are not always the same host.

---

## 1. Before you start

You need:

- **Python 3.10 or newer.** Check with `python3 --version`. If that fails,
  install it from [python.org](https://www.python.org/downloads/).
- **A Udemy course you are enrolled in.** This reads captions from courses on
  your own account; it cannot reach anything you have not bought.
- **Something to write the notes.** Either the
  [Claude Code](https://claude.com/claude-code) CLI (uses your existing
  subscription, no API key, no per-token bill) or an API key from Anthropic,
  OpenAI or Google. Section 4 covers both.

Optional, and only if you want them:

- **Chrome** - for the extension, which is the easiest way to capture a course
- **Google account** - to publish notes straight into Google Docs (section 7)
- **Node.js** - to render diagrams into Word documents. Diagrams work in the
  web page output without it.

**Time and cost.** A 180-lecture course takes about an hour to process. On the
Claude Code subscription there is no extra charge. On a paid API it is roughly
$2-6 for a whole course depending on the model.

---

## 2. Install

```bash
git clone https://github.com/<you>/notesgen.git
cd notesgen
python3 -m pip install -r requirements.txt
```

That covers the core pipeline plus the web UI: notes, Word documents, web
pages, plain text, PDF.

Prefer to install it as a package? `pip install -e ".[all]"` gives you a
`notesgen` command on your PATH instead of `python3 -m notesgen`.

Some features need extra packages. Install only what you want:

```bash
python3 -m notesgen setup                  # show what is and is not installed
python3 -m notesgen setup --extra web      # the local web UI
python3 -m notesgen setup --extra udemy    # fetch transcripts from a course URL
python3 -m notesgen setup --extra gdocs    # publish to Google Docs
python3 -m notesgen setup --extra api      # use Anthropic / OpenAI / Gemini APIs
```

Check it works:

```bash
python3 -m notesgen --help
python3 tests.py && python3 tests_mdparse.py
```

---

## 2b. Configure once, then one command

Set up `.env` once and the whole thing is a single command:

```bash
cp .env.example .env
```

```ini
# what to process — a URL, a zip, or a folder
NOTESGEN_INPUT=https://www.udemy.com/course/YOUR-COURSE-SLUG/

# which model writes the notes
NOTESGEN_PROVIDER=gemini
GEMINI_API_KEY=your-key-here

# what you want out of it
NOTESGEN_OUTPUTS=all
```

```bash
python3 -m notesgen run
```

That fetches the transcripts, writes the notes, draws the diagrams, builds
every format you asked for and publishes to Drive. Nothing else to type.

### Pick what you get

Not everyone wants all of it. Set `NOTESGEN_OUTPUTS`, or pass `--outputs`:

| You want | Set |
|---|---|
| Everything | `all` |
| Only a Google Doc in Drive | `gdoc` |
| Only the shareable web page on Drive | `drive-html` |
| Both Drive formats | `gdoc,drive-html` |
| Only a local web page | `html` |
| Only Markdown | `md` |
| Only plain text | `txt` |
| Only Word files | `docx` |
| Only a PDF | `pdf` |
| PDF on Drive too | `drive-pdf` |
| Everything local, nothing uploaded | leave unset |

```bash
python3 -m notesgen run --outputs gdoc          # just a Google Doc
python3 -m notesgen run --outputs html,md       # just web page and Markdown
python3 -m notesgen run --outputs all           # the lot
```

`run` prints what it is about to make before it starts, and dependencies are
added for you — asking for `gdoc` builds the `.docx` it needs first.

| Output | What it is |
|---|---|
| `notes` | the Markdown notes — always produced, everything builds on it |
| `diagrams` | system and flow diagrams per section |
| `html` | self-contained web page with a navigation sidebar |
| `txt` | plain text |
| `md` | one Markdown file per section |
| `docx` | Word documents |
| `pdf` | a PDF, rendered from the web page |
| `gdoc` | a Google Doc in your Drive |
| `drive-html` | the web page on Drive, shareable by link |
| `drive-pdf` | the PDF on Drive, shareable by link |

| Variable | Sets |
|---|---|
| `NOTESGEN_INPUT` | default for `-i` — URL, zip, or folder |
| `NOTESGEN_OUTPUTS` | what to produce (see the table above) |
| `NOTESGEN_PROVIDER` | `claude-cli` / `anthropic` / `openai` / `gemini` |
| `NOTESGEN_MODEL` | override the model |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | API keys |
| `NOTESGEN_OUTPUT` | where notes are written (default `./output`) |
| `NOTESGEN_INPUT_DIR` | where zips are unpacked (default `./input`) |
| `NOTESGEN_BROWSER_PROFILE` | signed-in Chrome profile (default `~/.notesgen/browser-profile`) |

A command-line flag always beats `.env`, and a real environment variable beats
both. `.env` is gitignored, so keys and course URLs stay on your machine.

---

## 3. Get the transcripts

Three ways, easiest first.

### Option A — the notesgen extension (recommended)

The extension in this repo's `extension/` folder captures the transcripts and
hands them straight to your local server, so there is no zip to move around
and no sign-in to sit through. See
[The Chrome extension](#the-chrome-extension) for the four-step install.

1. Open a course you are enrolled in.
2. Click **Generate notes**.
3. Watch it in the popup, or in the web UI.

Everything stays on your machine: the extension talks only to
`127.0.0.1`.

### Option A2 — a third-party extractor

Any tool that produces the usual
`<course>/NN-Section/NN-Lecture.txt` zip works too, including
**[Udemy Transcript Extractor](https://chromewebstore.google.com/detail/udemy-transcript-extracto/oimlbilmdnimabebeilpndlndfoepopd)**.
Feed the zip in with `-i path/to.zip`, or drop the path into the web UI.

### Option B — let notesgen fetch them

```bash
python3 -m notesgen setup --extra udemy
python3 -m notesgen fetch -i "https://www.udemy.com/course/YOUR-COURSE-SLUG/"
```

A Chrome window opens on the course page. **Sign in the way you normally do** —
password, Google, SSO, 2FA, whatever. Then leave it alone: the download starts
by itself and saves a `.zip` under `input/`.

Your credentials are never seen, typed or stored by this tool. The session
stays in a Chrome profile at `~/.notesgen/browser-profile`, so you only sign in
once — change it with `NOTESGEN_BROWSER_PROFILE`. It is deliberately kept
outside the project: it holds a live Udemy session, and should not travel with
your transcripts if you copy or share that folder. Under the hood it calls the
same undocumented Udemy endpoints the extension does, from inside your own
logged-in browser.

You can also set the course URL once and drop the argument:

```ini
# .env
NOTESGEN_INPUT=https://www.udemy.com/course/YOUR-COURSE-SLUG/
```

```bash
python3 -m notesgen fetch
```

If Udemy keeps asking whether you are human, see
[Troubleshooting](#9-troubleshooting).

### What `--input` accepts

You never have to unzip anything or find the "right" folder:

| You have | Pass |
|---|---|
| The extension's zip | `-i "MyCourse-transcripts.zip"` |
| An extracted folder | `-i "path/to/MyCourse"` |
| The folder *above* it | `-i "path/to"` — it finds the course inside |
| Only the course URL | `-i "https://www.udemy.com/course/..."` |

### Running more than one course

**One course per command.** `--input` takes a single course, not a list.

Courses never interfere with each other: each gets its own folder under
`output/<course name>/`, with its own notes, its own progress file, and its own
Google Doc. Run them one after another —

```bash
python3 -m notesgen run -i "https://www.udemy.com/course/first-course/"
python3 -m notesgen run -i "https://www.udemy.com/course/second-course/"
```

— or loop:

```bash
for url in \
  "https://www.udemy.com/course/first-course/" \
  "https://www.udemy.com/course/second-course/"
do
  python3 -m notesgen run -i "$url"
done
```

If you set `NOTESGEN_INPUT` in `.env` it applies when you pass no `-i`; passing
`-i` overrides it for that run.

A course URL is downloaded **once**. Later commands reuse what is already in
`input/`, so `discover` and `export` do not reopen a browser. Force a fresh
download with `--refetch`.

To see everything you have processed, with each course's Google Doc:

```bash
python3 -m notesgen links
```

```
  2 course(s) under ./output

  Complete Agentic AI Bootcamp With LangGraph and Langchain
    https://docs.google.com/document/d/1_SEz.../edit

  Another Course You Ran
    not published to Google Docs yet
```

Sanity-check before spending anything — this makes no model calls:

```bash
python3 -m notesgen discover -i "MyCourse-transcripts.zip"
```

It prints every section, the lecture count, and flags any lecture whose
captions failed.

---

## 4. Choose who writes the notes

`generate` is the only command that costs anything. Everything else is local.

| Provider | What you need | Cost |
|---|---|---|
| **`claude-cli`** *(default)* | [Claude Code](https://claude.com/claude-code) installed and signed in | Included in your subscription |
| `anthropic` | `ANTHROPIC_API_KEY` from [console.anthropic.com](https://console.anthropic.com/) | Pay per token |
| `openai` | `OPENAI_API_KEY` from [platform.openai.com](https://platform.openai.com/api-keys) | Pay per token |
| `gemini` | `GEMINI_API_KEY` from [aistudio.google.com](https://aistudio.google.com/apikey) | Pay per token, cheapest |

With Claude Code installed you need to do nothing — it is picked automatically.

To use an API instead, copy the example file and fill in one key:

```bash
cp .env.example .env
```

```ini
NOTESGEN_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
```

`.env` is gitignored. Environment variables override it. You can also pick per
run with `--provider gemini`.

If you select a provider whose key is missing, the run stops immediately and
tells you which variable to set — it does not fail 183 lectures one at a time.

---

## 5. Generate the notes

```bash
python3 -m notesgen generate -i "MyCourse-transcripts.zip"
```

This writes one Markdown file per lecture, a section overview for each section,
and a course index. Expect roughly an hour for 180 lectures.

**You can stop it at any time.** Progress is checkpointed after every lecture,
so re-running the same command picks up where it left off and skips everything
already done. If you hit a usage limit, just run it again later.

Add diagrams (optional, about 27 extra calls):

```bash
python3 -m notesgen diagram -i "MyCourse-transcripts.zip"
```

These are system-structure and flow diagrams drawn from your notes — not
decorative clip art. Sections with nothing worth drawing say so instead of
padding.

---

## 6. Get your notes out

```bash
python3 -m notesgen build  -i "MyCourse-transcripts.zip"   # Word (.docx)
python3 -m notesgen export -i "MyCourse-transcripts.zip"   # web page, text, markdown
python3 -m notesgen push   -i "MyCourse-transcripts.zip"   # Google Docs
```

All of these are **free and instant** — local formatting, no model calls. The
output format has no bearing on cost.

| You want to | Use | How |
|---|---|---|
| Read in Google Docs | `push` | see [section 7](#7-google-docs-setup-in-full) |
| Paste into Word **with formatting** | `html/` | open in a browser, Ctrl/Cmd+A, copy, paste |
| Paste anywhere — email, Notion, a chat box | `txt/` | open, copy |
| Notion, Obsidian, GitHub | `export-md/` | one file per section |
| Upload files to Drive yourself | `docx/` | drag in, open with Google Docs |

Everything lands under `output/<course name>/`. Each folder has a short
`PASTE.md` or `UPLOAD.md` explaining what to do with it. `00 - Complete
Course.*` holds the entire course in one file, and `LINKS.md` holds your
Google Docs links once you have published.

**Do it all in one go:**

```bash
python3 -m notesgen run -i "https://www.udemy.com/course/YOUR-COURSE-SLUG/"
```

That fetches, generates, diagrams, builds and exports.

### Diagrams in Word documents

Diagrams render live in the web page output with no setup. To embed them as
images in `.docx` and Google Docs you need a renderer:

```bash
npm install -g @mermaid-js/mermaid-cli
```

Without it, `.docx` shows the diagram source and says so — it never fails the
build. If you have Node but not the package, it is fetched automatically on
first use (that download takes a few minutes once).

---

## 7. Google Docs setup, in full

This is the fiddliest part, and it is entirely Google's doing. It takes about
five minutes, once. If you would rather skip it: run `export` and drag the
`docx/` files into Drive by hand — same result, no setup.

### 7.1 Install the packages

```bash
python3 -m notesgen setup --extra gdocs
```

### 7.2 Create a Google Cloud project

1. Go to **[console.cloud.google.com](https://console.cloud.google.com/)** and
   sign in.
2. Click the project dropdown in the top bar → **New Project**.
3. Name it anything (`notesgen` is fine) → **Create**.
4. Wait for it to be created, then make sure it is **selected** in that
   dropdown. Most problems below come from being in the wrong project.

### 7.3 Enable the Drive API

1. Left menu → **APIs & Services → Library**.
2. Search for **Google Drive API**.
3. Open it → **Enable**.

### 7.4 Configure the consent screen

1. **APIs & Services → OAuth consent screen**.
2. User type: **External** → **Create**.
3. Fill the required fields — app name (anything), your own email for both
   *User support email* and *Developer contact*. Everything else can stay
   empty.
4. Save and continue through the Scopes and Test users steps.

### 7.5 Add yourself as a test user — do not skip this

1. Still on **OAuth consent screen**, find **Test users** → **+ Add users**.
2. Enter **the Google address you will sign in with**.
3. Save.

Skip this and sign-in fails with `Error 403: access_denied` before you ever see
a consent screen. It is the single most common thing to get wrong.

*(Alternatively click **Publish app**. It stays unverified, so you still get a
warning screen, but any account can then authorise.)*

### 7.6 Create the OAuth client

1. **APIs & Services → Credentials → + Create credentials → OAuth client ID**.
2. Application type: **Desktop app**. Name it anything → **Create**.
3. **Download JSON**.
4. Save that file into your notesgen folder as exactly:

```
.gdocs/google-credentials.json
```

```bash
mkdir -p .gdocs
mv ~/Downloads/client_secret_*.json .gdocs/google-credentials.json
```

`.gdocs/` is gitignored, so it is never committed.

### 7.7 Push

```bash
python3 -m notesgen push -i "MyCourse-transcripts.zip"
```

A browser opens once. Sign in **with the address you added as a test user**.

You will see **"Google hasn't verified this app"** — expected for your own
unpublished client. Click **Advanced → Go to (your app name) (unsafe)**.

The permission requested is `drive.file`: access limited to files this tool
creates. It cannot see the rest of your Drive.

When it finishes you get a link. Open it and use **View → Show outline** for
the navigation pane.

### Finding the link again

You do not have to keep the terminal open. The link is saved and shown in
several places:

```bash
python3 -m notesgen links -i "MyCourse-transcripts.zip"   # one course
python3 -m notesgen links                                # every course
```

It is also printed at the end of `discover` and `run`, and written to
`output/<course>/LINKS.md` as a clickable file next to your notes.

Re-running updates the same document instead of making duplicates, so any link
you have shared keeps working. Add `--split-sections` for one document per
section instead of one big one.

### PDF

```bash
python3 -m notesgen run --outputs pdf          # local PDF
python3 -m notesgen run --outputs drive-pdf    # and on Drive, shareable
```

The PDF is printed from the exported web page by headless Chrome, so the
diagrams come through as images and the layout matches what you see in a
browser. The navigation sidebar is dropped for print.

It is **not** exported from the Google Doc, which was the obvious route but a
dead end: Drive refuses to export any Google-native file over 10 MB, and a
full course is well past that. Rendering from HTML has no such ceiling and
does not require publishing anything first — a 917-page, 8 MB PDF of the
reference course takes about 15 seconds.

Needs a browser: `python3 -m notesgen setup --extra udemy` (shared with the
Udemy fetch).

### The web page on Drive

```bash
python3 -m notesgen run --outputs drive-html
```

This uploads the exported web page to Drive and gives you a link anyone can
open. It is often the nicer thing to share: a navigation sidebar down the left
listing every section and lecture, diagrams as images, and code blocks that
keep their formatting.

The page is deliberately **self-contained** — diagrams are embedded in the file
rather than drawn by JavaScript, and nothing is loaded from the internet.
That is what makes it display inside Drive, and it means the file still works
if you download it, email it, or open it offline years from now.

You can have both: `--outputs gdoc,drive-html` gives you an editable Google Doc
*and* a shareable page, and `notesgen links` lists both.

---

## 8. Command reference

| Command | What it does | Costs |
|---|---|---|
| `discover` | List the course, flag broken captions | free |
| `fetch` | Download transcripts (URL) or unpack a zip | free |
| `generate` | Write the notes | **the only paid step** |
| `diagram` | Add diagrams from existing notes | small |
| `build` | Make `.docx` | free |
| `export` | Make `.html`, `.txt`, `.md` | free |
| `push` | Publish to Google Docs | free |
| `links` | Show where notes live (all courses if no `-i`) | free |
| `run` | Everything above, in order | — |
| `serve` | Run the local web UI | free |
| `gdocs-auth` | Authorise Google Drive up front | free |
| `setup` | Install optional extras | free |

`serve` takes `--port` (default 8787), `--host` (default `127.0.0.1`) and
`--no-browser`.

Authorise Google **before** a long run rather than during one:

```bash
python3 -m notesgen gdocs-auth
```

A run that ends in a Google Doc now checks this first and refuses to start if
Drive is not connected — rather than generating for an hour and failing at the
upload.

Useful flags:

| Flag | Effect |
|---|---|
| `-i`, `--input` | zip, folder, or Udemy URL |
| `--section 11 --section 12` | limit to those sections |
| `--only "11-*/01-*"` | limit to matching lectures |
| `--provider gemini` | pick the model backend |
| `--model MODEL` | override the model |
| `--workers 3` | parallel calls (default 3) |
| `--force` | redo work already done |
| `--no-diagrams`, `--no-docx`, `--no-images` | skip steps |
| `--split-sections` | one Google Doc per section |
| `--attach [PORT]` | drive a Chrome you launched yourself |
| `--refetch` | re-download from Udemy instead of reusing the last download |

---

## 9. Troubleshooting

**Udemy keeps asking "are you human?"**
Close it and try attaching to your own browser instead:

```bash
./attach-chrome.sh
# in another terminal, once you are logged in there:
python3 -m notesgen fetch --attach -i "https://www.udemy.com/course/..."
```

This drives a Chrome you started, which is ordinary browsing rather than
automation. Failing that, use the extension (option A in section 3) — it always
works, because it *is* a browser.

**Udemy returns 403**
You are signed into the wrong account, or not enrolled in that course. Check
the browser window is on the account that owns it.

**`Error 403: access_denied` from Google**
You are not on the test-user list — see [7.5](#75-add-yourself-as-a-test-user--do-not-skip-this).
Also confirm you are in the right Cloud project and that the Drive API is
enabled.

**"Google hasn't verified this app"**
Expected. Click **Advanced → Go to (app) (unsafe)**. It is your own client.

**`provider 'x' needs Y_API_KEY`**
Set that variable in `.env` or your environment, or drop `--provider` to use
Claude Code.

**A lecture failed**
Re-run the same command. Finished work is skipped; only failures are retried.

**Notes for one lecture say the transcript was missing**
Udemy had no usable captions for it. That is reported rather than guessed at —
watch that lecture directly.

---

## 10. How it works, and what it will not do

### The pipeline

```
transcripts → fix caption errors → notes per lecture → section overviews
           → course index → diagrams → .docx / .html / .txt / .md / Google Docs
```

### Caption errors are fixed before the model sees them

Udemy's auto-captions mangle product names consistently — *Landgraf* and *line
graph* for LangGraph, *Lankin* for LangChain, *genetic AI* for agentic AI,
*grok* for Groq. One lecture in the test course had 36 such errors; the course
had 1,200. `glossary.yml` repairs them by pattern before anything is generated.

Add your own for your subject. One rule matters: **never add a variant that is
also an ordinary English word.** `Face`/`Phase` → `FAISS` is tempting and would
corrupt every legitimate use of those words.

### It tells you what it does not know

Transcripts are audio only. Code the instructor typed on screen without
narrating is simply absent, so the notes mark it:

> ⚠️ Code shown on screen, not described in the audio.

Lectures whose captions failed entirely are **never sent to the model** — they
get an honest "no transcript" note. A confident-looking page built from six
words is worse than an admitted gap.

Notes reflect *what the course taught*, not what is objectively true. Where an
instructor is loose, the notes follow the lecture. They are revision notes for
that course.

### Limits worth knowing

- **Google Docs tabs cannot be created by any API.** The Docs API and Apps
  Script both offer only `getTab`/`getTabs`/`getActiveTab`/`setActiveTab`.
  Navigation is the heading outline instead.
- **Diagrams are Mermaid, authored by the model** — flowcharts, sequence and
  state diagrams. Not image-model illustrations, which garble text labels on
  technical material.
- **Very long documents load slowly in Google Docs.** Use `--split-sections`.
- **Udemy's terms restrict redistributing course content.** These notes are
  derived from material you paid for; keep them for yourself. `output/` is
  gitignored for exactly this reason.

### Repeat runs

`manifest.json` records a hash of every input. Re-running skips anything
unchanged, so an interrupted run resumes cleanly and a re-run after editing one
transcript regenerates only that lecture. `--force` overrides.

### Tests

```bash
python3 tests.py           # the pipeline seams, server and importer
python3 tests_mdparse.py   # the Markdown parser and diagram sanitiser
```

`tests_mdparse.py` covers the Markdown parser. The emphasis cases matter more
than they look: a Python course is full of `**kwargs` and `x**2` in prose, and
a loose bold rule silently eats them.

`tests.py` covers the parts the UI and extension rest on. The important ones
are around `coursetree.write_tree`: the Playwright fetch and the extension's
upload both write the course directory through it, and if they ever disagree by
a single character the transcript hash changes and every note already generated
for that course is silently invalidated. The rest covers output dependency
expansion, path-traversal guards, the job queue, and the Drive pre-flight
check.

---

## Security

This is a local tool, and it is built to stay that way.

- **The server binds to `127.0.0.1`.** It is not reachable from your network.
- **Every API call needs a token.** Binding to loopback is not enough on its
  own: any website open in your browser can send requests to `127.0.0.1`. The
  token is generated on first run, stored at `~/.notesgen/server-token` with
  `0600` permissions, and printed when the server starts. Set `NOTESGEN_TOKEN`
  to pin your own.
- **Generated HTML is sandboxed when previewed.** Notes are written by a
  language model and rendered to HTML. Previewing them in the UI serves them
  under a `sandbox` Content-Security-Policy with no same-origin access, so a
  page cannot reach the UI or read the token.
- **Downloads cannot escape `output/`.** Paths are resolved and checked by
  segment, and only known artifact types are served.
- **Your credentials are never handled.** Udemy transcripts are read through a
  browser session *you* signed in to. The tool never sees a password.
- **Google access is the narrowest scope available** (`drive.file`): it can
  only touch files it created. The OAuth client and token live in `.gdocs/`,
  are `0600`, and are gitignored.
- **`.env`, `.gdocs/`, `input/` and `output/` are all gitignored.** Keep it
  that way if you fork this.

Found a security problem? Open an issue without the exploit details and ask
for a private channel.

## Responsible use

Course transcripts are the instructor's copyrighted work. These notes are
derived from them and are for **your own revision**, on courses **you have
paid for**.

- Do not publish, sell or redistribute generated notes.
- Do not use this on courses you are not enrolled in - it cannot do that
  anyway, since it reads through your own account.
- `output/` is gitignored for this reason. Do not commit it.

The Udemy endpoints this uses are internal and undocumented. They can change
or disappear without notice, and using them may sit outside Udemy's terms of
service. That judgement is yours to make.
