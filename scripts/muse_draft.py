"""Draft code with Muse Spark on Meta credits; Claude Code reviews it before anything is committed.

Workflow (AGENTS.md, "Muse drafts, Claude verifies"):
  1. Send Muse a task, the files it needs to see, and the project rules (AGENTS.md).
  2. Muse returns complete files. They are written to .muse_drafts/<timestamp>/ (gitignored),
     or straight into the working tree with --apply, where `git diff` shows them for review.
  3. Every call's tokens and estimated cost are appended to docs/MUSE_DEV_LOG.md.

Usage:
  python scripts/muse_draft.py --task "Write quantproof/ingest/html.py ..." \\
      --context quantproof/core/termsheet.py tests/conftest.py [--apply] [--model muse-spark-1.3]
  python scripts/muse_draft.py --task-file task.md --context ... --dry-run   # size only, no call
  python scripts/muse_draft.py --total                                        # dev spend so far

Muse output is untrusted: file paths are checked so nothing is written outside the repo, into
.git/, or to a .env file.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shlex
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from quantproof.agent import client  # noqa: E402
from quantproof.agent.costs import Usage  # noqa: E402

DEFAULT_MODEL = "muse-spark-1.3"
# Muse Spark reasons before answering and reasoning counts against max_tokens; complete files
# plus reasoning need room, and a cut-off reply wastes the whole call.
DEFAULT_MAX_TOKENS = 24000
TIMEOUT_S = 900
DEV_BUDGET_USD = 80.0
LOG_PATH = REPO / "docs" / "MUSE_DEV_LOG.md"
DRAFTS_DIR = REPO / ".muse_drafts"

FENCE_RE = re.compile(r"\A```[\w+-]*\n(?P<body>.*)\n```\s*\Z", re.S)


def make_tag() -> str:
    """A random tag for this call's block markers. Files may contain fixed markers as text (this
    script does), so fixed markers could split a file in two; a fresh tag cannot collide."""
    return secrets.token_hex(4)


def output_format(tag: str) -> str:
    return f"""\
Return every file you create or change in full (never a diff, a fragment or "unchanged"
placeholders), each as:

<<<FILE {tag} relative/path/from/repo/root.py>>>
...complete file content, no markdown fences...
<<<END {tag}>>>

After the files you may add one block of notes for the reviewer (assumptions, open questions,
anything you were unsure of):

<<<NOTES {tag}>>>
...
<<<END {tag}>>>

Use exactly the tag {tag} in every marker. Write nothing outside these blocks."""


def system_prompt(rules: str) -> str:
    """Stable text only (no per-call tag), so repeated calls can reuse the cached prefix."""
    return (
        "You are drafting code for QuantProof, a Python project. A reviewer (Claude Code) checks "
        "every line, runs the tests and fixes problems before anything is committed, so be "
        "correct and explicit rather than clever, and say in the notes where you are unsure.\n\n"
        "Follow these project rules exactly:\n\n"
        f"{rules}"
    )


def build_messages(task: str, context: dict[str, str], rules: str, tag: str) -> list[dict]:
    parts = [f"Task:\n{task.strip()}"]
    for path, text in context.items():
        parts.append(f"<<<CONTEXT {tag} {path}>>>\n{text.rstrip()}\n<<<END {tag}>>>")
    parts.append(output_format(tag))
    return [
        {"role": "system", "content": system_prompt(rules)},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def safe_path(path: str) -> PurePosixPath:
    """A repo-relative path from Muse, or ValueError. Rejects absolute paths, '..', .git and .env."""
    p = PurePosixPath(path.strip())
    if not p.parts or p.is_absolute() or ".." in p.parts or "\\" in path or ":" in path:
        raise ValueError(f"unsafe path from model: {path!r}")
    if p.parts[0] == ".git" or any(part == ".env" or part.startswith(".env.") for part in p.parts):
        raise ValueError(f"refusing to write {path!r}")
    return p


def parse_reply(text: str, tag: str) -> tuple[dict[str, str], str]:
    """Split a reply into {path: content} and the notes. A file whose block was cut off (no end
    marker) is not matched, so a truncated reply never produces a half-written file."""
    t = re.escape(tag)
    end = rf"\n?^<<<END {t}>>>[ \t]*$"
    file_re = re.compile(rf"^<<<FILE {t} (?P<path>[^\n>]+)>>>\n(?P<body>.*?){end}", re.M | re.S)
    notes_re = re.compile(rf"^<<<NOTES {t}>>>\n(?P<body>.*?){end}", re.M | re.S)
    files: dict[str, str] = {}
    for m in file_re.finditer(text):
        path = str(safe_path(m["path"]))
        body = m["body"]
        fenced = FENCE_RE.match(body)
        if fenced:  # the model wrapped the file in ``` despite being asked not to
            body = fenced["body"]
        files[path] = body.rstrip("\n") + "\n"
    notes = notes_re.search(text)
    return files, (notes["body"].strip() if notes else "")


def call_muse(messages: list[dict], model: str, max_tokens: int,
              urlopen: Callable = urllib.request.urlopen) -> client.ChatResult:
    """One streamed call through the product client (retries included). It is not written to
    the product's cost log: development calls are logged in MUSE_DEV_LOG.md instead."""
    try:
        return client.chat(messages, model=model, max_tokens=max_tokens, purpose="dev draft",
                           log_path=None, urlopen=urlopen)
    except client.MetaAPIError as e:
        raise SystemExit(f"Meta API error {e.status}: {e.detail}") from None


