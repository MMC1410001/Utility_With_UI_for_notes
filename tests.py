"""Tests for the pieces the web UI and the Chrome extension depend on.

Run: python3 tests.py

The load-bearing one is `write_tree`. Two very different callers now produce
the course directory that `discover.py` reads - the Playwright fetch and the
extension's upload - and if they ever disagree by a single character, the
transcript hash changes and every note already generated for that course is
silently invalidated.
"""

import json
import tempfile
import zipfile
from pathlib import Path

from notesgen import coursetree, ingest, outputs as outputs_mod
from notesgen.discover import STATUS_NO_TRANSCRIPT, discover, flatten
from notesgen.server.importer import ImportError_, import_course

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ok    {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL  {label}  {detail}")


def tmpdir():
    return Path(tempfile.mkdtemp())


CURRICULUM = [
    {"_class": "chapter", "title": "Getting Started"},
    {"_class": "lecture", "title": "Welcome!", "body": "one"},
    {"_class": "quiz", "title": "Quiz 1"},
    {"_class": "lecture", "title": "Set/up: paths", "body": "two"},
    {"_class": "chapter", "title": "Deeper"},
    {"_class": "lecture", "title": "Silent", "body": None},
]


def test_write_tree():
    print("\ncoursetree.write_tree")
    root, written, missing = coursetree.write_tree(
        "My Course", CURRICULUM, tmpdir(), lambda item: item.get("body")
    )

    check("counts transcripts", (written, missing) == (2, 1), f"got {written},{missing}")

    # A quiz between two lectures must not consume a lecture number.
    # "Set/up: paths" loses the / and : entirely - it does not become a space.
    second = root / "01-Getting Started" / "02-Setup paths.txt"
    check("skips non-lectures when numbering", second.exists(),
          f"missing {second.name}; got {[p.name for p in second.parent.iterdir()]}")
    check("strips path characters from names", "/" not in second.name)

    # Numbering restarts inside each chapter.
    check("restarts numbering per chapter",
          (root / "02-Deeper" / "01-Silent.txt").exists())

    body = (root / "01-Getting Started" / "01-Welcome!.txt").read_text()
    check("writes the header block",
          body.startswith("Course: My Course\nChapter: Getting Started\nLecture: Welcome!\n"))
    check("header ends with a rule", "-" * 40 in body.split("\n")[3])
    check("body follows the header", body.rstrip().endswith("one"))

    silent = (root / "02-Deeper" / "01-Silent.txt").read_text()
    check("marks a missing transcript", coursetree.NO_TRANSCRIPT in silent)
    check("writes the combined file", (root / "_full-transcript.txt").exists())


def test_write_tree_edges():
    print("\ncoursetree.write_tree edge cases")
    # Lectures before any chapter header get a synthetic section.
    root, _, _ = coursetree.write_tree(
        "Loose", [{"_class": "lecture", "title": "First", "body": "x"}],
        tmpdir(), lambda i: i.get("body"))
    check("invents a section for orphan lectures",
          (root / "01-Course Content" / "01-First.txt").exists())

    # An untitled lecture still gets a usable filename.
    root, _, _ = coursetree.write_tree(
        "Blank", [{"_class": "lecture", "title": "", "body": "x"}],
        tmpdir(), lambda i: i.get("body"))
    names = [p.name for p in (root / "01-Course Content").iterdir()]
    check("falls back to a lecture number", names == ["01-Lecture 1.txt"], names)

    check("safe() never returns empty", coursetree.safe('///') == "untitled")
    check("safe() collapses whitespace", coursetree.safe("a   b") == "a b")
    check("course_slug parses a URL",
          coursetree.course_slug("https://www.udemy.com/course/abc-def/?x=1") == "abc-def")


def test_importer():
    print("\nserver.importer")
    vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n"
           + "Agents route tool calls carefully. " * 80 + "\n")
    payload = {
        "title": "Imported Course",
        "slug": "imported-course",
        "format": "vtt",
        "items": [
            {"_class": "chapter", "title": "Alpha"},
            {"_class": "lecture", "title": "Real", "vtt": vtt},
            {"_class": "lecture", "title": "Empty", "vtt": None},
        ],
    }
    result = import_course(payload, tmpdir())
    check("reports what it wrote",
          (result.lectures, result.missing, result.sections) == (1, 1, 1),
          f"{result.lectures},{result.missing},{result.sections}")
    check("records the source slug",
          (result.course_dir / ".source").read_text().strip() == "imported-course")

    # The whole point of sending raw VTT: cue timings must be gone.
    text = (result.course_dir / "01-Alpha" / "01-Real.txt").read_text()
    check("converts VTT to prose", "-->" not in text and "WEBVTT" not in text)

    # discover must read an imported tree exactly like a fetched one.
    lectures = {l.slug: l for l in flatten(discover(result.course_dir))}
    real = lectures["01-Alpha/01-Real"]
    check("discover sees a usable transcript", real.status == "ok", real.status)
    check("discover stubs the silent lecture",
          lectures["01-Alpha/02-Empty"].status == STATUS_NO_TRANSCRIPT)

    for label, bad in [
        ("rejects a missing title", {"items": payload["items"]}),
        ("rejects an empty curriculum", {"title": "X", "items": []}),
        ("rejects a lecture-free payload",
         {"title": "X", "items": [{"_class": "chapter", "title": "a"}]}),
        ("rejects an implausible slug",
         {"title": "X", "slug": "../etc", "items": payload["items"]}),
        ("rejects an unknown format",
         {"title": "X", "format": "docx", "items": payload["items"]}),
    ]:
        try:
            import_course(bad, tmpdir())
            check(label, False, "no error raised")
        except ImportError_:
            check(label, True)


