#!/usr/bin/env python3
"""Apply a licence header to new files, on a human's explicit assertion of authorship.

Invoked by the `/std-licence` comment command. The command means "we wrote these";
this script decides *which* header follows from that, per path, and refuses anything
it cannot determine.

  apply-std-licence.py <base-sha> <head-sha> [explicit paths...]

Prints a markdown report of what was applied and what was refused, so a wrong
inference is visible rather than silent.
"""

import sys

from git_io import GateError, changed_files, merge_base
from licence_map import (APPLY, classify, has_licence_header,
                         insert_header, leading_comment_region)


def added_files(base, head):
    """New paths, and the subset of them that are not files at all.

    Returns (added, gitlinks).

    This used to be a second, independent enumerator over `git diff --name-status -z`.
    --name-status carries no MODE, so it reports a submodule exactly as it reports a
    file and this script could not see a gitlink at all. It failed safe only by
    accident: `open()` on the submodule path raises, `has_header` reads an unreadable
    file as "leave alone", and the path was refused with the reason "already carries a
    header" - a statement about content that does not exist in this repository. Two
    unrelated behaviours happening to line up is not a design, and nothing tested it.

    The gate had already replaced --name-status with `--raw -z` for exactly this
    reason. The fix did not propagate, because propagating fixes by hand across two
    copies of a parser is a thing people forget. There is now one implementation, in
    git_io, and "did it reach the other copy" is no longer a question anyone has to
    ask.
    """
    added, _modified, _deleted, _renamed, _pairs, gitlinks = changed_files(base, head)
    return added, gitlinks


def has_header(path):
    """Uses the one shared predicate. This was a third, separate implementation - a
    literal search for "SPDX-License-Identifier" in the first 15 lines - which agreed
    with neither of the other two. Three answers to "has this file got a header" is
    how a file our own tool stamped became invisible to the gate enforcing headers."""
    try:
        content = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        return True  # unreadable: treat as "leave alone"
    return has_licence_header(leading_comment_region(content))


def write_header(path, licence):
    """Pure decision in licence_map.insert_header; this only does the file IO."""
    content = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(insert_header(content, path, licence))


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    base, head, explicit = argv[0], argv[1], set(argv[2:])
    # ONE resolution, before anything reads git, exactly as licence-gate.py does it.
    # What arrives is the base BRANCH'S TIP, so everything the base branch has done
    # since the branch was cut is billed to this pull request in reverse: a file
    # DELETED on the base branch comes back as a file this pull request ADDED, and
    # naming it in the command stamps a first-party copyright header onto content
    # this branch never contributed - under an assertion nobody made about it.
    # merge_base raises rather than falling back: an unresolvable base means nothing
    # was examined, which is not the same thing as nothing needing a decision.
    base = merge_base(base, head)
    # Paths may also arrive via a file, so a comment body never reaches a shell.
    try:
        with open("/tmp/explicit-paths.txt", encoding="utf-8") as fh:
            explicit |= {l.strip() for l in fh if l.strip()}
    except OSError:
        pass

    applied, refused = [], []
    added, gitlinks = added_files(base, head)
    for path in added:
        # Filtered here, by mode, and not inside classify(): licence_map is the pure
        # decision module and knows nothing about git. A submodule holds content this
        # repository neither stores nor wrote, so an assertion of authorship over it
        # is meaningless however explicitly it was named - and stamping a header would
        # write into the submodule's own checkout, not into this repository at all.
        # It is refused with a reason that is TRUE, which the accident it replaces was
        # not: "already carries a header" was said about a path with no content here.
        if path in gitlinks:
            refused.append((path, "a submodule, not a file in this repository - its "
                                  "content and its licence belong to the repository it "
                                  "points at; record it, do not stamp it"))
            continue
        action, lic, reason = classify(path, path in explicit, has_header(path))
        if action == APPLY:
            write_header(path, lic)
            applied.append((path, lic))
        else:
            refused.append((path, reason))

    out = []
    if applied:
        out.append(f"Applied a header to {len(applied)} file(s):\n")
        for p, lic in applied:
            out.append(f"- `{p}` — `{lic}`")
        out.append("")
    if refused:
        out.append(f"**Refused {len(refused)} file(s)** — these need a person:\n")
        for p, why in refused:
            out.append(f"- `{p}` — {why}")
        out.append("")
    if not applied and not refused:
        out.append("No new files needing a licence decision.")
    if applied:
        out.append("The licence for each file follows from where it sits in the tree. "
                   "The copyright line reflects your assertion that this is our work — "
                   "if any of the above is not ours, say so and it will be reverted.")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except GateError as exc:
        # Said out loud, and exiting non-zero so the calling action stops before its
        # commit-and-push step. "No new files needing a licence decision." is a
        # statement about the tree; this could not read the tree, so it is not
        # entitled to make one.
        print(f"**The licence-header tool could not run.**\n\n```\n{exc}\n```\n\n"
              f"No file was changed and nothing was recorded. This is not the same as "
              f"\"no new files needed a decision\" - nothing was examined at all. A "
              f"common cause is a checkout without full history.")
        sys.exit(2)
