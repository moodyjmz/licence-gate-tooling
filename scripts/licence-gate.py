#!/usr/bin/env python3
"""Licence gate: three checks over a PR diff.

  A  a modified file carrying a licence header must also carry a modification notice
  B  no line matching Copyright / Licensed under may be deleted or altered
  C  candidate replacement events, detected and listed for a human to disposition

A and B are mechanical and may block. C only ever prompts: it may say "this might be
a replacement", never "this isn't". A false prompt costs a reviewer seconds; a false
all-clear is a missing record nobody knows is missing - so C is tuned to over-detect,
and the reviewer's "not a replacement" is the cheap correction.

Usage:  licence-gate.py <base-sha> <head-sha>
Writes a markdown report to stdout and exits non-zero if A or B failed.
"""

import re
import subprocess
import sys

NOTICE = "Modified by the Example project."
# Deliberately narrow. An earlier, looser pattern matched any prose containing
# "copyright" or "licensed under" - which meant the gate flagged its own source and
# would flag any compliance document that quotes a licence line. A header states
# ownership in a recognisable form; prose about ownership does not.
HEADER_RE = re.compile(r"(copyright\s*(\(c\)|©|\d{4})|licensed under the)", re.I)

# Tooling and documentation discuss licences without being licensed material.
IGNORE_RE = re.compile(r"^(scripts/|\.github/|README\.md$|docs?/)")
ASSET_RE = re.compile(r"\.(svg|png|jpg|jpeg|gif|ico|dat|woff2?|ttf)$", re.I)
SOURCE_RE = re.compile(r"\.(js|ts|py|c|h|cpp|css|less|java|go|rb|sh)$", re.I)


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def changed_files(base, head):
    """Classify the diff.

    Renames matter more than they look. `git diff --name-status` reports a delete
    plus an add of similar content as a single `R<score>` line with TWO paths - and
    a rename is the commonest shape a replacement takes: swapping one asset for a
    differently-named one. An earlier version handled only A/D/M, so renames fell
    through silently and produced no candidate at all. Under-detection is the
    failure nobody notices, which is exactly why it is the one to guard against.
    """
    out = sh("git", "diff", "--name-status", f"{base}..{head}")
    added, modified, deleted, renamed = [], [], [], []
    for line in out.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") or status.startswith("C"):
            # R<score>\told\tnew
            if len(parts) >= 3:
                renamed.append((parts[1], parts[2]))
            continue
        path = parts[-1]
        if status.startswith("A"):
            added.append(path)
        elif status.startswith("D"):
            deleted.append(path)
        elif status.startswith("M"):
            modified.append(path)
    return added, modified, deleted, renamed


def check_b(base, head):
    """Deleted or altered licence/copyright lines. Returns [(path, line)]."""
    out = sh("git", "diff", "--unified=0", f"{base}..{head}")
    violations, path = [], None
    for line in out.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("-") and not line.startswith("---"):
            if path and IGNORE_RE.match(path):
                continue
            if HEADER_RE.search(line[1:]):
                violations.append((path, line[1:].strip()))
    return violations


def check_a(base, head, modified):
    """Modified files that have a licence header but no notice."""
    missing = []
    for p in modified:
        content = sh("git", "show", f"{head}:{p}")
        if not content:
            continue
        head_region = "\n".join(content.split("\n")[:40])
        if HEADER_RE.search(head_region) and NOTICE not in content:
            missing.append(p)
    return missing


def check_d(head, added):
    """New files that need a licence decision.

    Deliberately NOT auto-fixable, unlike check A. A modified file always needs the
    same notice, so a machine can write it. A new file needs a *copyright holder*,
    and that depends on where the content came from - which no diff can tell you.

    Stamping new files mechanically is how a vendored third-party asset ends up
    carrying your copyright. That is not a hypothetical: two icons added to a real
    repository, named as though they were first-party work, turned out to be
    unmodified Google Material Symbols. A rule that "new files get our header"
    would have asserted copyright over someone else's work, in a compliance
    programme whose whole purpose is not doing that.

    So this check blocks and asks. A human answers.
    """
    needing, assets = [], []
    for p in added:
        if ASSET_RE.search(p):
            assets.append(p)
        elif SOURCE_RE.search(p):
            content = sh("git", "show", f"{head}:{p}")
            if content and not HEADER_RE.search("\n".join(content.split("\n")[:40])):
                needing.append(p)
    return needing, assets


def check_c(added, modified, deleted, renamed):
    """Candidate replacement events. Over-detects by design."""
    cands = []
    for old, new in renamed:
        cands.append((old, f"renamed to `{new}` - the commonest shape of a replacement"))
    for p in deleted:
        cands.append((p, "file deleted"))
    for p in added:
        if ASSET_RE.search(p):
            cands.append((p, "asset added"))
    for p in modified:
        if ASSET_RE.search(p):
            cands.append((p, "asset modified"))
    return cands


