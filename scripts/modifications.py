#!/usr/bin/env python3
"""MODIFICATIONS.md, generated from git history. Never hand-written.

A fork has to say what it changed and when. Every field of that notice is already
recorded in the history, so nobody types one: this projects the history onto the
file. The only thing a human supplies is the baseline - the point the fork starts
from - and that lives in THIS repository, keyed by consuming repo, precisely so a
pull request in the described repo cannot move its own fork point and delete its
own history in the same change.

Three modes, and only one of them writes anything:

    modifications.py                     print the notice to stdout
    modifications.py --write             regenerate the file in place
    modifications.py --check BASE HEAD   compare against the committed file

Every git command goes through git_io. `subprocess` is not imported here, and that
is deliberate: the tolerant version of these calls - stdout, exit code ignored -
turned a failed git command into "nothing to report" four separate times in this
tooling. A generator that cannot read the history must say so; emitting a notice
with no entries in it is the one answer it is never entitled to give.
"""

import argparse
import fnmatch
import os
import re
import sys

from git_io import GateError, git_show, merge_base, sh_strict

FILENAME = "MODIFICATIONS.md"
HEADING = "# Modifications"

DEFAULT_CONFIG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config", "modifications.toml")

REQUIRED_KEYS = ("upstream", "baseline", "baseline_date", "upstream_authors")

# The shapes git gives a merge whose real title is in the body. A squash merge is not
# in this list: its subject IS the title.
MERGE_SUBJECT_RE = re.compile(
    r"^(?:Merge pull request #\d+ from |Merge branch |Merge branches |"
    r"Merge remote-tracking branch )")

# What an entry says when the history genuinely contains no description of the change.
# Fixed text: it is written into a file the gate enforces as append-only, so it must
# never vary between two runs over the same commit.
NO_DESCRIPTION = "(no description recorded in the history)"

BASELINE_RE = re.compile(
    r"^Forked\s+from\s+(.+?)\s+at\s+([0-9a-fA-F]{7,40})\s*,\s*dated\s+(\d{4}-\d{2}-\d{2})\s*\.\s*$")
ENTRY_RE = re.compile(
    r"^-\s+(\d{4}-\d{2}-\d{2})\s+([0-9a-fA-F]{4,40})\s+(\S.*?)\s*$")

# Record and field separators for `git log --format`. C0 controls that no commit
# message contains, and - unlike a newline - nothing in a subject or a body can be
# mistaken for one. Splitting a log on newlines and hoping is how a body-carrying
# merge commit gets read as several malformed records.
RS = "\x1e"
FS = "\x1f"


class ConfigError(GateError):
    """The configuration could not be used. Distinct from "nothing changed"."""


# --------------------------------------------------------------------------- config


def parse_toml(text, source="config"):
    """The small TOML subset this config needs, parsed STRICTLY.

    Python here is 3.9: `tomllib` arrived in 3.11, and a third-party dependency for
    four keys per repo is not a trade anyone should take. So: tables with a quoted or
    bare key, string values, single-line arrays of strings, `#` comments.

    Strict means anything outside that subset RAISES, naming the line. A tolerant
    parser - skip what you do not recognise - is the same defect as a tolerant git
    call: a mistyped `upstream_authors` line would be silently dropped, and silently
    dropping the list that decides what counts as ours produces a longer notice with
    no sign anything went wrong. Loud and wrong beats quiet and plausible.
    """
    tables = {}
    current = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise ConfigError(f"{source} line {n}: unterminated table header {raw!r}")
            name = line[1:-1].strip()
            if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
                name = name[1:-1]
            if not name or '"' in name:
                raise ConfigError(f"{source} line {n}: unusable table name {raw!r}")
            if name in tables:
                raise ConfigError(f"{source} line {n}: table [{name}] is defined twice")
            current = tables.setdefault(name, {})
            continue
        if "=" not in line:
            raise ConfigError(f"{source} line {n}: not a comment, table or assignment: {raw!r}")
        if current is None:
            raise ConfigError(f"{source} line {n}: assignment before any [table]: {raw!r}")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not re.match(r"^[A-Za-z0-9_-]+$", key):
            raise ConfigError(f"{source} line {n}: unusable key {key!r}")
        if key in current:
            raise ConfigError(f"{source} line {n}: key {key!r} is set twice in this table")
        current[key] = _parse_value(value, n, source, raw)
    return tables