def run_pytest(args: list[str], runner: Callable = subprocess.run) -> tuple[bool, str]:
    """Run pytest with the given args plus fixed flags.

    Runs [sys.executable, "-m", "pytest", *args, "-x", "--tb=short", "-p",
    "no:cacheprovider"] in REPO, capturing stdout+stderr as text. Returns
    (passed, output) where output is the last 150 lines (to keep the prompt small).
    """
    cmd = [sys.executable, "-m", "pytest", *args, "-x", "--tb=short", "-p", "no:cacheprovider"]
    completed = runner(cmd, cwd=REPO, capture_output=True, text=True)
    text = (completed.stdout or "") + (completed.stderr or "")
    lines = text.splitlines()
    return (completed.returncode == 0, "\n".join(lines[-150:]))


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def append_log(log: Path, *, when: datetime, model: str, task: str, sent: list[str],
               returned: list[str], usage: Usage, cost: float, note: str = "") -> None:
    row = [
        when.strftime("%Y-%m-%d %H:%M"), model, _cell(task)[:160], _cell(", ".join(sent) or "-"),
        _cell(", ".join(returned) or "-"), f"{usage.input_tokens:,} ({usage.cached_tokens:,})",
        f"{usage.output_tokens:,} ({usage.reasoning_tokens:,})", f"${cost:.4f}", _cell(note) or "-",
    ]
    with log.open("a", encoding="utf-8") as f:
        f.write("| " + " | ".join(row) + " |\n")


def total_spend(log: Path) -> float:
    """Sum of the 'Est. cost' column. Rows without a dollar amount (e.g. notes) count as 0."""
    total = 0.0
    if not log.exists():
        return total
    for line in log.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        if len(cells) >= 8 and (m := re.fullmatch(r"\$(\d+(?:\.\d+)?)", cells[7])):
            total += float(m[1])
    return total


