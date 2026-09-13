#!/usr/bin/env python3
"""Running git, and the one rule the rest of the tooling depends on:

    an empty answer from git is never the same thing as "nothing to report".

Every command that talks to git goes through here. It lives in its own module
because `licence-gate.py` has a dash in its name and cannot be imported, so
apply-std-licence.py could not reuse the strict helpers and went on running the
tolerant version of the same code - which is exactly how it kept the false
all-clear that the gate had already had fixed.
"""

import subprocess


class GateError(RuntimeError):
    """The gate could not run. Never the same thing as "nothing to report"."""


def _run(*args):
    """Run a git command, decoding tolerantly.

    `text=True` with no `errors=` raises UnicodeDecodeError *inside*
    subprocess.run, before any returncode is looked at, so it escapes every
    handler wrapped around the call. An ordinary pull request touching a PNG was
    enough: `git diff` emits the binary bytes, the gate died with a traceback, and
    the report file the review comment is built from was never written - so the
    comment body was empty, which reads exactly like "ran, found nothing".

    Replacing undecodable bytes keeps the failure where it belongs: a binary file
    has no licence text in it, so the checks find nothing in it and say so.
    """
    return subprocess.run(args, capture_output=True, text=True, errors="replace")


def sh_strict(*args):
    """For commands whose failure means the gate did not run.

    The tolerant version of this - return stdout, ignore the exit code - was
    catastrophic for enumerating the diff: an unreachable base commit (a force-push
    between the event and the job, a caller workflow missing fetch-depth: 0, a
    garbage-collected object) produced empty output, so every list was empty, every
    check found nothing, and the report certified a clean bill of health having
    analysed precisely nothing. Not an attack; any git hiccup on the runner did it,
    on every pull request at once.

    A gate that cannot run must say so and block. Silence is the one answer it is
    never entitled to give.
    """
    r = _run(*args)
    if r.returncode != 0:
        raise GateError(f"`{' '.join(args)}` failed ({r.returncode}): "
                        f"{r.stderr.strip() or 'no error output'}")
    return r.stdout


def merge_base(base, head):
    """The commit the two actually diverged at. Raises if it cannot be computed.

    The base a pull request event hands the gate is `pull_request.base.sha` - the base
    BRANCH'S CURRENT TIP, not the point the branch was cut. Diffing tip..head replays
    everything the base branch gained since, in reverse, against the author: a licence
    header added on main last week is reported as a header THIS pull request removed,
    on a file it never touched, with a modification notice demanded for good measure.
    Every stale branch showed the base branch's own recent work as its crimes, and the
    noise was read as the gate working.

    The answer is not `...` in the diff. Three-dot syntax fixes the enumeration and
    leaves check A and check B reading their blobs at the branch tip, so the list of
    files and the content behind them disagree about which commit "base" is - the same
    defect, quieter. Resolve once, here, and hand the ONE commit to the enumeration and
    to every `git show`.

    Empty stdout raises, and that is not belt-and-braces. `git diff --raw -z "..HEAD"`
    is not an error to git: it reads the empty side as HEAD, reports no change at all,
    and exits 0. An unresolvable base would come back as a clean bill of health for a
    tree nothing had looked at - "could not determine the base" and "nothing changed"
    sharing a code path, which is the one thing this module exists to prevent.

    The message names fetch depth because git will not. Unrelated histories exit 1 with
    NOTHING on stderr, so sh_strict's own wording would be "failed (1): no error
    output" - true, and useless to the person who has to fix it. A shallow checkout is
    the overwhelmingly likelier cause on a runner: `actions/checkout` defaults to depth
    1, and one commit per side has no common ancestor to find.
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

    The distinction is the whole point. The tolerant helper this replaces returned
    stdout and ignored the exit code, so callers wrote `if content and ...` and a
    failed `git show` - which writes to stderr and leaves stdout empty - was read as
    "an empty file, nothing to check". A gitlink (mode 160000) is the case that made
    that a hole: git cannot produce a blob for a submodule entry, so unrecorded
    vendored content was dropped by every check while the report certified a clean
    tree.

    None means "could not read", "" means a genuinely empty file, and a caller that
    conflates the two is reintroducing the bug. An empty file is legitimate and must
    never crash the gate.
    """
    r = _run("git", "show", f"{rev}:{path}")
    return None if r.returncode != 0 else r.stdout