def _parse_value(value, n, source, raw):
    if value.startswith("["):
        if not value.endswith("]"):
            raise ConfigError(
                f"{source} line {n}: arrays must be written on one line: {raw!r}")
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_string(part.strip(), n, source, raw)
                for part in _split_array(inner, n, source, raw)]
    return _parse_string(value, n, source, raw)


def _split_array(inner, n, source, raw):
    parts, depth, buf = [], 0, ""
    for ch in inner:
        if ch == '"':
            depth ^= 1
        if ch == "," and not depth:
            parts.append(buf)
            buf = ""
            continue
        buf += ch
    if depth:
        raise ConfigError(f"{source} line {n}: unterminated string in array: {raw!r}")
    if buf.strip():
        parts.append(buf)
    return parts


def _parse_string(value, n, source, raw):
    m = re.match(r'^"([^"\\]*)"$', value)
    if not m:
        raise ConfigError(
            f"{source} line {n}: values must be plain double-quoted strings "
            f"(no escapes, no single quotes, no bare words): {raw!r}")
    return m.group(1)


def load_config(path, repo):
    """The entry for `repo`, or a hard error saying exactly which of the two went wrong.

    "No config file" and "no entry for this repo" are different problems with
    different remedies, and neither is "this fork has no modifications".
    """
    if not os.path.exists(path):
        raise ConfigError(
            f"no modifications config at {path}. The baseline lives in the TOOLING "
            f"repository, keyed by consuming repo, and cannot be read from the repo "
            f"being described.")
    with open(path, "r", encoding="utf-8") as fh:
        tables = parse_toml(fh.read(), source=path)
    if repo not in tables:
        raise ConfigError(
            f"no config entry for `{repo}` in {path}. Add a [{repo}] table naming the "
            f"upstream, the baseline commit and its date. Without a baseline there is "
            f"no such thing as a modification, so nothing can be generated - this is "
            f"not the same as the fork having changed nothing.")
    entry = tables[repo]
    missing = [k for k in REQUIRED_KEYS if k not in entry]
    if missing:
        raise ConfigError(
            f"the [{repo}] entry in {path} is missing: {', '.join(missing)}")
    unknown = [k for k in entry if k not in REQUIRED_KEYS]
    if unknown:
        raise ConfigError(
            f"the [{repo}] entry in {path} has unknown key(s): {', '.join(sorted(unknown))}")
    if not isinstance(entry["upstream_authors"], list):
        raise ConfigError(
            f"the [{repo}] entry in {path}: upstream_authors must be a list, even "
            f"when it is empty")
    for key in ("upstream", "baseline", "baseline_date"):
        if isinstance(entry[key], list):
            raise ConfigError(f"the [{repo}] entry in {path}: {key} must be a string")
    return entry


# ----------------------------------------------------------------------- repo, git


def repo_identity(override=None):
    """`owner/name` for the checkout we are standing in.

    `git config --get remote.origin.url` EXITS 1 when the key is absent, and exit 1
    with an empty stderr is the one failure shape this tooling keeps being bitten by:
    sh_strict would report "failed (1): no error output", which tells the reader
    nothing. Catch it here and say what is actually wrong, and what to pass instead.
    """
    if override:
        if override.count("/") != 1 or not all(override.split("/")):
            raise ConfigError(f"--repo must be owner/name, not {override!r}")
        return override
    try:
        url = sh_strict("git", "config", "--get", "remote.origin.url").strip()
    except GateError as exc:
        raise ConfigError(
            "could not read `remote.origin.url`, so this repository cannot be "
            "identified and its config entry cannot be looked up. Pass --repo "
            f"owner/name. ({exc})") from exc
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
    if not m:
        raise ConfigError(
            f"could not read owner/name out of the origin URL {url!r}. Pass --repo "
            f"owner/name.")
    return f"{m.group(1)}/{m.group(2)}"