def write_files(files: dict[str, str], root: Path) -> list[Path]:
    written = []
    for rel, content in files.items():
        dest = (root / rel).resolve()
        if root.resolve() not in dest.parents:
            raise ValueError(f"{rel!r} escapes {root}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(dest)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--task", help="what to build (or use --task-file)")
    ap.add_argument("--task-file", type=Path)
    ap.add_argument("--context", nargs="*", default=[], help="repo files Muse should see")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--apply", action="store_true", help="write into the working tree for review")
    ap.add_argument("--dry-run", action="store_true", help="show prompt size; no API call")
    ap.add_argument("--total", action="store_true", help="print development spend so far")
    ap.add_argument("--pytest", default=None,
                    help='pytest args as one string, e.g. "tests/ingest -k edgar"; '
                         "run first and attach failures to the task")
    args = ap.parse_args(argv)

    if args.total:
        print(f"Development spend so far: ${total_spend(LOG_PATH):.4f} of ${DEV_BUDGET_USD:.0f}")
        return 0

    pytest_output = ""
    if args.pytest is not None:
        split_args = shlex.split(args.pytest)
        passed, pytest_output = run_pytest(split_args)
        if passed:
            print("Tests pass; nothing to fix.")
            return 0

    task = args.task_file.read_text(encoding="utf-8") if args.task_file else args.task
    if not task:
        if args.pytest is not None:
            task = "Make these tests pass."
        else:
            ap.error("give --task or --task-file")
    if args.pytest is not None:
        task = (task + "\n\nThe tests fail. Fix the code (not the tests, unless a test is "
                "clearly wrong; then say why in the notes). Failing output:\n```\n"
                + pytest_output + "\n```")

    context = {str(PurePosixPath(p)): (REPO / p).read_text(encoding="utf-8") for p in args.context}
    rules = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    tag = make_tag()
    messages = build_messages(task, context, rules, tag)
    chars = sum(len(m["content"]) for m in messages)
    print(f"Prompt: {chars:,} characters (~{chars // 4:,} tokens), {len(context)} context files")
    if args.dry_run:
        return 0

    spent = total_spend(LOG_PATH)
    if spent > DEV_BUDGET_USD:
        print(f"Development spend ${spent:.2f} is over the ${DEV_BUDGET_USD:.0f} budget; ask Joyeb.")
        return 2

    result = call_muse(messages, args.model, args.max_tokens)
    usage, cost, text, finish = result.usage, result.cost_usd, result.text, result.finish_reason

    # Parse before logging so the log records what came back, but log even if parsing fails:
    # the tokens were billed either way.
    try:
        files, notes = parse_reply(text, tag)
        problem = ""
    except ValueError as e:
        files, notes, problem = {}, "", str(e)
    if result.usage_estimated:
        problem = "API sent no usage; cost is an upper bound (all of max_tokens)"
    if finish != "stop":
        cut = "cut off at max_tokens" if finish == "length" else f"stream ended early ({finish})"
        problem = (problem + "; " if problem else "") + cut
    # Never overwrite an existing file Muse was not shown: hold it in the drafts folder instead.
    held = {f: c for f, c in files.items()
            if args.apply and f not in context and (REPO / f).exists()}
    if held:
        problem = (problem + "; " if problem else "") + (
            f"held (existing, not in --context): {', '.join(held)}")
    append_log(LOG_PATH, when=datetime.now(timezone.utc), model=args.model, task=task,
               sent=list(context), returned=list(files), usage=usage, cost=cost,
               note=problem or "awaiting review")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = DRAFTS_DIR / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "reply.md").write_text(text, encoding="utf-8")
    (out / "meta.json").write_text(json.dumps(
        {"model": args.model, "finish_reason": finish, "seconds": round(result.seconds, 1),
         "attempts": result.attempts, "usage": asdict(usage), "estimated_cost_usd": round(cost, 6)},
        indent=2))
    if args.apply:
        write_files({f: c for f, c in files.items() if f not in held}, REPO)
        write_files(held, out / "held")
    else:
        write_files(files, out / "files")

    print(f"{args.model}: {result.seconds:.0f} s, {usage.input_tokens:,} in ({usage.cached_tokens:,} cached), "
          f"{usage.output_tokens:,} out ({usage.reasoning_tokens:,} reasoning), ~${cost:.4f}")
    print(f"Files: {', '.join(files) or 'none'} -> {'working tree' if args.apply else out / 'files'}")
    if notes:
        print(f"Notes from Muse:\n{notes}")
    if problem:
        print(f"Problem: {problem}. Raw reply saved in {out / 'reply.md'}")
    print(f"Development spend so far: ${total_spend(LOG_PATH):.4f} of ${DEV_BUDGET_USD:.0f}")
    return 1 if problem else 0


if __name__ == "__main__":
    raise SystemExit(main())
