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
import sys

from git_io import GateError, changed_files, git_show
from licence_map import (has_licence_header, leading_comment_lines,
                         leading_comment_region)

NOTICE = "Modified by the Example project."

# Tooling and documentation discuss licences without being licensed material.
IGNORE_RE = re.compile(r"^(scripts/|\.github/|README\.md$|docs?/)")
SOURCE_RE = re.compile(r"\.(js|ts|py|c|h|cpp|css|less|java|go|rb|sh)$", re.I)
# Text that is not licensed material in its own right and is not a replaceable asset.
TEXT_RE = re.compile(r"\.(md|txt|json|ya?ml|toml|ini|cfg|lock)$|^[^.]+$", re.I)


def is_asset(path):
    """Anything that is neither source nor plain text is treated as an asset.

    This used to be a list of extensions - svg, png, ico, woff, ttf and so on - which
    fails by omission every time somebody invents a file format. `.otf` and `.webp`
    were both missing, so a font swap and an image swap were neither assets nor
    source: they fell through every check and the gate reported "no replacement
    candidates detected", in green, on two new third-party binaries.

    Inverted, the failure mode moves to the safe side. An unrecognised extension now
    raises a candidate a reviewer dismisses in seconds, rather than passing silently.
    """
    return not (SOURCE_RE.search(path) or TEXT_RE.search(path))


def needs_provenance(path):
    """True when a NEWLY ADDED path needs a human to say where it came from.

    is_asset() plus the extensionless case, which fell off the end of check_d with no
    branch taken at all. TEXT_RE's `^[^.]+$` classifies a path with no extension as
    plain text, so `VENDORTOOL` was not an asset, did not match SOURCE_RE, and was
    therefore never listed as needing a decision, never raised as a candidate, and
    never even mentioned under "not checked". A newly added dotless binary - a
    vendored tool, a font renamed without its suffix - was simply invisible. That is
    a false all-clear on unrecorded third-party content, which is the one thing this
    tool exists to prevent.

    Routed to the asset path rather than the blocking path, because that is the safe
    and resolvable direction: there may be nowhere to put a header in it, so blocking
    would be unresolvable, while a candidate costs a reviewer one line of "not a
    replacement". No carve-out for `LICENSE` or `Makefile`. A fresh allow-list of
    known-harmless names is the same shape as the extension allow-list this file
    already threw out for failing by omission, and a LICENSE file appearing in a
    licence-compliance repository is a thing a person should look at anyway.

    Scoped to ADDITIONS. An extensionless file that is merely modified is not
    invisible - check_a and check_b both read it - so widening is_asset() itself would
    only add a candidate on every edit of a Makefile, and a gate that prompts on
    routine edits is a gate people learn to click past.

    Tested on the BASENAME, not the whole path. `^[^.]+$` fails on `src/v1.2/tool`,
    which has a dot in a directory name and still no extension.
    """
    return is_asset(path) or "." not in path.rsplit("/", 1)[-1]


def both_ends_ignorable(old_path, new_path):
    """True when EVERY end of a change is a path whose licence text we do not track.

    Ignoring on the new path alone let a rename launder an edit: move a licensed file
    into docs/ in the same commit that rewrites its copyright holder, and the removed
    line is skipped as documentation.
    """
    ends = [p for p in (old_path, new_path) if p]
    return bool(ends) and all(IGNORE_RE.match(p) for p in ends)