def resolve_baseline(baseline):
    """The full SHA of the configured baseline, or a hard error naming fetch depth.

    git will not tell you this. `git rev-parse --verify` on a commit a shallow clone
    cannot see fails the same way as one that never existed, and the runner default
    is `actions/checkout` at depth 1 - so the overwhelmingly likely cause of a
    baseline that "does not exist" is that it was simply not fetched. Saying so here
    costs a sentence and saves the reader an afternoon.
    """
    try:
        out = sh_strict("git", "rev-parse", "--verify", "--quiet", f"{baseline}^{{commit}}")
    except GateError as exc:
        raise ConfigError(
            f"the configured baseline `{baseline}` cannot be resolved in this "
            f"checkout. Almost always a shallow clone: `actions/checkout` defaults to "
            f"fetch-depth: 1 and the baseline is by definition an old commit, so set "
            f"fetch-depth: 0. Failing that, the configured baseline is wrong. ({exc})"
        ) from exc
    sha = out.strip()
    if not sha:
        raise ConfigError(
            f"the configured baseline `{baseline}` cannot be resolved in this "
            f"checkout (git named no commit). Set fetch-depth: 0 if this is a "
            f"shallow clone; otherwise the configured baseline is wrong.")
    return sha


def require_ancestor(baseline_sha, head):
    """The baseline must be an ancestor of head, or the range means nothing.

    `git merge-base --is-ancestor` answers with an exit code and prints NOTHING, so a
    false comes back indistinguishable from a git failure. Compute the merge-base
    instead and compare: equal is an ancestor, unequal is a fork point somewhere else,
    and an unrelated history raises out of git_io with the fetch-depth wording.
    """
    mb = merge_base(baseline_sha, head).strip()
    if mb != baseline_sha:
        raise ConfigError(
            f"the configured baseline {baseline_sha[:12]} is NOT an ancestor of "
            f"{head}: they diverge at {mb[:12]}. Either the branch was rebased off "
            f"the fork point or the configured baseline names the wrong commit. "
            f"Nothing can be generated from a range that does not exist.")


def is_upstream_author(name, email, patterns):
    """Does this author match a configured upstream identity?

    fnmatch against the name and the address, case-insensitively - patterns, not
    equality, because one person arrives under several addresses and half of them are
    personal. A pattern with no wildcard is therefore an exact match, which is the
    point: substring matching would let `example.org` swallow `not-example.org.evil`,
    and an author wrongly classed as upstream is an entry that never appears. A
    missing entry is a compliance gap nobody can see; a spurious one is noise someone
    can delete. The failure direction is chosen, and it is over-reporting.
    """
    for pattern in patterns:
        p = pattern.lower()
        if fnmatch.fnmatch((email or "").lower(), p) or fnmatch.fnmatch((name or "").lower(), p):
            return True
    return False


def introduced_commits(sha, parents):
    """The commits a merge BROUGHT IN, or None when the commit is not a merge.

    For parents P1 (first) and P2 that is `P1..P2`; for an octopus it is the union,
    `P2 P3 ... ^P1`. The union is the conservative reading: a larger introduced set
    makes "every commit here is upstream's" harder to satisfy, and leaning towards
    inclusion is the failure direction this tool has chosen everywhere else.

    A range that cannot be READ raises. "Could not work out what this merge
    introduced" and "it introduced nothing of ours" decide opposite things about
    whether the entry is written at all, so they must never share a code path - and
    the likely causes (a shallow clone, a partial clone, a pruned object) are exactly
    the ones that produce an empty answer from a tolerant call.
    """
    if len(parents) < 2:
        return None
    first, rest = parents[0], parents[1:]
    fmt = RS + FS.join(["%an", "%ae", "%s"])
    try:
        out = sh_strict("git", "log", "--reverse", f"--format={fmt}",
                        *rest, f"^{first}", "--")
    except GateError as exc:
        raise GateError(
            f"could not list the commits {sha[:7]} brought in ({first[:7]}..{rest[0][:7]}"
            f"{' and further parents' if len(rest) > 1 else ''}): {exc}\n"
            f"Whether that merge is ours or upstream's is decided by those commits, so "
            f"this is not a merge with nothing in it - it is a merge nothing can be "
            f"said about. A shallow or partial clone is the usual cause; set "
            f"fetch-depth: 0.") from exc
    commits = []
    for record in out.split(RS):
        if not record.strip("\n"):
            continue
        fields = record.split(FS, 2)
        if len(fields) < 3:
            raise GateError(
                f"could not parse a `git log` record for {sha[:7]} "
                f"({len(fields)} of 3 fields): {record[:120]!r}")
        commits.append((fields[0], fields[1], fields[2].strip()))
    return commits