def apply_fix(head, paths):
    """Append the notice to each file's existing header block. Add-only."""
    fixed = []
    for p in paths:
        try:
            content = open(p, encoding="utf-8").read()
        except OSError:
            continue
        if NOTICE in content:
            continue
        lines = content.split("\n")
        # insert before the line closing the comment block that holds the header
        for i, line in enumerate(lines[:40]):
            if HEADER_RE.search(line):
                for j in range(i, min(i + 40, len(lines))):
                    st = lines[j].strip()
                    if st in ("*/", "-->"):
                        pref = " * " if st == "*/" else "    * "
                        lines[j:j] = [f"{pref}{NOTICE}"]
                        open(p, "w", encoding="utf-8").write("\n".join(lines))
                        fixed.append(p)
                        break
                break
    return fixed


def main(argv):
    candidates_only = "--candidates" in argv
    fix_mode = "--fix" in argv
    argv = [a for a in argv if not a.startswith("--")]
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    base, head = argv
    added, modified, deleted, renamed = changed_files(base, head)

    if candidates_only:
        for p, _ in check_c(added, modified, deleted, renamed):
            print(p)
        return 0

    if fix_mode:
        missing = check_a(base, head, modified)
        for p in apply_fix(head, missing):
            print(p)
        return 0

    b = check_b(base, head)
    a = check_a(base, head, modified)
    c = check_c(added, modified, deleted, renamed)
    d_src, d_assets = check_d(head, added)

    out = []
    blocking = bool(a or b or d_src)

    if b:
        out.append(f"### Blocking — {len(b)} licence line(s) removed or altered\n")
        out.append("A licence has not changed, so no existing licence text may change. "
                   "Add lines; never edit or delete them.\n")
        out.append("**How to resolve:** restore the original line exactly, then add your "
                   "notice on a new line beneath it. This cannot be auto-fixed — only you "
                   "know what the line was meant to say.\n")
        for p, line in b:
            out.append(f"- `{p}`\n  ```\n  - {line}\n  ```")
        out.append("")

    if a:
        out.append(f"### Blocking — {len(a)} modified file(s) missing a notice\n")
        out.append(f"Each needs this line appended to its existing header block:\n")
        out.append(f"```\n * {NOTICE}\n```")
        out.append("**How to resolve:** comment `/auto-fix` on this pull request and it "
                   "will be applied for you, or add the line by hand. Anyone with write "
                   "access can trigger it — this is a mechanical fix, not a judgement.\n")
        for p in a:
            out.append(f"- `{p}`")
        out.append("")

    if d_src or d_assets:
        out.append("### New files — a licence decision is needed\n")
        out.append("**Not auto-fixed, deliberately.** A modified file always needs the same "
                   "notice, so a machine can add it. A new file needs a copyright holder, "
                   "and that depends on where the content came from — which no diff can "
                   "tell you. Stamping new files mechanically is how someone else's work "
                   "ends up carrying your copyright.\n")
        for p in d_src:
            out.append(f"- `{p}` — no licence header. Who wrote this, and which licence applies in this directory?")
        for p in d_assets:
            out.append(f"- `{p}` — asset added. Where did it come from? Check it is not a third-party icon set before claiming copyright.")
        out.append("\n**How to resolve:** add the correct header by hand. **`/auto-fix` "
                   "deliberately will not touch these** — the right copyright holder "
                   "depends on where the content came from, and guessing is how someone "
                   "else's work ends up carrying yours.\n")

    if c:
        out.append(f"### Needs your decision — {len(c)} candidate(s)\n")
        out.append("These *may* be replacement events. Detection over-reports on "
                   "purpose; deciding is a human judgement, so every one needs an "
                   "answer, including \"no\".\n")
        for p, why in c:
            out.append(f"- `{p}` — {why}")
        out.append("\n**How to resolve:** a reviewer — not the pull request author — "
                   "replies with a disposition for every path. Use a register ID where it "
                   "is a replacement, or `not a replacement` where it is not:\n")
        out.append("```\nexample-log:")
        for p, _ in c:
            out.append(f"  {p} -> ")
        out.append("```")
        out.append("")

    verified = ["no licence or copyright line deleted or altered"] if not b else []
    if not a:
        verified.append("every modified file with a header carries a notice")
    if verified:
        out.append("<details><summary>✅ Verified automatically — you need not check these</summary>\n")
        for v in verified:
            out.append(f"- {v}")
        out.append("\n</details>\n")

    out.append("<details><summary>⚠️ Not checked — these need a human</summary>\n")
    out.append("- whether each candidate above is genuinely a replacement")
    out.append("- whether a stated rationale is the real one")
    out.append("- where an added asset actually came from")
    out.append("\n</details>")

    print("\n".join(out))
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
