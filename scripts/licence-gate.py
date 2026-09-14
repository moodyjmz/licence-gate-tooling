#!/usr/bin/env python3
"""Licence gate: a set of checks over a PR diff.

  A  a modified file carrying a licence header must also carry a modification notice
  B  no line matching Copyright / Licensed under may be deleted or altered
  C  candidate replacement events, detected and listed for a human to disposition
  D  new files that need a licence decision
  E  structured licence declarations - the licence as a field, in the metadata the
     shipped artefact carries, which no prose header matcher can see
  F  `.gitattributes` changes, which can collapse the very diff being approved

A, B and D are mechanical and may block. C only ever prompts. A false prompt costs a
reviewer seconds; a false all-clear is a missing record nobody knows is missing, so C
over-detects and the reviewer's "not a replacement" is the cheap correction. E and F
feed C rather than blocking: both describe events legitimate about as often as not, so
the answer is a person's.

Usage:  licence-gate.py <base-sha> <head-sha>
Writes a markdown report to stdout and exits non-zero if A or B failed.
"""

import re
import sys

from git_io import GateError, changed_files, git_show, merge_base, sh_strict
from licence_map import (COPYRIGHT, SOURCE_RE, declaration_kind, has_licence_header,
                         leading_comment_lines, leading_comment_region,
                         licence_declaration)

NOTICE = "Modified by the Example project."

# Tooling and documentation discuss licences without being licensed material.
IGNORE_RE = re.compile(r"^(scripts/|\.github/|README\.md$|docs?/)")
# Third-party trees we carry but do not maintain. Their headers are upstream's, not
# ours, so the two blocking checks do not apply. A dependency bump legitimately
# rewrites vendored files - removing upstream's old copyright line, adding their new
# one - and blocking it gave two instructions nobody could follow ("restore the licence
# line"; "add the modification notice") on a bot-opened PR that can answer neither.
# Blocking routine third-party work just forces an admin force-push - the only way past
# a block today - which records nothing about who decided or why.
#
# Not ignored, though: a change here is a third-party version landing in our tree,
# which is exactly what a reviewer wants told, so check_c always raises it - out of the
# blocking path, into the human one, same as binaries.
VENDOR_RE = re.compile(r"^(vendor/|vendors/|third[_-]party/|node_modules/|external/)")
# SOURCE_RE is imported from licence_map, not declared here. There were two and they
# disagreed - licence_map counted `.html` as source, this file as an asset - and a term
# with two definitions has none: the last time "has this file got a header" had three
# answers, a file our own tool stamped became invisible to both blocking checks.

# The files this programme records its own findings in. They are not licensed material,
# and their first line only looks like a header. `leading_comment_lines` reads a
# Markdown `#` heading as a line comment, so a MODIFICATIONS.md opening `# Modifications
# - licensed under the GNU AGPL v3` read as a licence header to check A, and rows naming
# a holder match NAMED_COPYRIGHT_RE - so writing the record the gate asks for was
# blocked by the gate that asked for it.
#
# Not added to IGNORE_RE: that would drop the path out of sight and make deleting
# recorded rows invisible, the wrong failure for an append-only record. These paths stay
# in every list and still reach check_c; they are excluded from the header logic of A
# and B and nothing else. Append-only is enforced by check_g, which blocks.
NOTICE_FILE_RE = re.compile(r"(^|/)(MODIFICATIONS|REPLACEMENTLOG)\.md$", re.I)
# Text that is not licensed material in its own right and is not a replaceable asset.
TEXT_RE = re.compile(r"\.(md|txt|json|ya?ml|toml|ini|cfg|lock)$|^[^.]+$", re.I)


def is_asset(path):
    """Anything that is neither source nor plain text is treated as an asset.

    An extension allow-list fails by omission on every new format: `.otf` and `.webp`
    were both missing, so a font swap and an image swap fell through every check and the
    gate reported "no replacement candidates detected" on two new third-party binaries.
    Inverting it moves the failure to the safe side - an unrecognised extension raises a
    candidate a reviewer dismisses in seconds rather than passing silently.
    """
    return not (SOURCE_RE.search(path) or TEXT_RE.search(path))


def needs_provenance(path):
    """True when a newly added path needs a human to say where it came from.

    is_asset() plus the extensionless case, which fell off check_d with no branch taken.
    TEXT_RE's `^[^.]+$` classifies a dotless path as plain text, so `VENDORTOOL` was not
    an asset, did not match SOURCE_RE, and was never listed as needing a decision nor
    raised as a candidate - a dotless vendored binary was invisible, a false all-clear
    on unrecorded third-party content.

    Routed to the asset path, not the blocking one: there may be nowhere to put a header
    in it, so blocking would be unresolvable, while a candidate costs a reviewer one
    line. No carve-out for `LICENSE` or `Makefile` - a name allow-list fails by omission
    the same way the extension one did, and a LICENSE file in a licence-compliance repo
    is worth a look anyway.

    Scoped to additions. A merely-modified extensionless file is not invisible (check_a
    and check_b read it), so widening is_asset() would only add a candidate on every
    Makefile edit. Tested on the basename: `^[^.]+$` fails on `src/v1.2/tool`, a dot in
    a directory and no extension.
    """
    return is_asset(path) or "." not in path.rsplit("/", 1)[-1]


def both_ends_ignorable(old_path, new_path):
    """True when every end of a change is a path whose licence text we do not track.

    Ignoring on the new path alone let a rename launder an edit: move a licensed file
    into docs/ in the commit that rewrites its copyright holder, and the removed line is
    skipped as documentation.
    """
    ends = [p for p in (old_path, new_path) if p]
    return bool(ends) and all(IGNORE_RE.match(p) for p in ends)