def is_upstream_change(name, email, introduced, patterns):
    """Is this entry upstream's work rather than ours?

    The author of a MERGE commit is whoever performed the merge, which on a fork is
    whoever was on duty - not whoever wrote the change. Reading that field lets every
    routine upstream sync we merged ourselves into the notice as our own modification,
    and drops our own work whenever an upstream maintainer or a bot did the merging.
    The second direction is the one that matters: a spurious entry is noise somebody
    deletes, a missing one is a compliance gap nobody can see.

    So a merge is upstream's only when EVERY commit it introduced is upstream-authored.
    A commit with no second parent has introduced nothing and is judged by its own
    author, which for a non-merge is the person who wrote it.
    """
    if introduced is None:
        return is_upstream_author(name, email, patterns)
    if not introduced:
        # A merge that brought in nothing at all - a second parent already reachable
        # from the first. "Every commit it introduced is upstream's" is vacuously true
        # of an empty set, and excluding an entry on a technicality is the failure
        # direction this tool does not take. No evidence means ours.
        return False
    return all(is_upstream_author(n, e, patterns) for n, e, _ in introduced)


def branch_names_in(subject):
    """Every spelling of the branch a merge subject names, lower-cased.

    `Merge pull request #34 from owner/fix/boo` names `owner/fix/boo`, and the branch
    a human would type is `fix/boo`; `Merge remote-tracking branch 'origin/topic'`
    names `origin/topic` and `topic`. Both spellings are kept because either can turn
    up as the body line git's own forms pre-fill.
    """
    m = re.match(r"^Merge pull request #\d+ from (\S+)", subject)
    if m:
        refs = [m.group(1)]
    elif MERGE_SUBJECT_RE.match(subject):
        refs = re.findall(r"'([^']+)'", subject)
    else:
        return set()
    names = set()
    for ref in refs:
        names.add(ref.lower())
        if "/" in ref:
            names.add(ref.split("/", 1)[1].lower())
    return names


def unusable_because(text, branches):
    """Why this candidate cannot describe the change, or None when it can.

    THE BRANCH-NAME RULE, stated rather than guessed: a candidate is merely a branch
    name when it contains NO WHITESPACE and equals a branch the merge subject itself
    names. Both halves are needed. `fix: don't crash on a/b paths` is an ordinary
    title with a slash in it, so "looks slashy" would delete real descriptions; and a
    one-word title like `Tidying` is kept, because nothing in the subject claims it is
    a ref. Anchoring to the subject is what makes the rule defensible: it recognises
    the specific thing that happens - the merge form is submitted with the branch name
    still in the description box - and nothing else.

    A merge template is rejected for the same reason as the branch name. `Merge pull
    request #19 from owner/thing` in a compliance notice names no change either, and
    it can arrive from the introduced side as easily as from the subject.
    """
    if not text:
        return "has no body"
    if MERGE_SUBJECT_RE.match(text):
        return f"has a body line that is itself a merge template ({text!r})"
    if not re.search(r"\s", text) and text.lower() in branches:
        return f"has a body line that is merely the branch name ({text!r})"
    return None


