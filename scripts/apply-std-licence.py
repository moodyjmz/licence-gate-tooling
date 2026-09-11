#!/usr/bin/env python3
"""Apply a licence header to new files, on a human's explicit assertion of authorship.

Invoked by the `/std-licence` comment command. The command means "we wrote these";
this script decides *which* header follows from that, per path, and refuses anything
it cannot determine.

  apply-std-licence.py <base-sha> <head-sha> [explicit paths...]

Prints a markdown report of what was applied and what was refused, so a wrong
inference is visible rather than silent.
"""

import subprocess
import sys

from licence_map import APPLY, classify, insert_header


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def added_files(base, head):
    out = sh("git", "diff", "--name-status", f"{base}..{head}")
    added = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if parts[0].startswith("A"):
            added.append(parts[-1])
    return added


def has_header(path):
    try:
        head = "".join(open(path, encoding="utf-8").readlines()[:15])
    except (OSError, UnicodeDecodeError):
        return True  # unreadable: treat as "leave alone"
    return "SPDX-License-Identifier" in head


def write_header(path, licence):
    """Pure decision in licence_map.insert_header; this only does the file IO."""
    content = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(insert_header(content, path, licence))


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    base, head, explicit = argv[0], argv[1], set(argv[2:])
    # Paths may also arrive via a file, so a comment body never reaches a shell.
    try:
        with open("/tmp/explicit-paths.txt", encoding="utf-8") as fh:
            explicit |= {l.strip() for l in fh if l.strip()}
    except OSError:
        pass

    applied, refused = [], []
    for path in added_files(base, head):
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
    sys.exit(main(sys.argv[1:]))