def looks_binary(text):
    """Whether this blob is bytes rather than lines.

    A NUL byte is the test git itself uses, and it is the one that matters: text files
    do not contain them. U+FFFD is counted too because blobs are read with
    errors="replace", so undecodable bytes arrive as replacement characters rather
    than as an exception - a run of those is a binary that decoded quietly.
    """
    if "\x00" in text:
        return True
    return text.count("\ufffd") > max(8, len(text) // 200)


def check_b(base, head, pairs):
    """Deleted or altered licence/copyright lines. Returns [(path, line)].

    `pairs` is [(old_path, new_path, old_is_gitlink, new_is_gitlink)] taken from git's
    own file enumeration, modes included.

    NOTHING HERE PARSES DIFF TEXT, and that is the point. Two of the defects found in
    this file existed only because it used to read structure off `git diff` output:

      * paths were taken from `--- a/…` / `+++ b/…` lines, which can be forged from
        file CONTENT - a line reading `-- a/docs/decoy` becomes `--- a/docs/decoy`
        once the removal marker is prepended. Two of those above the real edit
        repointed the parser at `docs/`, an ignored path, and the copyright rewrite
        below was skipped: exit 0, with the report stating no copyright line had been
        altered. It had been; the holder was replaced outright.
      * then, one layer down, skipping any line starting `---` ate content: a deleted
        SQL, Lua, Haskell or Ada comment starts `--`, arrives as `--- …`, and was read
        as a file header. Deleting a copyright line from a .sql file passed.

    Both were bugs in a hand-rolled state machine over a format whose escaping is not
    ours to control. The question this check actually answers does not need that
    format at all: does every header-shaped line in the base blob still appear in the
    head blob? Both blobs are one `git show` away, and neither has any syntax layered
    on top of it. A parser that does not exist has no parsing bugs.

    THE TRADE-OFF, stated because it is a real loss: line-set membership is coarser
    than positional diffing. A header line that is merely REFLOWED - moved up or down,
    or duplicated - stops being reported, because the text still appears somewhere.
    And a line whose whitespace changed reads as "deleted" rather than "changed",
    since membership is on exact text. The second is a false positive and the first is
    a narrowing; the narrowing is bounded (the line is still literally present in the
    file, byte for byte) while the over-report is the direction this tool has chosen
    everywhere else. Comparison is exact, NOT stripped: stripping would make a
    whitespace-altered line read as unchanged, which is narrowing detection to make
    something pass.

    BINARY BLOBS ARE ROUTED TO THE REVIEWER, NOT BLOCKED HERE. Removing the diff
    parser meant binaries started reaching this check - `git diff` used to say
    `Binary files … differ` - and a TrueType `name` table carries
    `Copyright (c) 2011 Example Foundry` as plain ASCII, which survives the tolerant
    decode. So a font swap blocked here, with advice ("restore the original line
    exactly") that cannot be followed inside a .woff2.

    This check answers "was a LINE removed or edited". A binary has no lines anyone
    edits: its embedded copyright changes because the whole asset was replaced, which
    is a different event with a different remedy. Reporting it here adds nothing that
    "this asset changed" did not already say, and says it in a form nobody can act on
    - which is how a gate gets switched off.

    So this is ROUTING, not narrowing, and it is routing only because of the guarantee
    below: every binary skipped here is raised as a candidate by check_c, whatever its
    extension, and a reviewer must disposition it. If that guarantee ever stops
    holding, this becomes a hole. `binary_skips` is returned for exactly that reason -
    the caller feeds it to check_c rather than trusting the two to agree.
    """
    violations, binary_skips = [], []
    for old_path, new_path, old_is_link, new_is_link in pairs:
        if both_ends_ignorable(old_path, new_path):
            continue
        # WHICH END has no blob decides what happens, and getting this symmetrical was
        # a bug caught before it shipped: skipping a pair because the PATH appeared in
        # the union `gitlinks` set made replacing a licensed file with a submodule
        # pointer - one path, git calls it T - pass in silence. The base blob is
        # perfectly readable there; only the head end is a gitlink. That is the
        # file-to-symlink failure one mode along, and the diff-text version this
        # replaced caught it, so the symmetrical skip was weaker than the bug.
        #
        # Recognised BY MODE throughout, never by watching `git show` fail: that is how
        # submodules became invisible to every check in the first place.
        if old_path is None or old_is_link:
            # Nothing was here to delete from: a pure addition, or a submodule pointer
            # whose content has never lived in this repository. check_c and check_d
            # raise both for a human.
            continue
        before = read_at(base, old_path, "the base version of")
        # Decided by CONTENT, not extension. An extension list is what made .otf and
        # .webp invisible once already, and the question here is whether the blob has
        # lines at all - which its name cannot answer.
        if looks_binary(before):
            binary_skips.append(new_path or old_path)
            continue
        # No head blob means every line of the base is gone, which is the truth and the
        # over-detecting direction. Both cases are real: an ordinary `git rm`, and a
        # licensed file overwritten by a gitlink at the same path. Asking `git show`
        # for either would turn a deletion into "the gate could not run".
        after = ("" if new_path is None or new_is_link
                 else read_at(head, new_path, "the modified file"))
        present = set(after.split("\n"))
        for line in before.split("\n"):
            if has_licence_header(line) and line not in present:
                violations.append((old_path, line.strip()))
    return violations, binary_skips


def read_at(rev, path, what):
    """`git show rev:path`, or a GateError naming the file it could not read.

    Everything that reads a file the diff named comes through here, because the
    alternative - `content = sh(...)` followed by `if content and ...` - cannot tell
    a genuinely empty file from a `git show` that failed. It read both as "nothing to
    check", and the second one is the gate skipping a file it was asked to judge.

    Entries that are not files at all are filtered out before this point, by mode,
    so a failure reaching here means git could not produce a blob it should have
    been able to produce. That is not a clean result and must not be reported as one.
    """
    content = git_show(rev, path)
    if content is None:
        raise GateError(f"could not read {what} `{path}` at {rev[:9]}; "
                        f"nothing about this file has been checked")
    return content


def check_a(base, head, modified, base_paths=None, gitlinks=()):
    """Modified files that have a licence header but no notice.

    The header is looked for in the file's LEADING COMMENT BLOCK, not in a fixed
    number of lines. A fixed 40-line window was defeated by inserting 45 lines of
    filler comment above the header: the gate then saw no header, demanded no notice,
    and told the reviewer in its verified box that every modified file with a header
    carried one. See licence_map.leading_comment_region for why the whole file is not
    scanned instead.
    """
    missing = []
    for p in modified:
        # Tooling and documentation are excluded here exactly as they are in check_b
        # and check_c. Without it, a README whose first line reads "# Copyright (c)
        # 2020 Example Corp" - an ordinary thing to write - is demanded a modification
        # notice on every edit, and /auto-fix cannot supply one because a Markdown file
        # has no comment block to close. That blocks a pull request on a requirement
        # nothing can satisfy, which is how a gate loses its audience.
        if IGNORE_RE.match(p):
            continue
        # A submodule has no content in this repository, so there is nowhere to put a
        # notice and nothing to read. It is not silently dropped: check_c and check_d
        # raise it, which is where "where did this content come from?" belongs.
        if p in gitlinks:
            continue
        content = read_at(head, p, "the modified file")
        # Ask the BASE as well as the head. Reading only the head meant one prepended
        # line of code above an existing header ended the leading comment region before
        # the header, so the gate decided the file had none and demanded no notice -
        # a one-line bypass, easier than the 45-line one this replaced. What matters is
        # whether the file carried a header before the change, and it is the base that
        # answers that. A header cannot be escaped by being pushed out of view.
        # A renamed file does not exist at its new path in the base, so ask for the
        # name it had there.
        before = read_at(base, (base_paths or {}).get(p, p), "the base version of")
        had_header = has_licence_header(leading_comment_region(before))
        has_header_now = has_licence_header(leading_comment_region(content))
        if (had_header or has_header_now) and NOTICE not in content:
            missing.append(p)
    return missing


def check_d(head, added, gitlinks=()):
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

    Source files block, because a header can be added to one. Binary assets do not:
    there is nowhere to put a header in a .otf, so blocking would be unresolvable.
    They raise a candidate instead, and the acknowledgement gate requires a reviewer
    to disposition every candidate - which is where "where did this come from?" gets
    answered by a person.

    A submodule is neither. It carries content this repository does not hold and did
    not write - the definition of the thing this tool exists to record - and it takes
    the FIRST branch, before any extension is looked at. Deciding by extension is what
    hid it: a gitlink named `vendor/thing.py` matched SOURCE_RE, and a gitlink named
    `vendor/thing` matched TEXT_RE's no-extension rule, so one was read as an empty
    source file and the other fell through every branch. Neither is a file at all, and
    the name never had anything to say about it.

    Returns (needing, assets, links).
    """
    needing, assets, links = [], [], []
    for p in added:
        if p in gitlinks:
            links.append(p)
        elif needs_provenance(p):
            assets.append(p)
        elif SOURCE_RE.search(p):
            content = read_at(head, p, "the new file")
            if not has_licence_header(leading_comment_region(content)):
                needing.append(p)
    return needing, assets, links


def check_c(added, modified, deleted, renamed, gitlinks=(), binary_skips=()):
    """Candidate replacement events. Over-detects by design."""
    cands = []
    for old, new in renamed:
        cands.append((old, f"renamed to `{new}` - the commonest shape of a replacement"))
    for p in deleted:
        cands.append((p, "file deleted"))
    for p in added:
        # needs_provenance rather than is_asset: an extensionless addition raised no
        # candidate here either. `and p not in gitlinks` is load-bearing, not tidiness
        # - the dedup below runs AFTER this loop, so a gitlink admitted here as "asset
        # added" would suppress the submodule reason string that tells the reviewer
        # the content is in another repository entirely.
        if needs_provenance(p) and p not in gitlinks:
            cands.append((p, "asset or extensionless file added"))
    for p in modified:
        if is_asset(p) and p not in gitlinks:
            cands.append((p, "asset modified"))
    # Every gitlink, whatever its name and whatever git called the change. A submodule
    # is a pointer at a tree in another repository, which is unrecorded vendored
    # content by definition, and a bump silently swaps all of it. It reached no list
    # and raised no candidate at all, so the acknowledgement gate reported "No
    # replacement candidates detected" - a false all-clear on the one thing this tool
    # is for.
    seen = {p for p, _ in cands}
    for p in sorted(gitlinks):
        if p not in seen:
            cands.append((p, "submodule (gitlink) added or changed - its content lives "
                             "in another repository and is not in this diff"))
    # check_b skips binary blobs: they have no lines to remove, so a hit there says
    # nothing that "this asset changed" did not, in a form nobody can act on. That is
    # only routing rather than narrowing if every one of them reaches a reviewer here,
    # so the skipped paths are passed in and added explicitly instead of trusting the
    # extension rules to have covered them. A binary named .js would otherwise be
    # skipped by check_b and classified as source by check_c, and vanish between them.
    seen = {p for p, _ in cands}
    for p in binary_skips:
        if p not in seen:
            cands.append((p, "binary content changed - no line-level check is possible, "
                             "so the whole asset needs your decision"))
    return cands


def _indent_of(line):
    return line[:len(line) - len(line.lstrip())]


def _eol_of(line):
    """The carriage return a CRLF file leaves on the end of each split line.

    Lines are split on "\\n" and left otherwise untouched, so a file's existing bytes
    survive whatever we do to it; an inserted line copies its neighbour's ending.
    Reading with universal newlines instead - the default - silently translated every
    CRLF to LF on the way in and wrote LF back out, so adding one notice line to a
    CRLF file rewrote every line in it, and /auto-fix committed and pushed that.
    """
    return "\r" if line.endswith("\r") else ""


def insert_notice(lines, region, anchor):
    """Put the notice inside the comment block the header is already in.

    Returns new lines, or None when the block cannot be closed and guessing would
    be the only alternative. Refusing is reported loudly; writing a guess is not.

    The style is taken from the scanner's record of what the anchor line IS, not
    re-derived from how it happens to start. Re-deriving it is what corrupted files:
    the old code looked for a closer ALONE on its own line, and an ordinary header
    closing on the same line as its last text - ` * Licensed under X */` - matched
    nothing, fell through to the line-comment branch, whose marker pattern matched the
    body's leading `*`, and appended the notice AFTER the `*/`. Outside the comment.
    As a bare statement. `node --check` rejects the result, and /auto-fix holds
    contents:write and pushed it unreviewed.
    """
    lines = list(lines)
    if anchor.kind == "block":
        closer = next((r for r in region
                       if r.index >= anchor.index and r.kind == "block"
                       and r.token == anchor.token and r.closes_at is not None), None)
        if closer is None:
            return None
        line = lines[closer.index]
        indent = _indent_of(line)
        if closer.token == "*/":
            # Match the block's own body lines: ` * text` under a `/*`. Taken from the
            # closer's indent, so an indented block stays indented - and a closer that
            # is itself the opener (`/* Copyright ... */`, one line) still gets a
            # continuation marker rather than none.
            prefix = indent + ("* " if line.lstrip().startswith("*") else " * ")
        else:
            prefix = indent + "  "
        notice = f"{prefix}{NOTICE}{_eol_of(line)}"
        before = line[:closer.closes_at].rstrip()
        if before.strip():
            # The closer shares its line with the last line of header text, which is
            # ordinary style and was the corrupting case. Split it: text, notice, then
            # the closer on its own line. Inserting a line above this one would have
            # put the notice before the text it follows; inserting below it would put
            # the notice outside the comment, which is the bug being fixed.
            lines[closer.index:closer.index + 1] = [
                before + _eol_of(line), notice, indent + line[closer.closes_at:]]
        else:
            lines[closer.index:closer.index] = [notice]
        return lines
    if anchor.kind == "line":
        # Extend the run of header lines by one, in the marker the file already uses.
        # The run is bounded by the leading comment region and stops at the first line
        # that is not the same kind of comment, so it cannot walk out of the header
        # block, into a `*/` that its own `startswith` test used to match, or off the
        # end of a file that is nothing but comments.
        in_region = {r.index: r for r in region}
        last = anchor.index
        while True:
            nxt = in_region.get(last + 1)
            if nxt is None or nxt.kind != "line" or nxt.token != anchor.token:
                break
            last += 1
        line = lines[anchor.index]
        notice = f"{_indent_of(line)}{anchor.token} {NOTICE}{_eol_of(lines[last])}"
        lines[last + 1:last + 1] = [notice]
        return lines
    return None


def apply_fix(head, paths):
    """Append the notice to each file's existing header block. Add-only.

    Returns (fixed, unfixable). The second list matters: this only knew how to insert
    before a `*/` or `-->`, which are the closing tokens of block comments. Nine of the
    eleven extensions this tool stamps headers for use LINE comments - # for Python,
    shell and Ruby, // for JavaScript, C, Java and Go - which have no closing token at
    all. For every one of those the search fell through, the file was left untouched,
    and nothing was recorded. The gate blocked the pull request, told the reviewer to
    run /auto-fix, and /auto-fix then replied "Nothing to auto-fix". A remedy that
    quietly does nothing is worse than no remedy: the reviewer has been given a reason
    to stop looking.
    """
    fixed, unfixable = [], []
    for p in paths:
        try:
            # newline="" on both ends: see _eol_of. Nothing here may change a byte it
            # was not asked to change, and a line-ending translation changes all of
            # them.
            with open(p, encoding="utf-8", newline="") as fh:
                content = fh.read()
        except (OSError, UnicodeDecodeError):
            unfixable.append(p)
            continue
        if NOTICE in content:
            continue
        lines = content.split("\n")
        # The anchor must be IN the leading comment region. The old search ran over
        # `max(region, 40)` lines, so a file whose region holds no header still got
        # searched 40 lines deep and anchored on whatever looked like one - a licence
        # line inside a string literal or a heredoc, which is program data, not a
        # comment. The notice was written into the middle of it and pushed. This is
        # not narrowing detection: check_a decides whether a file needs a notice, and
        # a file it flags that this cannot place a notice in is reported unfixable,
        # loudly, for a human. A remedy may refuse; it may not guess.
        region = leading_comment_lines(content)
        anchor = next((r for r in region if has_licence_header(lines[r.index])), None)
        fixed_lines = insert_notice(lines, region, anchor) if anchor else None
        if fixed_lines is None:
            unfixable.append(p)
            continue
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(fixed_lines))
        fixed.append(p)
    return fixed, unfixable


def main(argv, author=None):
    author = next((a.split("=", 1)[1] for a in argv if a.startswith("--author=")), author)
    candidates_only = "--candidates" in argv
    fix_mode = "--fix" in argv
    argv = [a for a in argv if not a.startswith("--")]
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    base, head = argv
    added, modified, deleted, renamed, pairs, gitlinks = changed_files(base, head)
    # A rename is reported INSTEAD of a modification, never alongside it, so a file
    # renamed and edited in one commit never reached check_a - it kept its header,
    # gained content, needed a notice, and the report said every modified file with a
    # header carried one.
    modified_for_a = modified + [new for _, new in renamed]
    base_paths = {p: p for p in modified}
    base_paths.update({new: old for old, new in renamed})

    if candidates_only:
        _, binary_skips = check_b(base, head, pairs)
        for p, _ in check_c(added, modified, deleted, renamed, gitlinks, binary_skips):
            print(p)
        return 0

    if fix_mode:
        missing = check_a(base, head, modified_for_a, base_paths, gitlinks)
        fixed, unfixable = apply_fix(head, missing)
        for p in fixed:
            print(p)
        for p in unfixable:
            # Loudly, on stderr: the alternative is the action reporting "nothing to
            # auto-fix" about a file the gate is actively blocking on.
            print(f"could not auto-fix: {p}", file=sys.stderr)
        return 0 if not unfixable else 3

    b, binary_skips = check_b(base, head, pairs)
    a = check_a(base, head, modified_for_a, base_paths, gitlinks)
    c = check_c(added, modified, deleted, renamed, gitlinks, binary_skips)
    d_src, d_assets, d_links = check_d(head, added, gitlinks)

    out = []
    blocking = bool(a or b or d_src)

    # WHAT DO I DO NOW. This block exists because the report failed the only test that
    # matters: the person who built the gate opened a pull request, read the report,
    # and said "I don't know how to resolve this". Every fact needed was present and
    # none of it was usable. Three things were wrong, and all three are fixed here.
    #
    #   * nothing said what was actually BLOCKING. A section headed "a licence decision
    #     is needed", carrying its own "How to resolve", sat above a check that had
    #     already passed - so the reader could not tell which of the two sections was
    #     the one stopping the merge.
    #   * advisory and blocking sections were formatted identically, so "you may want
    #     to" and "this will not merge until" looked exactly alike.
    #   * the disposition instruction was addressed to whoever was reading, but a
    #     disposition from the AUTHOR is refused by design. The report told the one
    #     person reading it to do the one thing they are barred from doing, and never
    #     mentioned the bar.
    #
    # A gate that cannot say what to do next is a gate people route around, and being
    # routed around looks identical to working.
    todo = []
    if b:
        todo.append(("anyone with write access",
                     "restore the {} this pull request removed or altered — by hand; "
                     "this cannot be automated".format(
                         "licence line" if len(b) == 1 else
                         "{} licence lines".format(len(b)))))
    if a:
        todo.append(("anyone with write access, the author included",
                     "add the modification notice to {} — comment `/auto-fix` and it "
                     "is done for you".format(
                         "1 file" if len(a) == 1 else "{} files".format(len(a)))))
    if d_src:
        todo.append(("anyone with write access",
                     "give {} a licence header — by hand, since only a person knows "
                     "where the content came from".format(
                         "1 new file" if len(d_src) == 1 else
                         "{} new files".format(len(d_src)))))
    if c:
        subject = ("the candidate below" if len(c) == 1
                   else "every one of the {} candidates below".format(len(c)))
        todo.append(("a reviewer" + (", NOT @" + author if author else ", not the author"),
                     "answer {}, using the block at the end of this comment".format(subject)))

    if todo:
        out.append("### What has to happen before this merges\n")
        for i, (who, what) in enumerate(todo, 1):
            out.append("{}. **{}** — {}".format(i, who, what))
        out.append("")
        if c and author:
            out.append("> @{} opened this pull request, so a disposition from them is "
                       "refused — the whole value of the record is that a second person "
                       "looked. Anyone else with write access can give it.\n".format(author))
    else:
        out.append("### Nothing to do — both checks pass\n")
        out.append("Anything below is for information and does not block the merge.\n")

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

    if d_src or d_assets or d_links:
        out.append("### New files — worth a look, does not block\n")
        out.append("**Not auto-fixed, deliberately.** A modified file always needs the same "
                   "notice, so a machine can add it. A new file needs a copyright holder, "
                   "and that depends on where the content came from — which no diff can "
                   "tell you. Stamping new files mechanically is how someone else's work "
                   "ends up carrying your copyright.\n")
        for p in d_src:
            out.append(f"- `{p}` — no licence header. Who wrote this, and which licence applies in this directory?")
        for p in d_assets:
            out.append(f"- `{p}` — asset added. Where did it come from? Check it is not a third-party icon set before claiming copyright.")
        for p in d_links:
            out.append(f"- `{p}` — submodule added. Its content is in another repository "
                       f"and is not in this diff: what is it, who wrote it, and under "
                       f"which licence is it being vendored?")
        out.append("\n`/auto-fix` deliberately will not touch these: the right "
                   "copyright holder depends on where the content came from, and "
                   "guessing is how someone else's work ends up carrying yours. Add a "
                   "header by hand if one is wanted — **none of this blocks the "
                   "merge.**\n")

    if c:
        out.append(f"### A reviewer must answer these — {len(c)} candidate(s)\n")
        out.append("These *may* be replacement events. Detection over-reports on "
                   "purpose; deciding is a human judgement, so every one needs an "
                   "answer, including \"no\".\n")
        for p, why in c:
            out.append(f"- `{p}` — {why}")
        out.append("\n**How to resolve:** a reviewer — not the pull request author — "
                   "replies with a disposition for every path. Use a register ID where it "
                   "is a replacement, or `not a replacement` where it is not. The commit "
                   "line is required: it is what ties your signature to the tree you "
                   "actually looked at.\n")
        out.append("```\nexample-log:")
        out.append(f"  commit: {head[:9]}")
        for p, _ in c:
            out.append(f"  {p} -> ")
        out.append("```")
        out.append("")

    # Every line in this section tells the reviewer not to look, so it may only carry
    # claims the run actually established. Binary blobs are skipped by check_b - they
    # have no lines to compare - so where any were skipped the unqualified claim is
    # false for them, and the qualified wording is what was really checked. Saying
    # less is the price of the section being trustworthy at all.
    verified = []
    if not b:
        verified.append(
            "no licence or copyright line deleted or altered"
            if not binary_skips else
            f"no licence or copyright line deleted or altered in text files "
            f"({len(binary_skips)} binary file(s) have no lines to compare and are "
            f"listed above for your decision instead)")
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


def report_failure(exc, argv):
    """The one way this program ends badly: a block that says so, never a traceback.

    stdout is the report the review comment is built from, so the block goes there -
    an empty comment body reads as "ran, found nothing", which is the single most
    dangerous thing this tool can say. Under --fix, stdout is instead a list of paths
    the calling action commits, so the block goes to stderr; a markdown heading is not
    a filename.
    """
    where = sys.stderr if "--fix" in argv else sys.stdout
    print(f"### Blocking — the licence gate could not run\n\n"
          f"```\n{exc}\n```\n\n"
          f"Nothing was checked. This is not a clean result and must not be read "
          f"as one; a common cause is a checkout without full history.", file=where)


def run(argv, _main=None):
    """main(), with every failure turned into a report. Returns the exit code.

    The handler used to name its exceptions - GateError and UnicodeDecodeError - and
    anything else escaped as a traceback. CI went red, which is correct, but the
    review comment was built from stdout and nothing had been printed before the
    crash, so the comment body was EMPTY. An empty comment reads as "ran, found
    nothing": the gate's most dangerous possible output, produced by any bug nobody
    anticipated, which is the only kind there is. An unanticipated exception is by
    definition the case the named list does not cover, so the list was always going to
    be the wrong shape.

    `Exception`, not `BaseException`: main() returning through sys.exit raises
    SystemExit, and swallowing that - or KeyboardInterrupt - would be a new bug.

    `_main` is a seam for the test that proves this, and nothing else: the failure
    being guarded against is an exception type not yet invented, so the only honest
    way to test it is to inject one.
    """
    try:
        return (_main or main)(argv)
    except UnicodeDecodeError as exc:
        # Belt and braces behind git_io._run's errors="replace". A decode error used
        # to be raised INSIDE subprocess.run, before any returncode was looked at, so
        # it escaped every handler here: an ordinary pull request touching a PNG ended
        # the gate with a traceback and an empty report file. Named separately only to
        # say what happened in words a reader can act on.
        report_failure(GateError(f"could not decode git output: {exc}"), argv)
        return 2
    except GateError as exc:
        report_failure(exc, argv)
        return 2
    except Exception as exc:  # noqa: BLE001 - deliberate; see the docstring
        report_failure(
            GateError(f"unexpected {type(exc).__name__}: {exc}"), argv)
        return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