def join_subjects(subjects, cap=3):
    """Several introduced titles as ONE line, because one entry is one change.

    Three is as much of a list as a reader can use, and the count tells them there is
    more. Both are fixed by the merge's own history, so the line reads the same on
    every regeneration - which the append-only rule requires.
    """
    head = "; ".join(subjects[:cap])
    if len(subjects) <= cap:
        return head
    return f"{head} (+{len(subjects) - cap} more)"


def subject_for(subject, body, introduced, sha, warn):
    """The title of the CHANGE, which is not always the subject of the commit.

    A squash merge puts the pull request title in the subject. A true merge commit
    puts `Merge pull request #19 from owner/work/thing` there and the real title -
    the one a human wrote - on the first line of the body. Same history, two shapes,
    and reading the subject blindly fills the notice with entries saying "Merge
    branch", which is a notice that technically exists.

    The body line is not always a title either. Every merge form pre-fills the
    description with the branch name, so `- 2026-09-11  9c22a6c  fix/boo` is a real
    line in a real notice, and it tells its recipient nothing. In order:

        1. the first non-empty body line, when it describes something
        2. the subjects of the commits the merge brought in
        3. NO_DESCRIPTION, and a warning

    Step 3 is an entry that says so, not a hard error. The cause is a commit message,
    and a commit message cannot be corrected without rewriting history - so refusing
    to generate would wedge the notice for good over a line nobody can fix. The entry
    still has to be there: the date and the commit are the compliance-relevant part,
    and a reader who wants more can follow the SHA. What it may not do is quietly
    print a branch name, an empty string or git's template and let that pass for a
    description.
    """
    branches = branch_names_in(subject)
    if not MERGE_SUBJECT_RE.match(subject):
        if subject:
            return subject
        warn(f"{sha[:7]}: this commit has an empty subject, so the entry cannot say "
             f"what was modified. Recorded as {NO_DESCRIPTION}.")
        return NO_DESCRIPTION

    body_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
    complaint = unusable_because(body_line, branches)
    if complaint is None:
        return body_line

    usable = [s for _, _, s in (introduced or []) if unusable_because(s, branches) is None]
    if usable:
        warn(f"{sha[:7]}: this merge {complaint}; describing it with the commit(s) it "
             f"brought in instead. A merge with a written title reads better in the "
             f"notice.")
        return join_subjects(usable)

    warn(f"{sha[:7]}: this merge {complaint}, and the commits it brought in name no "
         f"change either, so the entry cannot say what was modified. Recorded as "
         f"{NO_DESCRIPTION}; only a rewritten history could improve it.")
    return NO_DESCRIPTION


def notice_path():
    """Where the notice belongs: the repository root, not wherever we are standing.

    `--check` reads the committed blob, and a blob path is always relative to the
    root. A bare `open(FILENAME, "w")` is relative to the process, so running --write
    from a subdirectory would write a SECOND notice that the check can never see -
    and the gate's own pattern is anchored to any path component, so it would start
    enforcing append-only on the stray one too.
    """
    return os.path.join(sh_strict("git", "rev-parse", "--show-toplevel").strip(),
                        FILENAME)


def touches_only_the_notice(sha):
    """Is this commit nothing but the notice being brought up to date?

    THE CIRCULARITY THIS RESOLVES, because it is not obvious and the rule reads like
    an exception otherwise. An entry carries the commit's short SHA, so the commit
    that WRITES the notice can never contain its own entry - amending changes the SHA
    the entry would have to name. Without this, the file is stale the instant it is
    committed, `--check` is permanently red in the steady state, and the regeneration
    that follows a merge adds a bookkeeping entry which the next one records in turn,
    one commit behind for ever. Half the notice would end up being the notice.

    So a commit whose diff touches NOTHING but the notice is bookkeeping, not a
    modification. A commit touching the notice AND anything else is a real change and
    stays: the boundary is "only", not "at all".

    `git diff --name-only <sha>^ <sha>` is deliberately per-commit rather than a
    clever single `git log`, because `--name-only` on a merge commit is a question
    with a surprising answer, and the direction the surprise runs is a commit read as
    notice-only and dropped from the record. An EMPTY file list is never notice-only
    either - an empty commit is a commit - for the same reason.
    """
    out = sh_strict("git", "diff", "--name-only", "-z", f"{sha}^", sha, "--")
    paths = [p for p in out.split("\0") if p]
    return paths == [FILENAME]