def both_ends_notice_files(old_path, new_path):
    """True when every end of a change is one of the record files.

    Same shape and reason as both_ends_ignorable: testing the new path alone would let a
    rename launder an edit. Renaming a licensed source file to MODIFICATIONS.md in the
    commit that guts its header must not buy an exemption.
    """
    ends = [p for p in (old_path, new_path) if p]
    return bool(ends) and all(NOTICE_FILE_RE.search(p) for p in ends)


# git's own window. `git diff` decides binary by looking for a NUL in the first 8000
# bytes and nowhere else, so a NUL at offset 40000 is text to git. Scanning the whole
# blob put the gate and git into disagreement in the direction that stops the gate
# checking: one NUL on the end of a .js file made it binary to us, text to git, skipped.
_BINARY_WINDOW = 8000


def looks_binary(text):
    """Whether this blob is bytes rather than lines, decided the way git decides it.

    A NUL byte is git's own test - text files do not contain them. U+FFFD counts too:
    blobs are read with errors="replace", so undecodable bytes arrive as replacement
    characters, and a run of them is a binary that decoded quietly. Both tests are
    confined to the leading window, measured on decoded text - a multi-byte character
    makes it slightly generous, which is the safe direction.

    Does not decide anything alone: one end looking binary is not grounds to skip the
    line checks - see check_b, where both ends must look binary first.
    """
    window = text[:_BINARY_WINDOW]
    if "\x00" in window:
        return True
    return window.count("\ufffd") > max(8, len(window) // 200)


def resolve(rev):
    """A ref as its commit SHA.

    The disposition block prints this for the reviewer to copy, and the acknowledgement
    gate compares what they wrote against the head SHA. Printing the ref verbatim let a
    caller's branch name into the block, so the reviewer would copy it exactly and be
    told their disposition did not name the current head. Falls back to the input if git
    cannot resolve it - a slightly wrong line beats a report that does not appear.
    """
    try:
        return sh_strict("git", "rev-parse", rev).strip() or rev
    except GateError:
        return rev


def check_b(base, head, pairs):
    """Deleted or altered licence/copyright lines. Returns [(path, line)].

    `pairs` is [(old_path, new_path, old_is_gitlink, new_is_gitlink)] taken from git's
    own file enumeration, modes included.

    Nothing here parses diff text, deliberately. Two defects existed only because this
    used to read structure off `git diff` output:

      * paths taken from `--- a/` / `+++ b/` lines can be forged from file content - a
        line reading `-- a/docs/decoy` becomes `--- a/docs/decoy` once the removal
        marker is prepended. Two of those above the real edit repointed the parser at
        `docs/`, an ignored path, and the copyright rewrite below was skipped: exit 0,
        report clean, holder replaced outright.
      * skipping any line starting `---` ate content: a deleted SQL or Lua comment
        starts `--`, arrives as `--- ...`, and read as a file header, so deleting a
        copyright line from a .sql file passed.

    The question this answers needs no diff format. It is asked twice over the two
    blobs, each one `git show` away with no syntax on top:

      * does every header-shaped line in the base blob still appear anywhere in the
        head blob?
      * does every header-shaped line in the base blob's leading comment region still
        appear in the head blob's leading comment region?

    The second question exists because prominence is positional. Move an upstream
    copyright line out of the leading comment block into a template string at the
    bottom, byte-identical, and whole-file membership still passes - but AGPL 5(a)
    requires a prominent notice, and an attribution demoted to program data is destroyed
    however many bytes survive. A whole-file set cannot express that, so the region is
    asked separately. The region is `licence_map.leading_comment_region` - the same
    definition check_a and apply_fix use, imported, not re-derived.

    Removal from the region is the finding, and only removal: a line moving into the
    region loses no prominence, and reordering within it changes no membership. Comparison
    is exact, not stripped - a whitespace-only change reads as "deleted", a false positive
    in the safe direction; stripping would let a whitespace-altered line read as unchanged.

    Binary blobs are routed to the reviewer, not blocked. A TrueType `name` table carries
    `Copyright (c) 2011 Example Foundry` as ASCII, which survives the tolerant decode, so a
    font swap would block here with advice ("restore the original line exactly") nobody can
    follow inside a .woff2. A binary has no lines anyone edits; its embedded copyright
    changed because the whole asset was replaced, which "this asset changed" already says in
    a form a reviewer can act on. This is routing, not narrowing, and only safe because of
    the guarantee below: every binary skipped here is raised as a candidate by check_c,
    whatever its extension, so `binary_skips` is returned and fed to check_c rather than
    trusting the two to agree. A deleted binary is not in that list and need not be - the
    deletion branch runs first, and check_c's `for p in deleted` loop raises it. Pinned by a
    test, because "covered by the other branch" is the sentence before a file is covered by
    neither.

    Both ends, and only both. Testing the base alone gave a two-step attack, every step
    green: step one adds a comment of undecodable bytes to a .js file ("Nothing to do");
    step two deletes the licence header and the fixture, so check_b reads the base as binary,
    skips it, and emits "no line-level check is possible" - while the head is ordinary text,
    the check was possible, and the licence line is gone. A path binary at exactly one end
    still blocks: one side has lines, so the comparison can run, and it over-reports, the safe
    direction. Only when neither end has lines is "no line-level check is possible" true, and
    the reason is emitted only where it is.
    """
    violations, binary_skips, claims, deleted_headers = [], [], [], []
    for old_path, new_path, old_is_link, new_is_link in pairs:
        if both_ends_ignorable(old_path, new_path):
            continue
        # Excluded from the header logic only. The path is still enumerated, still
        # deleted-or-not, and still reaches check_c. See NOTICE_FILE_RE.
        if both_ends_notice_files(old_path, new_path):
            continue
        if all(VENDOR_RE.match(e) for e in (old_path, new_path) if e):
            continue
        # Which end has no blob decides what happens. Skipping a pair because the path
        # appeared in the union `gitlinks` set let a licensed file replaced by a submodule
        # pointer (one path, git calls it T) pass in silence, since the base blob is
        # readable and only the head end is a gitlink. Recognised by mode throughout,
        # never by watching `git show` fail - that is how submodules became invisible once.
        if old_path is None or old_is_link:
            # Nothing was here to delete from: a pure addition, or a submodule pointer
            # whose content has never lived in this repository. check_c and check_d
            # raise both for a human.
            continue
        before = read_at(base, old_path, "the base version of")
        # A whole-file deletion is a question, not a violation. Fusing the two as "no head
        # blob means every base line is gone" blocked `git rm` on a licensed file with an
        # instruction nobody can follow - restore the lines into a file that no longer
        # exists - clearable only by an unattributed admin force-push that records nothing.
        # A rename below git's similarity threshold arrives as delete-plus-add and hit the
        # same wall. Deletion is the commonest shape of a replacement, and check_c raises
        # every deleted path as a candidate, so this routes rather than narrows. The
        # guarantee is check_c's `for p in deleted` loop; if it stops holding, silent pass.
        if new_path is None:
            # Recorded, though, because the verified box is not allowed to certify what
            # this run did not establish. Saying "no licence or copyright line deleted
            # or altered" under a heading that tells the reviewer not to look, on a pull
            # request that deleted a licensed file outright, would be a false all-clear
            # created by the routing above rather than removed by it.
            if any(has_licence_header(l) for l in before.split("\n")):
                deleted_headers.append(old_path)
            continue
        # A gitlink at the same path is not a deletion and still blocks: the entry is
        # there, the base blob is readable, and the remedy - do not overwrite a licensed
        # file with a submodule pointer - is one a person can carry out. Binary is decided
        # by content, not extension (an extension list is what made .otf and .webp
        # invisible), and at both ends: a blob binary at one end has lines at the other, so
        # the comparison runs; only a change with no lines at either end is routed away.
        if new_is_link:
            # No head blob exists to compare against, so "both ends" cannot be asked.
            # A binary base here is the font-swapped-for-a-submodule case, whose remedy
            # is not a line anyone can restore; a text base is the licensed-file case,
            # which blocks.
            if looks_binary(before):
                binary_skips.append(new_path)
                continue
            after = ""
        else:
            after = read_at(head, new_path, "the modified file")
            if looks_binary(before) and looks_binary(after):
                binary_skips.append(new_path)
                continue
        present = set(after.split("\n"))
        # The leading comment region of each end, as the set of its own lines. Taken
        # from licence_map's single definition and split back up rather than walked
        # here: a second walk is a second definition, whatever it is called.
        was_prominent = set(leading_comment_region(before).split("\n"))
        is_prominent = set(leading_comment_region(after).split("\n"))
        reported = set()
        for line in before.split("\n"):
            if not has_licence_header(line) or line in reported:
                continue
            gone_entirely = line not in present
            demoted = line in was_prominent and line not in is_prominent
            if gone_entirely or demoted:
                reported.add(line)
                violations.append((old_path, line.strip()))

        # And the other direction: examining only removals made adding an ownership claim
        # invisible. A PR could append `Copyright (c) 2022 Evil Corp / Licensed under the
        # Evil License 6.66` to any file, carry the notice correctly, and pass with the
        # verified box confirming nothing was altered - true, because something was invented
        # rather than altered. Not blocking: a contributor adding their own copyright line
        # is legitimate, so it goes to the reviewer. Absent from the base and present in the
        # head is the whole test; the notice does not match HEADER_RE, so the tool's own
        # additions do not trip it.
        was_there = set(before.split("\n"))
        for line in after.split("\n"):
            if has_licence_header(line) and line not in was_there:
                claims.append((new_path or old_path, line.strip()))
    return violations, binary_skips, claims, deleted_headers


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


def _declared_at(rev, path, is_link, what):
    """The structured declaration at one end of a change, or None if there isn't one.

    End-by-end, as check_b does it: `git show` cannot produce a blob for a gitlink, so
    reading both ends unconditionally would turn a submodule bump into "could not read
    the modified file" and block on a file that is not a file. A missing path is an
    addition or a deletion, not a failure.
    """
    if path is None or is_link or declaration_kind(path) is None:
        return None
    return licence_declaration(path, read_at(rev, path, what))


def _fmt_declaration(values):
    return " ".join(f"`{v}`" for v in values) if values else "(no licence field)"


def check_e(base, head, pairs):
    """Changes to a structured licence declaration. Returns [(path, reason)].

    Candidates, never blocking: the field is legitimately edited - every dependency bump
    rewrites a package.json, and relicensing happens - so the question is "was this
    meant?", which only a person answers. Two cases, reported differently. A declaration
    file merely touched raises nothing, the licence field intact and checked rather than
    assumed. The field changing value raises a candidate naming both values - a
    relicensing of everything the build ships. A format that could not be parsed is a
    third case and raises a candidate too: "no licence field changed" and "could not read
    as JSON" are different answers and must not share a code path.
    """
    findings = []
    for old_path, new_path, old_is_link, new_is_link in pairs:
        if both_ends_ignorable(old_path, new_path):
            continue
        before = _declared_at(base, old_path, old_is_link, "the base version of")
        after = _declared_at(head, new_path, new_is_link, "the modified file")
        if before is None and after is None:
            continue
        path = new_path or old_path
        if (before is not None and not before.parsed) or \
           (after is not None and not after.parsed):
            findings.append((path,
                             "declares its licence in a structured field, and this run "
                             "could not parse the file — so whether the licence changed "
                             "is UNKNOWN, not unchanged. A reviewer other than the "
                             "author must read the file and say"))
            continue
        was = before.values if before is not None else ()
        now = after.values if after is not None else ()
        if was == now:
            continue
        findings.append((path,
                         "the structured licence declaration changed: {} → {}. This is "
                         "the licence the built artefact carries (.deb, .rpm, the npm "
                         "package), not a source header. A reviewer other than the "
                         "author must confirm the change was intended and recorded"
                         .format(_fmt_declaration(was), _fmt_declaration(now))))
    return findings


def check_a(base, head, modified, base_paths=None, gitlinks=()):
    """Modified files that have a licence header but no notice.

    The header is looked for in the file's leading comment block, not a fixed line
    count. A 40-line window was defeated by 45 lines of filler comment above the header:
    the gate then saw no header and demanded no notice. See
    licence_map.leading_comment_region for why the whole file is not scanned instead.
    """
    missing = []
    for p in modified:
        # Tooling and documentation are excluded, as in check_b and check_c. Otherwise a
        # README opening "# Copyright (c) 2020 Example Corp" is demanded a notice on every
        # edit that /auto-fix cannot supply - a Markdown file has no comment block to
        # close - blocking a PR on a requirement nothing can satisfy.
        if IGNORE_RE.match(p) or VENDOR_RE.match(p):
            continue
        # A record file's first line is a Markdown heading, not a header, and Markdown has
        # no comment block for /auto-fix to close - so a notice here is a requirement
        # nothing can satisfy, on the one file the programme exists to write. See
        # NOTICE_FILE_RE.
        if NOTICE_FILE_RE.search(p):
            continue
        # A submodule has no content here, so nowhere to put a notice and nothing to read.
        # Not silently dropped: check_c and check_d raise it, which is where "where did
        # this content come from?" belongs.
        if p in gitlinks:
            continue
        content = read_at(head, p, "the modified file")
        # Ask the base as well as the head. Reading only the head meant one line of code
        # prepended above an existing header ended the region before it, so the gate
        # decided the file had none - a one-line bypass. Whether the file carried a header
        # before the change is what matters, and the base answers that. A renamed file
        # does not exist at its new path in the base, so ask for the name it had there.
        before = read_at(base, (base_paths or {}).get(p, p), "the base version of")
        had_header = has_licence_header(leading_comment_region(before))
        region_now = leading_comment_region(content)
        has_header_now = has_licence_header(region_now)
        # The notice is looked for in the region, not the whole file - whole-blob
        # membership was satisfied by the string appearing anywhere, inside a literal or a
        # vendored bundle far below. Prominence is positional (see check_b), and the notice
        # is subject to the same rule. Not a narrowing: the region is the same
        # `leading_comment_region` apply_fix writes into, so the remedy still satisfies the
        # check, and a notice the region cannot see a reader at the top cannot see either.
        if (had_header or has_header_now) and NOTICE not in region_now:
            missing.append(p)
    return missing


def check_d(head, added, gitlinks=()):
    """New files that need a licence decision.

    Not auto-fixable, unlike check A. A modified file always needs the same notice, so a
    machine can write it; a new file needs a *copyright holder*, which depends on where
    the content came from - no diff can tell. Stamping new files mechanically is how a
    vendored third-party asset ends up carrying your copyright: two icons added to a real
    repository, named as first-party work, were unmodified Google Material Symbols, and
    "new files get our header" would have asserted copyright over someone else's work.

    Source files block - a header can be added to one. Binary assets do not: there is
    nowhere to put a header in a .otf, so they raise a candidate the acknowledgement gate
    makes a reviewer disposition. A submodule is neither, and takes the first branch
    before any extension is looked at: deciding by extension hid it, since a gitlink named
    `vendor/thing.py` matched SOURCE_RE and `vendor/thing` matched TEXT_RE's no-extension
    rule - neither is a file, and the name never had anything to say about it.

    Whose header it is decides the question. A new source file already carrying a header
    used to produce nothing - not `needs_provenance`, took the SOURCE_RE branch, had a
    header, appended to no list - so a third-party file added with upstream's header
    intact, the commonest way vendored code arrives, was invisible here and to check_c's
    added-loop. The discriminator is the holder, not the presence of a header: a header
    naming us is a claim the diff shows, a header naming someone else is content that
    arrived from somewhere, so the first raises nothing and the second a candidate.
    Asking about every headered new file instead fires on ordinary first-party work.

    What this cannot close: a third-party file added carrying *our* header. The claim is
    false and the diff cannot tell. That is the permanent human part.
    """
    needing, assets, links, foreign = [], [], [], []
    for p in added:
        if p in gitlinks:
            links.append(p)
        elif needs_provenance(p):
            assets.append(p)
        elif SOURCE_RE.search(p):
            content = read_at(head, p, "the new file")
            region = leading_comment_region(content)
            if not has_licence_header(region):
                needing.append(p)
            elif COPYRIGHT not in region:
                foreign.append((
                    p, "new file arriving with a licence header that does not name "
                       "us - whose code is it, under which licence, and does it need "
                       "recording?"))
    return needing, assets, links, foreign


# `.gitattributes` at any depth: git reads one per directory, so a nested file governs
# its own subtree and hides a diff exactly as well as the root one.
GITATTRIBUTES_RE = re.compile(r"(^|/)\.gitattributes$")

# Attributes that stop a reviewer seeing the diff they are approving. `linguist-*`
# makes GitHub collapse the file's diff by default; `-diff` and `binary` are stronger
# still - there is no textual diff rendered at all.
_DIFF_HIDING_ATTRS = ("linguist-generated", "linguist-vendored", "-diff", "binary")

# `pattern attr attr ...`, with the pattern optionally quoted because git allows a
# space in it. Getting the pattern wrong would name the wrong file in the finding.
_ATTR_LINE_RE = re.compile(r'^("(?:[^"\\]|\\.)*"|\S+)\s*(.*)$')


def _attribute_lines(text):
    """(pattern, attributes, raw) for each real line. Comments and blanks dropped."""
    out = []
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _ATTR_LINE_RE.match(stripped)
        if m:
            out.append((m.group(1), m.group(2).split(), stripped))
    return out


def _blob_or_empty(rev, path, is_link, what):
    """The blob at one end, or "" where that end has none. End-by-end, as check_b."""
    if path is None or is_link:
        return ""
    return read_at(rev, path, what)


def check_f(base, head, pairs):
    """Changes to `.gitattributes`. Returns [(path, reason)].

    The attack: marking a path `linguist-generated` makes GitHub collapse that file's
    diff by default in the PR view, so a contributor can hide the very change a reviewer
    is about to approve - and a finding naming a path the reviewer never looks at is worse
    than none, because they believe they read it.

    The path already reached a human: `.gitattributes` matches neither SOURCE_RE nor
    TEXT_RE, so `is_asset` raised it as "asset modified". That was never the gap - "asset
    modified" does not tell a reviewer a diff below is collapsed, and it is the mechanism,
    not the path, they need.

    A candidate, never a block: marking a genuinely generated file is ordinary, whether
    this one is generated is a judgement, and blocking would be unresolvable for the
    legitimate case. The wording is only as strong as what was established - a
    `.gitattributes` change that hides no diff still raises a candidate (the file decides
    how every diff renders) but does not claim one was hidden, and removing
    `linguist-generated` lands here as an ordinary change, since un-collapsing a diff is
    not an attack.
    """
    findings = []
    for old_path, new_path, old_is_link, new_is_link in pairs:
        ends = [p for p in (old_path, new_path) if p]
        if not any(GITATTRIBUTES_RE.search(p) for p in ends):
            continue
        before = _blob_or_empty(base, old_path, old_is_link, "the base version of")
        after = _blob_or_empty(head, new_path, new_is_link, "the modified file")
        was = {raw for _, _, raw in _attribute_lines(before)}
        hiding = [(pat, [a for a in attrs if a in _DIFF_HIDING_ATTRS])
                  for pat, attrs, raw in _attribute_lines(after)
                  if raw not in was and any(a in _DIFF_HIDING_ATTRS for a in attrs)]
        path = new_path or old_path
        if hiding:
            named = ", ".join("`{}` is marked `{}`".format(pat, " ".join(attrs))
                              for pat, attrs in hiding)
            findings.append((path,
                             "`.gitattributes` adds or changes a diff-hiding attribute: "
                             "{}. GitHub collapses a matching file's diff by default, so "
                             "a change under that pattern can be approved without ever "
                             "being displayed. Expand every collapsed diff in this pull "
                             "request before approving it, and confirm the paths really "
                             "are generated or vendored. A reviewer other than the "
                             "author must do this".format(named)))
        else:
            findings.append((path,
                             "`.gitattributes` changed — it decides how every diff in "
                             "this repository is rendered and which paths GitHub treats "
                             "as generated. A reviewer other than the author must read "
                             "the change itself"))
    return findings


def check_g(base, head, pairs):
    """The register files are append-only. Returns [(path, reason)]. Blocks.

    The base blob must be a byte-for-byte prefix of the head blob: rows may be added
    beneath what is there, nothing already recorded may move, change or disappear.

    There was no content check here before. MODIFICATIONS.md and REPLACEMENTLOG.md are
    excluded from checks A and B (a Markdown heading reads as a header, rows naming a
    holder as ownership claims), which left the two files this programme exists to write
    unchecked - deleting historical rows raised nothing but a check_c "this file changed",
    true of every PR that records anything.

    This blocks, and always will: the register is the artefact, and a row waved through
    is a record nobody knows is missing. The attributed override in
    docs/override-design.md - designed, not yet built - deliberately excludes this check,
    because the register can never be rewritten, only appended. Blocking strands nobody - the remedy is mechanical, put the rows back, always
    possible unlike "restore the licence line" on a vendored bump, and no bot authors a
    register edit. The cost, plainly: a typo fix in an existing row is blocked too, because
    an append-only record whose rows can be edited is not one; the message says to append a
    correcting row instead.

    A subtler cost: `base` is the merge-base, so a row edited on the base branch and merged
    in arrives as this PR's change. The remedy still works.

    Either end, not both: renaming the register away in the commit that guts it must not
    buy an exemption, the mirror of `both_ends_notice_files`. IGNORE_RE is not consulted -
    a register under docs/ is still the register.
    """
    violations = []
    for old_path, new_path, old_is_link, new_is_link in pairs:
        ends = [p for p in (old_path, new_path) if p]
        if not any(NOTICE_FILE_RE.search(p) for p in ends):
            continue
        # Nothing was here to preserve: the register being created, or a submodule
        # pointer whose content has never lived in this repository.
        if old_path is None or old_is_link:
            continue
        before = read_at(base, old_path, "the base version of")
        after = _blob_or_empty(head, new_path, new_is_link, "the modified file")
        if after.startswith(before):
            continue
        b_lines, a_lines = before.split("\n"), after.split("\n")
        common = 0
        while (common < len(b_lines) and common < len(a_lines)
               and b_lines[common] == a_lines[common]):
            common += 1
        lost = len(b_lines) - common
        # Reported as a divergence point, not a row count: everything after the first
        # mismatch is unverifiable, not necessarily deleted - a one-word edit to the first
        # of ten rows leaves the other nine unmatched too, so "10 rows lost" would be false.
        violations.append((old_path,
                           "the register is append-only, and line {} no longer matches "
                           "what was already recorded there ({} recorded line(s) from "
                           "that point on are changed, reordered or missing)"
                           .format(common + 1, lost)))
    return violations


def _add_reason(cands, path, why):
    """Add a candidate, merging into any entry this path already has.

    The disposition block prints one `path -> ` line per candidate and the reviewer
    answers each, so the same path twice asks them to answer it twice and gives the
    acknowledgement gate two keys for one file. The specific reason leads: a reviewer
    scanning the list reads a leading "asset modified" and moves on, so the finding that
    earned its own check goes first and the generic one follows as context.
    """
    for i, (p, existing) in enumerate(cands):
        if p == path:
            if why not in existing:
                cands[i] = (p, why + " (also: " + existing + ")")
            return
    cands.append((path, why))


def check_c(added, modified, deleted, renamed, gitlinks=(), binary_skips=(),
            claims=(), *, declarations, attributes):
    """Candidate replacement events. Over-detects by design.

    `declarations` and `attributes` are keyword-only with no default, deliberately. The
    two call sites - the report and `--candidates`, which the acknowledgement gate reads
    - would diverge if a default let one silently stop requiring answers for licence-field
    changes while the other kept requiring them. A TypeError makes a forgotten call site
    announce itself.
    """
    cands = []
    for old, new in renamed:
        cands.append((old, f"renamed to `{new}` - the commonest shape of a replacement"))
    for p in deleted:
        cands.append((p, "file deleted"))
    for p in added:
        # needs_provenance rather than is_asset: an extensionless addition raised no
        # candidate here either. `and p not in gitlinks` is load-bearing - the dedup below
        # runs after this loop, so a gitlink admitted here as "asset added" would suppress
        # the submodule reason that tells the reviewer the content is in another repo.
        if needs_provenance(p) and p not in gitlinks:
            cands.append((p, "asset or extensionless file added"))
    for p in modified:
        if is_asset(p) and p not in gitlinks:
            cands.append((p, "asset modified"))
    # One dedup, loops most-specific-first. `seen` is first-writer-wins, so this order
    # decides which of several true reasons the reviewer is shown - behaviour, not layout.
    # A vendored submodule qualifies twice, and "third-party tree changed" would send the
    # reviewer to read a diff that does not exist in this repository.
    seen = {p for p, _ in cands}
    # Every gitlink, whatever its name and whatever git called the change: a submodule is
    # a pointer at a tree in another repository, unrecorded vendored content that a bump
    # silently swaps. It reached no list and raised no candidate, so the acknowledgement
    # gate reported "No replacement candidates detected" on the one thing this tool is for.
    for p in sorted(gitlinks):
        if p not in seen:
            cands.append((p, "submodule (gitlink) added or changed - its content lives "
                             "in another repository and is not in this diff"))
            seen.add(p)
    for path, line in claims:
        if path not in seen:
            cands.append((path, "a new ownership or licence claim appears in this file "
                                "— only a person can say whether it is true"))
            seen.add(path)
    for p in list(added) + list(modified) + list(deleted):
        if VENDOR_RE.match(p) and p not in seen:
            cands.append((p, "third-party tree changed - upstream content landing in "
                             "our repository, which is a replacement whoever did it"))
            seen.add(p)
    # check_b skips binary blobs (no lines to remove), which is routing rather than
    # narrowing only if every one reaches a reviewer here - so the skipped paths are passed
    # in and added explicitly rather than trusting the extension rules to cover them. A
    # binary named .js would otherwise be skipped by check_b, classified as source by
    # check_c, and vanish between them.
    for p in binary_skips:
        if p not in seen:
            cands.append((p, "binary content changed - no line-level check is possible, "
                             "so the whole asset needs your decision"))
    # Merged rather than appended: a declaration file may already be here as a deleted
    # path or an asset, and the specific reason is the one worth reading.
    for path, why in list(declarations) + list(attributes):
        _add_reason(cands, path, why)
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

    The style comes from the scanner's record of what the anchor line is, not re-derived
    from how it starts. Re-deriving corrupted files: the old code looked for a closer
    alone on its own line, so an ordinary header closing on the same line as its last
    text (` * Licensed under X */`) matched nothing, fell through to the line-comment
    branch whose marker matched the body's leading `*`, and appended the notice after the
    `*/` - outside the comment, as a bare statement. `node --check` rejects that, and
    /auto-fix used to hold contents:write and push it unreviewed.
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
    before a `*/` or `-->`, the closing tokens of block comments, but most extensions this
    tool stamps use line comments (# for Python/shell/Ruby, // for JS/C/Java/Go) with no
    closing token. For those the search fell through, the file was left untouched, and
    /auto-fix replied "Nothing to auto-fix" on a PR the gate had blocked - a remedy that
    quietly does nothing is worse than none.
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
        # The same question check_a asks, of the same region. Testing the whole file let
        # a stray notice below the header block - which check_a blocks on, prominence
        # being positional - look already-done, so the remedy wrote nothing and the
        # blocked PR had no way out. The remedy and the check must agree on what "already
        # has a notice" means.
        if NOTICE in leading_comment_region(content):
            continue
        lines = content.split("\n")
        # The anchor must be in the leading comment region. The old search ran over
        # `max(region, 40)` lines and anchored on whatever looked like a header - a licence
        # line inside a string literal or heredoc, program data, not a comment - and wrote
        # the notice into the middle of it. A file check_a flags that this cannot place a
        # notice in is reported unfixable, for a human. A remedy may refuse; it may not
        # guess.
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
    # One resolution, before anything reads git, so everything below sees the same commit
    # - the enumeration here and every `git show` checks A and B make against `base`. What
    # arrives is the base branch's tip, so diffing it replayed the base branch's own recent
    # commits in reverse and billed them to the author. Resolving it in changed_files alone
    # would fix the file list and leave the blobs at the tip, the same defect with fewer
    # symptoms.
    base = merge_base(base, head)
    added, modified, deleted, renamed, pairs, gitlinks = changed_files(base, head)
    # A rename is reported instead of a modification, never alongside it, so a file
    # renamed and edited in one commit never reached check_a - it kept its header, gained
    # content, needed a notice, and the report certified every headered file carried one.
    modified_for_a = modified + [new for _, new in renamed]
    base_paths = {p: p for p in modified}
    base_paths.update({new: old for old, new in renamed})

    if candidates_only:
        _, binary_skips, claims, _deleted_headers = check_b(base, head, pairs)
        # The foreign-header additions belong here too. Adding them to the report's call
        # and not this one split the two: the report said "1 candidate" while the
        # acknowledgement gate, which reads this list, demanded nothing and went green.
        # Until the two enumerators are one function, every candidate source goes in both.
        _, _, _, foreign = check_d(head, added, gitlinks)
        for p, _ in check_c(added, modified, deleted, renamed, gitlinks,
                            binary_skips, claims,
                            declarations=list(check_e(base, head, pairs)) + list(foreign),
                            attributes=check_f(base, head, pairs)):
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

    b, binary_skips, claims, deleted_headers = check_b(base, head, pairs)
    a = check_a(base, head, modified_for_a, base_paths, gitlinks)
    # check_d first: the foreign-header additions it finds are candidates, so it feeds
    # check_c rather than merely reporting alongside it.
    d_src, d_assets, d_links, d_foreign = check_d(head, added, gitlinks)
    c = check_c(added, modified, deleted, renamed, gitlinks, binary_skips, claims,
                declarations=list(check_e(base, head, pairs)) + list(d_foreign),
                attributes=check_f(base, head, pairs))
    g = check_g(base, head, pairs)

    out = []
    blocking = bool(a or b or d_src or g)

    # A "what has to happen" block, up front. The report used to state every fact and
    # leave the reader unable to act on any of them. Three faults, all fixed here:
    #   * nothing said what was actually blocking - an advisory section carrying its own
    #     "How to resolve" sat above a check that had already passed, so the reader could
    #     not tell which section stopped the merge;
    #   * advisory and blocking sections were formatted identically, so "you may want to"
    #     and "this will not merge until" looked alike;
    #   * the disposition instruction addressed whoever was reading, but a disposition from
    #     the author is refused by design - it told the one person reading it to do the one
    #     thing they are barred from, without mentioning the bar.
    todo = []
    if g:
        todo.append(("anyone with write access, the author included",
                     "put back the {} the register lost — restore the rows exactly as "
                     "they were and add any new ones beneath them".format(
                         "row" if len(g) == 1 else "rows")))
    if b:
        todo.append(("anyone with write access",
                     "restore the {} this pull request removed or altered — by hand; "
                     "this cannot be automated".format(
                         "licence line" if len(b) == 1 else
                         "{} licence lines".format(len(b)))))
    if a:
        todo.append(("anyone with write access, the author included",
                     "add the modification notice to {} — comment `/auto-fix` for a "
                     "patch to apply yourself".format(
                         "1 file" if len(a) == 1 else "{} files".format(len(a)))))
    if d_src:
        todo.append(("anyone with write access",
                     "say who wrote {} and let the header be applied — see below"
                     .format("1 new file" if len(d_src) == 1
                             else "{} new files".format(len(d_src)))))
    if claims:
        todo.append(("a reviewer",
                     "confirm or reject {} new ownership claim(s) this pull request adds "
                     "to existing files".format(len(claims))))
    if c:
        subject = ("the candidate below" if len(c) == 1
                   else "every one of the {} candidates below".format(len(c)))
        todo.append(("a reviewer" + (", not @" + author if author else ", not the author"),
                     "answer {}, using the block at the end of this comment".format(subject)))

    if todo:
        out.append("### What has to happen before this merges\n")
        for i, (who, what) in enumerate(todo, 1):
            out.append("{}. **{}** — {}".format(i, who, what))
        out.append("")
        if c and author:
            out.append("> @{} opened this pull request, so a disposition from them is "
                       "not accepted. Anyone else with write access can give it.\n"
                       .format(author))
    else:
        out.append("### Nothing to do — both checks pass\n")
        out.append("Anything below is for information and does not block the merge.\n")

    if g:
        out.append(f"### Blocking — the register lost rows ({len(g)} file(s))\n")
        out.append("`MODIFICATIONS.md` and `REPLACEMENTLOG.md` are **append-only**. They "
                   "are the record this whole programme exists to produce, and a row "
                   "that quietly disappears is a record nobody knows is missing. The "
                   "commonest cause is not malice: two pull requests both append, the "
                   "second hits a conflict, and the resolution drops the first's "
                   "rows.\n")
        out.append("**How to resolve:** restore the previous content exactly, then add "
                   "your new rows beneath it. Anyone with write access can do this, the "
                   "author included — it is a mechanical fix, not a judgement.\n")
        out.append("**If a recorded row is genuinely wrong, do not edit it.** Append a "
                   "new row that corrects it and names the row it corrects. A register "
                   "whose history can be rewritten is not a register.\n")
        for p, why in g:
            out.append(f"- `{p}` — {why}")
        out.append("")

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
        out.append("**How to resolve:** comment `/auto-fix` and the bot replies with a "
                   "patch; save it, `git apply` it, then commit and push. Or add the line "
                   "by hand. Anyone with write access can ask for the patch — this is a "
                   "mechanical fix, not a judgement — but you apply it yourself: a bot "
                   "pushing here would make the bot the last pusher and dismiss any "
                   "review already given.\n")
        for p in a:
            out.append(f"- `{p}`")
        out.append("")

    if d_src or d_assets or d_links or d_foreign:
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
        for p, _ in d_foreign:
            out.append(f"- `{p}` — new file, and its header names someone other than us. "
                       f"It is in the candidate list below: a reviewer must say whose "
                       f"code this is and whether it needs recording.")
        for p in d_links:
            out.append(f"- `{p}` — submodule added. Its content is in another repository "
                       f"and is not in this diff: what is it, who wrote it, and under "
                       f"which licence is it being vendored?")
        out.append("\n`/auto-fix` does not touch new files: the copyright holder depends "
                   "on the file's origin, which the diff cannot determine.\n")
        if d_src:
            # The report said "by hand" while the command that does it existed and went
            # unmentioned. A tool nobody is told about is a tool nobody uses, and the
            # instruction it replaced was the vaguest sentence in the whole report.
            named = " ".join(d_src)
            out.append("**If these are ours, say so and the headers are applied:** post "
                       "a comment reading\n")
            out.append("```\n/std-licence {}\n```".format(named))
            out.append("\nThis command asserts *we wrote these*, so it names each file "
                       "rather than taking them in one sweep. The licence is derived from "
                       "the path; the copyright line records your assertion of "
                       "authorship.\n")
            out.append("If any of them is **not** ours — vendored, copied, generated "
                       "from something else — leave it out and add its real header by "
                       "hand.\n")

    if claims:
        out.append("### Ownership claimed — {} new line(s)\n".format(len(claims)))
        out.append("Someone has **added** a copyright or licence statement. Nothing was "
                   "removed, so the checks above have nothing to say about it — but a "
                   "file now asserts something it did not before, and a reviewer must "
                   "confirm it.\n")
        for path, line in claims:
            out.append("- `{}`\n  ```\n  {}\n  ```".format(path, line))
        out.append("\nIf it is right — a contributor recording their own copyright, a "
                   "header added deliberately — say so in the disposition below. If it "
                   "is not, remove the line.\n")

    if c:
        out.append("### A reviewer must answer these — {}\n".format(
            "1 candidate" if len(c) == 1 else "{} candidates".format(len(c))))
        out.append("These *may* be replacement events. Detection over-reports on "
                   "purpose; deciding is a human judgement, so every one needs an "
                   "answer, including \"no\".\n")
        for p, why in c:
            out.append(f"- `{p}` — {why}")
        # Say what to do first, then what it means. This described its own purpose - "the
        # commit line ties your signature to the tree you looked at" - which reads as
        # homework and never mentions the line is already filled in. Nothing here needs
        # looking up or composing.
        out.append("\n**What to do — four steps, nothing to look up:**\n")
        out.append("1. Copy the block below.")
        out.append("2. Paste it into the comment box at the bottom of this pull request.")
        out.append("3. After each `->`, type your answer.")
        out.append("4. Post the comment.\n")
        out.append("The check re-runs automatically within about a minute; nothing else "
                   "is needed.\n")
        out.append("```\nexample-log:")
        # Resolve, never echo. This printed whatever ref it was handed, so a caller
        # passing a branch name put a branch name in the block the reviewer copies -
        # while the acknowledgement gate compares against the head SHA. The reviewer
        # would have followed the instructions exactly and been told, for ever, that
        # their disposition did not name the current head.
        out.append(f"  commit: {resolve(head)[:9]}")
        for p, _ in c:
            out.append(f"  {p} -> ")
        out.append("```")
        out.append("\nAn answer is either a **register ID** (if the file replaces "
                   "something) or the words **`not a replacement`** (if it does not). "
                   "Every line needs one — a blank is not an answer, and the gate will "
                   "say so.\n")
        out.append("The `commit:` line is already filled in; leave it as it is. It "
                   "records the commit you reviewed, so if the branch is pushed again "
                   "your sign-off lapses instead of covering code you did not see.\n")
        out.append("The disposition must come from a reviewer other than the author.\n")

    # Every line in this section tells the reviewer not to look, so it may only carry
    # claims the run actually established. Binary blobs are skipped by check_b - they
    # have no lines to compare - so where any were skipped the unqualified claim is
    # false for them, and the qualified wording is what was really checked. Saying
    # less is the price of the section being trustworthy at all.
    verified = []
    if not b:
        # Deleted files carry the same obligation as binaries do. check_b no longer
        # blocks on a whole-file deletion - there is no file to restore a line into -
        # so an unqualified "nothing was deleted" would be a false all-clear invented
        # by that routing. The files are listed as candidates above; this line says so
        # rather than certifying past them.
        caveats = []
        if binary_skips:
            caveats.append(f"{len(binary_skips)} binary file(s) have no lines to "
                           f"compare")
        if deleted_headers:
            caveats.append(f"{len(deleted_headers)} licensed file(s) were deleted "
                           f"outright")
        verified.append(
            "no licence or copyright line deleted or altered"
            if not caveats else
            "no licence or copyright line deleted or altered in the files that still "
            "exist as text ({} and are listed above for your decision instead)"
            .format(", ".join(caveats)))
    if not a:
        verified.append("every modified file with a header carries a notice")
    # Only where a register was actually in this diff. Telling a pull request that
    # never touched one that its rows survived is a claim about a file this run never
    # read - the shape of false all-clear the verified box exists to avoid.
    if not g and any(NOTICE_FILE_RE.search(p)
                     for p in list(added) + list(modified) + list(deleted)
                     + [x for pair in renamed for x in pair]):
        verified.append("every row already recorded in the register is still there, "
                        "unchanged")
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

    Naming the expected exceptions (GateError, UnicodeDecodeError) let anything else
    escape as a traceback: CI went red, correctly, but nothing was printed before the
    crash, so the review comment built from stdout was empty - which reads as "ran, found
    nothing". Catching `Exception` here turns any unanticipated bug into a report that
    blocks instead. Not `BaseException`: main() exits through sys.exit, so swallowing
    SystemExit or KeyboardInterrupt would be a new bug.

    `_main` is a seam for the test that injects an as-yet-uninvented exception type.
    """
    try:
        return (_main or main)(argv)
    except UnicodeDecodeError as exc:
        # Belt and braces behind git_io._run's errors="replace". A decode error used to
        # be raised inside subprocess.run before any returncode was looked at, escaping
        # every handler here - a PR touching a PNG ended the gate with a traceback and an
        # empty report. Named separately only to say what happened in words a reader can
        # act on.
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