# A submodule entry in the tree. There is no blob behind it: its content lives in
# another repository entirely, which is precisely why it needs a human.
GITLINK_MODE = "160000"


def changed_files(base, head):
    """Enumerate and classify the diff. THE one implementation; do not write another.

    Returns (added, modified, deleted, renamed, pairs, gitlinks).

    `pairs` carries PER-END modes - (old_path, new_path, old_is_gitlink,
    new_is_gitlink) - and `gitlinks` is the union of both ends, which is what a caller
    asking "is this entry a submodule" wants. The two are not interchangeable and
    conflating them was a bug: a licensed file REPLACED by a submodule pointer has one
    path, one entry in `gitlinks`, and a perfectly readable blob at the base. A caller
    that skipped the path because it appeared in `gitlinks` skipped the deletion of an
    entire licence header. Ask the end you are about to read, not the path.

    It lives here, and not in licence-gate.py where it grew up, because there were
    TWO of these. The gate's used `git diff --raw -z`; apply-std-licence.py's used
    `--name-status -z`, which carries no mode and therefore cannot see a gitlink at
    all. The gate had that hole fixed; the second copy never heard about it, and the
    only thing keeping it safe was a coincidence - `open()` on a submodule path
    happens to fail, `has_header` treats an unreadable file as "leave alone", and the
    file was refused for a reason that was not true. Nothing tested that coincidence,
    and the script relying on it is the one holding `contents: write`.

    Two copies of a parser is the seam the next defect lands in: every fix has to be
    remembered twice, by someone who does not know the second copy exists. One
    implementation removes the question rather than answering it.

    Everything below is a defect that reached production in the copy that survived.

    -z, because without it git C-QUOTES any path containing a tab, a backslash, a
    double quote, or - under the default core.quotePath - any non-ASCII byte. The
    tab-split version handed `"src/h\303\251ader.py"`, quotes and octal escapes
    included, to every downstream `git show` and `git diff`, which then matched no
    file at all. `git show` wrote its error to stderr, the tolerant helper captured
    only stdout, and an empty result was treated as "nothing to check" - so every
    check skipped the file in silence while the report certified that they passed.
    That needed no attacker. Any commit touching café.js, handbog.md or 中文.py
    was invisible to the gate.

    --raw rather than --name-status, for the MODES. --name-status reports a submodule
    exactly as it reports a file - `A<TAB>vendor/thing` - so the only way to notice a
    gitlink was to try to read it and watch what happened, and what happened was an
    empty `git show` that every check read as "nothing to check". A whole class of
    entries that are not files was being classified by whether reading it failed. git
    already knows what each entry IS; ask it once, here, and every caller knows too.

    Renames matter more than they look. git reports a delete plus an add of similar
    content as a single `R<score>` record with TWO paths - and a rename is the
    commonest shape a replacement takes: swapping one asset for a differently-named
    one. An earlier version handled only A/D/M, so renames fell through silently and
    produced no candidate at all.
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
            # Anything not A or D is treated as a modification, including status
            # letters this loop does not name. A bare `elif M` silently dropped
            # everything else: replacing a licensed file with a symlink is reported as
            # T (type change), so the file entered no list, reached no check, and the
            # report stated that no copyright line had been altered while the entire
            # header had just ceased to exist. Unknown input is not permission to stop
            # checking.
            modified.append(path)
            pairs.append((path, path, old_is_link, new_is_link))
        i += 2
    return added, modified, deleted, renamed, pairs, gitlinks