def collect_entries(baseline_sha, head, patterns, warn):
    """First-parent commits in baseline..head that are ours, oldest first.

    FIRST-PARENT so one entry is one change: a twelve-commit pull request landed as a
    merge is one line, not twelve. OLDEST FIRST so regenerating appends to the file
    rather than rewriting it - which is what keeps the notice append-only.

    The format is explicit and the separators are C0 controls, not newlines. Fields
    are split with a cap so a body can contain anything at all without reshaping the
    record, and a record with too FEW fields raises rather than being skipped. `%b` is
    LAST because it is the only field allowed to contain arbitrary text: put anything
    after it and the body quietly swallows it.

    The cost is a git call per first-parent commit, and now two for a merge: one to
    read what it introduced, one for the notice-only check. That is not reducible to a
    single traversal of `baseline..head`. A merge's second-parent side can reach back
    BEFORE the baseline, so `P1..P2` is not a subset of what that traversal lists, and
    computing membership locally from it would silently lose introduced commits - in
    the direction of reading our own work as upstream's, which is the failure this
    module is here to prevent.
    """
    fmt = RS + FS.join(["%H", "%h", "%an", "%ae", "%ad", "%s", "%P", "%b"])
    out = sh_strict("git", "log", "--first-parent", "--reverse", "--abbrev=7",
                    "--date=short", f"--format={fmt}", f"{baseline_sha}..{head}", "--")
    entries = []
    for record in out.split(RS):
        if not record.strip():
            continue
        fields = record.split(FS, 7)
        if len(fields) < 8:
            raise GateError(
                f"could not parse a `git log` record ({len(fields)} of 8 fields): "
                f"{record[:120]!r}")
        full, short, name, email, date, subject, parents, body = fields
        introduced = introduced_commits(full, parents.split())
        if is_upstream_change(name, email, introduced, patterns):
            continue
        if touches_only_the_notice(full):
            continue
        entries.append(
            (date, short, subject_for(subject.strip(), body, introduced, full, warn)))
    return entries


# -------------------------------------------------------------------------- render


def render(cfg, baseline_sha, entries):
    """The file, and nothing else in it.

    Note what is NOT here: no count, no generation timestamp, no trailing marker.
    `licence-gate.py` enforces the register as append-only - the base blob must be a
    byte-exact PREFIX of the head one - so anything printed after the entries moves
    on every regeneration and makes the generator's own output fail the gate it is
    meant to feed. The abbreviation length is pinned for the same reason: `%h` grows
    with the repository, and an unpinned one would silently rewrite every earlier
    line the day the repo crosses the threshold.
    """
    lines = [HEADING, "",
             f"Forked from {cfg['upstream']} at {baseline_sha}, "
             f"dated {cfg['baseline_date']}.", ""]
    lines += [f"- {date}  {short}  {subject}" for date, short, subject in entries]
    return "\n".join(lines) + "\n"


def parse_document(text):
    """(baseline_triple_or_None, [entry_triples]) out of a committed notice.

    PARSED, not normalised, because `--check` is asking "are the entries current" and
    nothing else. Reflowing the baseline sentence or leaving trailing whitespace on a
    line is not a stale notice, and a check that fails on those is a check people
    learn to ignore - at which point it is indistinguishable from one that does not
    run at all.
    """
    baseline, entries = None, []
    for line in text.splitlines():
        m = ENTRY_RE.match(line)
        if m:
            entries.append((m.group(1), m.group(2).lower(), m.group(3)))
            continue
        m = BASELINE_RE.match(line.strip())
        if m and baseline is None:
            baseline = (m.group(1), m.group(2).lower(), m.group(3))
    return baseline, entries


