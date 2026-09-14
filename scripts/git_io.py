#!/usr/bin/env python3
"""Running git. One rule the rest of the tooling depends on:

    an empty answer from git is never the same thing as "nothing to report".

Every git command goes through here. Separate module because `licence-gate.py`
has a dash in its name and cannot be imported, so apply-std-licence.py once
reused a tolerant copy of these helpers and kept a false all-clear the gate had
already fixed.
"""

import subprocess


class GateError(RuntimeError):
    """The gate could not run. Never the same thing as "nothing to report"."""


def _run(*args):
    """Run a git command, decoding with errors="replace".

    `text=True` with no `errors=` raises UnicodeDecodeError *inside*
    subprocess.run, before the returncode is seen, so it escapes the caller's
    handlers. A pull request touching a PNG was enough: `git diff` emits binary
    bytes, the gate died with a traceback, the report file was never written, and
    the empty comment body read as "ran, found nothing". Replacing undecodable
    bytes leaves a binary readable as text with no licence in it.
    """
    return subprocess.run(args, capture_output=True, text=True, errors="replace")


def sh_strict(*args):
    """For commands whose failure means the gate did not run.

    The tolerant version - return stdout, ignore the exit code - was catastrophic
    for enumerating the diff: an unreachable base commit (a force-push between the
    event and the job, a missing fetch-depth: 0, a gc'd object) produced empty
    output, so every list was empty and the report certified a clean tree having
    analysed nothing. No attacker needed; any git error on the runner did it. So a
    command whose failure means the gate did not run must raise, not return empty.
    """
    r = _run(*args)
    if r.returncode != 0:
        raise GateError(f"`{' '.join(args)}` failed ({r.returncode}): "
                        f"{r.stderr.strip() or 'no error output'}")
    return r.stdout


def merge_base(base, head):
    """The commit the two actually diverged at. Raises if it cannot be computed.

    A pull_request event hands the gate `pull_request.base.sha` - the base branch's
    current tip, not the fork point. Diffing tip..head replays everything the base
    branch gained since against the author: a header added on main last week reads
    as one this pull request removed, from a file it never touched. Resolve to the
    merge-base once, here, and hand the one commit to the enumeration and to every
    `git show` - three-dot diff syntax would fix only the file list and leave the
    blobs at the tip, the same defect with fewer symptoms.

    Empty stdout raises. `git diff --raw -z "..HEAD"` is not an error to git - it
    reads the empty side as HEAD and reports no change, exit 0 - so an unresolvable
    base would pass as a clean tree. The message names fetch depth because git will
    not: `actions/checkout` defaults to depth 1, the usual cause, and an
    unrelated-histories failure exits 1 with nothing on stderr.
    """
    try:
        out = sh_strict("git", "merge-base", base, head)
    except GateError as exc:
        raise GateError(
            f"could not compute the merge-base of {base} and {head}: {exc}\n"
            f"The commits have no common history the checkout can see. Almost always a "
            f"shallow clone - `actions/checkout` defaults to fetch-depth: 1, and the "
            f"gate needs enough history to find where the branch was cut; set "
            f"fetch-depth: 0. Failing that, the two really are unrelated histories.") \
            from exc
    mb = out.strip()
    if not mb:
        raise GateError(
            f"`git merge-base {base} {head}` succeeded but named no commit. The base "
            f"cannot be determined, which is not the same as nothing having changed; "
            f"a checkout without full history (fetch-depth: 0) is the usual cause.")
    return mb


def git_show(rev, path):
    """The content of `path` at `rev`, or None when git could not produce it.

    The tolerant helper this replaces returned stdout and ignored the exit code, so
    a failed `git show` (empty stdout, error on stderr) read as "an empty file,
    nothing to check". A gitlink (mode 160000) made it a hole: git cannot produce a
    blob for a submodule, so unrecorded vendored content was dropped by every check.
    None means "could not read", "" means a genuinely empty file; conflating them
    reintroduces the bug. An empty file is legitimate and must not crash the gate.
    """
    r = _run("git", "show", f"{rev}:{path}")
    return None if r.returncode != 0 else r.stdout


# A submodule entry: no blob behind it, its content lives in another repository,
# which is why it needs a human.
GITLINK_MODE = "160000"


def changed_files(base, head):
    """Enumerate and classify the diff. The one implementation; do not add another.

    Returns (added, modified, deleted, renamed, pairs, gitlinks).

    `pairs` carries per-end modes - (old_path, new_path, old_is_gitlink,
    new_is_gitlink); `gitlinks` is the union of both ends. They are not
    interchangeable: a licensed file replaced by a submodule pointer has one path,
    one gitlinks entry, and a readable blob at the base, so a caller that skips the
    path because it is in `gitlinks` skips the deletion of a header. Ask the end you
    are about to read, not the path.

    One implementation, here, because there were two - the gate's `git diff --raw
    -z` and apply-std-licence.py's `--name-status -z`, which carries no mode and so
    cannot see a gitlink. A fix to one never reached the other, and the copy without
    it holds `contents: write`.

    -z, because otherwise git C-quotes any path with a tab, backslash, quote, or
    (under core.quotePath) a non-ASCII byte, and the quoted form matches no file
    downstream - so any commit touching café.js or 中文.py was skipped in silence.

    --raw not --name-status, for the modes: --name-status reports a submodule as an
    ordinary file, so the only way to spot a gitlink was to read it and watch the
    read fail. git already knows what each entry is; ask it once, here.

    Renames: git reports delete-plus-add of similar content as one `R<score>` record
    with two paths, and a rename is the commonest shape of a replacement. Handling
    only A/D/M let renames fall through with no candidate at all.
    """
    out = sh_strict("git", "diff", "--raw", "-z", f"{base}..{head}")
    toks = out.split("\0")
    added, modified, deleted, renamed, pairs, gitlinks = [], [], [], [], [], set()
    i = 0
    while i < len(toks):
        meta = toks[i]
        if not meta.startswith(":"):
            i += 1
            continue
        # :<old-mode> <new-mode> <old-sha> <new-sha> <status>
        fields = meta[1:].split()
        if len(fields) < 5:
            raise GateError(f"could not parse `git diff --raw` record {meta!r}")
        status = fields[4]
        old_is_link = fields[0] == GITLINK_MODE
        new_is_link = fields[1] == GITLINK_MODE
        if status[0] in ("R", "C"):
            # status, old, new
            if i + 2 >= len(toks):
                break
            old, new = toks[i + 1], toks[i + 2]
            renamed.append((old, new))
            pairs.append((old, new, old_is_link, new_is_link))
            if old_is_link:
                gitlinks.add(old)
            if new_is_link:
                gitlinks.add(new)
            i += 3
            continue
        if i + 1 >= len(toks):
            break
        path = toks[i + 1]
        if old_is_link or new_is_link:
            gitlinks.add(path)
        if status[0] == "A":
            added.append(path)
            pairs.append((None, path, False, new_is_link))
        elif status[0] == "D":
            deleted.append(path)
            pairs.append((path, None, old_is_link, False))
        else:
            # Anything not A or D is a modification, including status letters not
            # named here. A bare `elif M` dropped the rest: a licensed file replaced
            # by a symlink is reported as T, so it reached no check while its header
            # ceased to exist. Unknown status is not a reason to stop checking.
            modified.append(path)
            pairs.append((path, path, old_is_link, new_is_link))
        i += 2
    return added, modified, deleted, renamed, pairs, gitlinks
