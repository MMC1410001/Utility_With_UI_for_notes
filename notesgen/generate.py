"""Generate per-lecture notes, section rollups, and the course index."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import engine, events, prompts
from .providers import MissingCredential, ProviderUnavailable
from .discover import STATUS_NO_TRANSCRIPT, Lecture, Section
from .glossary import Glossary
from .manifest import Manifest

FRONT_MATTER = "# {label}\n\n*{course} - Section {section_idx}: {section_title}*\n\n"


def md_path(md_root: Path, lecture: Lecture) -> Path:
    return md_root / lecture.path.parent.name / f"{lecture.path.stem}.md"


def rollup_path(md_root: Path, section: Section) -> Path:
    return md_root / f"{section.idx}-{_safe(section.title)}" / "_section-overview.md"


def _safe(name: str) -> str:
    return "".join(c for c in name if c not in '/\\:*?"<>|').strip()


def generate_lectures(
    course: str,
    sections: list[Section],
    md_root: Path,
    manifest: Manifest,
    glossary: Glossary,
    *,
    model: str = "sonnet",
    workers: int = 3,
    force: bool = False,
    progress: events.Progress | None = None,
    should_cancel: events.ShouldCancel | None = None,
) -> dict:
    jobs: list[tuple[Section, Lecture, int]] = []
    stats = {"generated": 0, "skipped": 0, "stubbed": 0, "failed": 0}

    for section in sections:
        for pos, lec in enumerate(section.lectures, start=1):
            out = md_path(md_root, lec)
            if not force and manifest.is_current(lec.slug, lec.body_hash()):
                stats["skipped"] += 1
                continue

            # Never spend a model call on a lecture whose captions failed.
            # A confident-looking page built from 22 words is worse than an
            # honest gap.
            if lec.status == STATUS_NO_TRANSCRIPT:
                _write(out, _header(course, section, lec)
                       + prompts.NO_TRANSCRIPT_STUB.format(words=lec.words))
                manifest.record(
                    lec.slug,
                    hash=lec.body_hash(),
                    output=str(out.relative_to(md_root.parent)),
                    status=STATUS_NO_TRANSCRIPT,
                    cost_usd=0.0,
                )
                stats["stubbed"] += 1
                events.unit(progress, "lectures", stats["stubbed"], 0,
                            lec.label, stubbed=True)
                continue

            jobs.append((section, lec, pos))

    if not jobs:
        return stats

    print(f"  {len(jobs)} lecture(s) to generate, {stats['skipped']} already current")
    events.stage(progress, "lectures", "start", total=len(jobs))

    def work(job):
        section, lec, pos = job
        corrected, hits = glossary.apply(lec.body)
        prompt = prompts.lecture_prompt(
            course, section, lec, pos, len(section.lectures), corrected
        )
        result = engine.call(prompt, model=model)
        return job, result, sum(hits.values())

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, j): j for j in jobs}
        done = 0
        for fut in as_completed(futures):
            section, lec, _ = futures[fut]
            done += 1
            events.check(should_cancel)
            try:
                _, result, n_fixed = fut.result()
            except (MissingCredential, ProviderUnavailable):
                # Configuration, not content: every remaining lecture would
                # fail the same way, so stop now with the actionable message.
                pool.shutdown(cancel_futures=True)
                raise
            except Exception as exc:  # noqa: BLE001 - one bad lecture must not stop 182 good ones
                stats["failed"] += 1
                print(f"  [{done}/{len(jobs)}] FAILED {lec.label}: {exc}")
                events.unit(progress, "lectures", done, len(jobs), lec.label,
                            failed=str(exc))
                continue

            out = md_path(md_root, lec)
            _write(out, _header(course, section, lec) + result.text + "\n")
            manifest.record(
                lec.slug,
                hash=lec.body_hash(),
                output=str(out.relative_to(md_root.parent)),
                status="ok",
                words=lec.words,
                glossary_fixes=n_fixed,
                cost_usd=result.cost_usd,
                duration_ms=result.duration_ms,
            )
            stats["generated"] += 1
            print(f"  [{done}/{len(jobs)}] {lec.label}  ({n_fixed} term fixes)")
            events.unit(progress, "lectures", done, len(jobs), lec.label,
                        cost_usd=result.cost_usd)

    return stats


def generate_rollups(
    course: str,
    sections: list[Section],
    md_root: Path,
    manifest: Manifest,
    *,
    model: str = "sonnet",
    workers: int = 3,
    force: bool = False,
    progress: events.Progress | None = None,
    should_cancel: events.ShouldCancel | None = None,
) -> dict:
    stats = {"generated": 0, "skipped": 0, "stubbed": 0, "failed": 0}
    jobs = []

    for section in sections:
        notes, digest = _collect_section_notes(md_root, section)
        if not notes.strip():
            continue
        key = f"__rollup__/{section.idx}"
        if not force and manifest.is_current(key, digest):
            stats["skipped"] += 1
            continue

        # A section whose every lecture lost its captions has nothing to roll
        # up. Asking the model would only spend a call to be told so.
        if all(l.status == STATUS_NO_TRANSCRIPT for l in section.lectures):
            out = rollup_path(md_root, section)
            _write(out, f"# Section {section.idx}: {section.title}\n\n"
                        + prompts.EMPTY_SECTION_STUB)
            manifest.record(
                key,
                hash=digest,
                output=str(out.relative_to(md_root.parent)),
                status=STATUS_NO_TRANSCRIPT,
                cost_usd=0.0,
            )
            stats["stubbed"] = stats.get("stubbed", 0) + 1
            continue

        jobs.append((section, notes, digest, key))

    if not jobs:
        return stats

    print(f"  {len(jobs)} section rollup(s) to generate")
    events.stage(progress, "rollups", "start", total=len(jobs))

    def work(job):
        section, notes, digest, key = job
        prompt = prompts.section_rollup_prompt(course, section, notes)
        return job, engine.call(prompt, model=model)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, j): j for j in jobs}
        done = 0
        for fut in as_completed(futures):
            section, _, digest, key = futures[fut]
            done += 1
            events.check(should_cancel)
            try:
                _, result = fut.result()
            except (MissingCredential, ProviderUnavailable):
                pool.shutdown(cancel_futures=True)
                raise
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                print(f"  FAILED rollup {section.idx}: {exc}")
                events.unit(progress, "rollups", done, len(jobs),
                            f"Section {section.idx}", failed=str(exc))
                continue
            out = rollup_path(md_root, section)
            _write(out, f"# Section {section.idx}: {section.title}\n\n" + result.text + "\n")
            manifest.record(
                key,
                hash=digest,
                output=str(out.relative_to(md_root.parent)),
                status="ok",
                cost_usd=result.cost_usd,
            )
            stats["generated"] += 1
            print(f"  rollup {section.idx}-{section.title}")
            events.unit(progress, "rollups", done, len(jobs),
                        f"{section.idx} {section.title}", cost_usd=result.cost_usd)

    return stats


def generate_course_index(
    course: str,
    sections: list[Section],
    md_root: Path,
    manifest: Manifest,
    *,
    model: str = "sonnet",
    force: bool = False,
    progress: events.Progress | None = None,
) -> bool:
    import hashlib

    parts = []
    for section in sections:
        p = rollup_path(md_root, section)
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    if not parts:
        return False

    blob = "\n\n".join(parts)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    key = "__course_index__"
    if not force and manifest.is_current(key, digest):
        print("  course index already current")
        return False

    events.stage(progress, "index", "start", total=1)
    result = engine.call(prompts.course_index_prompt(course, blob), model=model)
    out = md_root / "_course-index.md"
    _write(out, f"# {course}\n\n" + result.text + "\n")
    manifest.record(
        key,
        hash=digest,
        output=str(out.relative_to(md_root.parent)),
        status="ok",
        cost_usd=result.cost_usd,
    )
    print("  course index")
    events.unit(progress, "index", 1, 1, "course index", cost_usd=result.cost_usd)
    return True


def _collect_section_notes(md_root: Path, section: Section) -> tuple[str, str]:
    import hashlib

    chunks = []
    for lec in section.lectures:
        p = md_path(md_root, lec)
        if p.exists():
            chunks.append(p.read_text(encoding="utf-8"))
    blob = "\n\n---\n\n".join(chunks)
    return blob, hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _header(course: str, section: Section, lec: Lecture) -> str:
    return FRONT_MATTER.format(
        label=lec.label,
        course=course,
        section_idx=section.idx,
        section_title=section.title,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