def _same_sha(a, b):
    """Two abbreviations of the same commit are the same commit."""
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]


# --------------------------------------------------------------------------- modes


def generate(repo, config_path, head, warn):
    cfg = load_config(config_path, repo)
    baseline_sha = resolve_baseline(cfg["baseline"])
    require_ancestor(baseline_sha, head)
    entries = collect_entries(baseline_sha, head, cfg["upstream_authors"], warn)
    return cfg, baseline_sha, entries


def check(repo, config_path, base, head, warn, out):
    """Advisory. Returns 0 when current, 1 when stale. Hard errors still raise.

    `base` does NOT influence what is generated. The range is always the configured
    baseline to `head`; base is here to be validated against head - a pull request
    whose base cannot be reached is a checkout problem, and this is the mode that runs
    on pull requests, so it is the mode that should say so. Wiring base into the
    generation range would make the notice depend on which branch happened to be
    open, which is the one thing a projection of history must not do.
    """
    cfg, baseline_sha, entries = generate(repo, config_path, head, warn)
    merge_base(base, head)

    committed = git_show(head, FILENAME)
    if committed is None:
        out(f"{FILENAME} does not exist at {head}; it would be created with "
            f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}.")
        _advice(out)
        return 1

    doc_baseline, doc_entries = parse_document(committed)
    if doc_baseline is not None and not _same_sha(doc_baseline[1], baseline_sha.lower()):
        raise ConfigError(
            f"{FILENAME} says the fork starts at {doc_baseline[1]}, and the config "
            f"says {baseline_sha[:12]}. One of the two is wrong and this tool cannot "
            f"know which: the file describes a different fork point, so every entry "
            f"in it is measured from somewhere else. Fix the config or the file "
            f"deliberately - do not let a regeneration paper over it.")
    # A file with no parseable baseline line at all is merely malformed, and
    # regenerating fixes it. Only DISAGREEMENT is the hard error: that is the case
    # where the tool would otherwise quietly overwrite a claim somebody made.

    if doc_entries == entries:
        return 0

    missing = [e for e in entries if e not in doc_entries]
    extra = [e for e in doc_entries if e not in entries]
    if doc_baseline is None:
        out(f"{FILENAME} has no `Forked from ...` line; a regeneration adds one.")
    for entry in missing:
        out(f"missing:  - {entry[0]}  {entry[1]}  {entry[2]}")
    for entry in extra:
        out(f"recorded but not derivable from the history:  - {entry[0]}  "
            f"{entry[1]}  {entry[2]}")
    if not missing and not extra:
        out(f"{FILENAME} has the right entries in the wrong order; oldest first.")
    _advice(out)
    return 1


def _advice(out):
    out("")
    out(f"This does not block: {FILENAME} is generated, never hand-written, and is "
        f"brought up to date automatically. Run `modifications.py --write` if you "
        f"would rather it were current in this branch.")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=f"Generate {FILENAME} from git history.")
    ap.add_argument("--check", nargs=2, metavar=("BASE", "HEAD"),
                    help="compare the committed file against the history (advisory)")
    ap.add_argument("--write", action="store_true",
                    help=f"regenerate {FILENAME} in place. No commit, no push.")
    ap.add_argument("--repo", help="owner/name, when there is no origin remote to ask")
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help="the baseline config, which lives in the tooling repository")
    args = ap.parse_args(argv)

    if args.check and args.write:
        ap.error("--check and --write are different modes; pick one")

    def warn(message):
        print(f"warning: {message}", file=sys.stderr)

    try:
        repo = repo_identity(args.repo)
        if args.check:
            return check(repo, args.config, args.check[0], args.check[1], warn, print)
        cfg, baseline_sha, entries = generate(repo, args.config, "HEAD", warn)
        text = render(cfg, baseline_sha, entries)
        if args.write:
            with open(notice_path(), "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"wrote {FILENAME}: {len(entries)} entr"
                  f"{'y' if len(entries) == 1 else 'ies'}.")
        else:
            sys.stdout.write(text)
        return 0
    except GateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