def test_outputs_dependencies():
    print("\noutputs.parse dependency expansion")
    cases = [
        ("gdoc", {"notes", "docx", "gdoc"}),
        ("pdf", {"notes", "html", "pdf"}),
        ("drive-pdf", {"notes", "html", "pdf", "drive-pdf"}),
        ("drive-html", {"notes", "html", "drive-html"}),
    ]
    for value, needed in cases:
        got = set(outputs_mod.parse(value))
        check(f"{value} pulls in {sorted(needed - {value})}",
              needed <= got, f"got {sorted(got)}")
    check("notes is always present", "notes" in outputs_mod.parse("txt"))
    try:
        outputs_mod.parse("nonsense")
        check("rejects an unknown output", False)
    except outputs_mod.OutputError:
        check("rejects an unknown output", True)


def test_zip_traversal():
    print("\ningest.extract_zip")
    work = tmpdir()
    archive = work / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escaped.txt", "x")
    try:
        ingest.extract_zip(archive, work / "dest")
        check("refuses ../ traversal", False, "extraction succeeded")
    except ingest.IngestError:
        check("refuses ../ traversal", True)


def test_safe_file():
    print("\nserver.files.safe_file")
    from fastapi import HTTPException

    from notesgen.server import files as files_mod

    out = tmpdir()
    course = out / "Course"
    (course / "html").mkdir(parents=True)
    (course / "html" / "page.html").write_text("<p>hi</p>")
    (out / "secret.txt").write_text("nope")
    # A sibling whose name merely starts the same must not be reachable.
    (out / "Course-evil").mkdir()
    (out / "Course-evil" / "leak.txt").write_text("nope")

    good = files_mod.safe_file(out, "Course", "html/page.html")
    check("serves a real artifact", good.read_text() == "<p>hi</p>")

    for label, course_name, rel in [
        ("blocks ../ escape", "Course", "../secret.txt"),
        ("blocks a nested course name", "../..", "etc/passwd"),
        ("blocks a prefix sibling", "Course", "../Course-evil/leak.txt"),
    ]:
        try:
            files_mod.safe_file(out, course_name, rel)
            check(label, False, "returned a path")
        except HTTPException:
            check(label, True)

    (course / "html" / "notes.exe").write_text("x")
    try:
        files_mod.safe_file(out, "Course", "html/notes.exe")
        check("blocks a non-artifact suffix", False)
    except HTTPException:
        check("blocks a non-artifact suffix", True)


def test_job_queue():
    print("\nserver.jobs.JobQueue")
    import time

    from notesgen.server.jobs import JobQueue

    q = JobQueue()
    job = q.submit("test", lambda j: {"n": 41 + 1})
    for _ in range(100):
        if job.state in ("done", "failed"):
            break
        time.sleep(0.05)
    check("runs a job", job.state == "done" and job.result == {"n": 42},
          f"{job.state} {job.result}")

    def explode(job):
        raise RuntimeError("nope")

    boom = q.submit("test", explode)
    for _ in range(100):
        if boom.state in ("done", "failed"):
            break
        time.sleep(0.05)
    check("records a failure", boom.state == "failed" and "nope" in (boom.error or ""),
          f"{boom.state} {boom.error}")

    # A job cancelled before it starts must never run its body.
    ran = []
    slow = q.submit("test", lambda j: (time.sleep(0.4), {"ok": True})[1])
    queued = q.submit("test", lambda j: ran.append(1))
    q.cancel(queued.id)
    for _ in range(100):
        if slow.state in ("done", "failed"):
            break
        time.sleep(0.05)
    time.sleep(0.2)
    check("cancelling a queued job skips it",
          queued.state == "cancelled" and not ran, f"{queued.state} ran={ran}")

    events = q.submit("test", lambda j: [j.emit({"type": "unit", "n": i}) for i in range(3)])
    for _ in range(100):
        if events.state in ("done", "failed"):
            break
        time.sleep(0.05)
    seqs = [e["seq"] for e in events.events]
    check("event sequence numbers increase", seqs == sorted(seqs) and len(set(seqs)) == len(seqs))
    check("replays only newer events",
          all(e["seq"] > 2 for e in events.since(2)))


def test_drive_preflight():
    print("\npipeline.check_drive_auth")
    from notesgen import pipeline

    empty = tmpdir()   # no google-credentials.json in here
    try:
        pipeline.check_drive_auth(("notes", "gdoc"), empty)
        check("refuses a Drive run with no credentials", False, "no error")
    except pipeline.PipelineError:
        check("refuses a Drive run with no credentials", True)

    try:
        pipeline.check_drive_auth(("notes", "html", "pdf"), empty)
        check("allows a local PDF without Google", True)
    except pipeline.PipelineError as exc:
        check("allows a local PDF without Google", False, str(exc))


def main():
    print("notesgen tests")
    for fn in (
        test_write_tree,
        test_write_tree_edges,
        test_importer,
        test_outputs_dependencies,
        test_zip_traversal,
        test_safe_file,
        test_job_queue,
        test_drive_preflight,
    ):
        fn()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
