#!/usr/bin/env python3
"""The review comment, rendered from the checks' findings. Pure: no git, no IO.

SEPARATE FROM THE CHECKS because it is pure string assembly over their outputs and
was the only part of the gate that could not be tested without building a throwaway
repository first. A renderer tested through git is a renderer nobody adds a case to.

TERSE ON PURPOSE, and that is a correction rather than a preference. The report this
replaces ran to 671 words on a pull request with three findings; the findings
themselves accounted for about sixty. The rest was rationale, and it was not even
said once - "someone else's work ends up carrying your copyright" appeared twice in
one section, and the bar on the author signing off appeared three times in one
comment.

The failure that put the rationale there is real and is NOT being undone. The report
once stated every fact and left the reader unable to act, so a "what has to happen"
block was added at the top. What went wrong afterwards is that the sections below it
went on explaining the same things at length, so the summary and the body became two
accounts of one pull request - and a reader who finds the second account longer stops
believing the first one is complete.

So: the summary IS the report. Every finding appears exactly once, under the action
that clears it. WHY the gate asks is documentation and moves behind a fold - once, at
the bottom, where a curious reader finds it and a working reviewer does not have to.

THE RULE FOR ANYONE EDITING THIS: a sentence explaining why the tool exists does not
belong in a pull request comment. A sentence telling the reader what to type does.
When those are hard to tell apart, ask whether a reviewer who already agrees with the
tool still needs the sentence to finish the task.

WHAT MUST SURVIVE ANY FURTHER TRIM, because each is a defect the report has already
had:
  * every blocking finding names the files, not just a count. A count is not
    actionable and the reader has to go and find them.
  * a remedy the reader is BARRED from carrying out must say so where the remedy is
    given. The report once told the only person reading it to do the one thing they
    could not do, and never mentioned the bar.
  * the verified box may only claim what the run established. It is the one part
    that tells the reader NOT to look.
  * the disposition template is pre-filled and stays that way. It costs the reviewer
    nothing and the acknowledgement gate compares against the head it names.
"""

DOCS = ("https://github.com/moodyjmz/licence-gate-tooling"
        "/blob/main/README.md#the-actions")


def _files(paths, indent=""):
    """Findings are listed FLUSH, one per line, as `- \`path\` — reason`.

    That shape is read back: the integration suite recovers a path's findings by line
    prefix, and a reader skimming a long report scans the left margin. Indenting them
    under their heading renders as a nested list and breaks both."""
    return [f"{indent}- `{p}`" for p in paths]


def _plural(n, one, many=None):
    return one if n == 1 else (many or one + "s")


def render(*, a, b, c, g, d_src, d_assets, d_links, d_foreign, claims,
           binary_skips, deleted_headers, register_touched, head_short, author,
           notice):
    """The comment body, as a string. Every argument is a finding list; none is git.

    `register_touched` is whether a register file appeared in this diff at all -
    passed in rather than recomputed, because the verified box may not claim that
    rows survived in a pull request that never touched one.
    """
    out = []
    reviewer = f"a reviewer (not @{author})" if author else "a reviewer, not the author"

    # ONE LINE THAT SAYS WHETHER THIS MERGES. The reader's first question, answered
    # before anything else, and in the two words the rest of the comment is about.
    #
    # It does NOT name the gate. The posting step wraps this body under its own
    # "## Licence gate", so naming it here put the name twice in three lines - a
    # heading that repeats its parent tells the reader nothing and costs them the
    # first line of the comment, which is the one line that has their attention.
    if a or b or d_src or g:
        out.append("### 🚫 Blocked\n")
    elif c or claims or d_assets or d_links or d_foreign:
        out.append("### ⏳ Waiting on a reviewer\n")
        out.append("Nothing here blocks the merge on its own.\n")
    else:
        out.append("### ✅ Nothing to do\n")

    # ---------------------------------------------------------------- blocking
    #
    # One heading per ACTION, with its files under it. The report this replaces gave
    # each finding a count in a summary and then a section of its own lower down, so
    # every file was introduced twice and its remedy stated twice.
    step = 0
    if g:
        step += 1
        out.append(f"**{step}. Put back the register {_plural(len(g), 'row')}** — "
                   f"anyone with write access, the author included.")
        out.append("Restore the rows exactly as they were and add new ones beneath. "
                   "A row that is genuinely wrong gets a new row correcting it, never "
                   "an edit.")
        for p, why in g:
            out.append(f"- `{p}` — {why}")
        out.append("")
    if b:
        step += 1
        out.append(f"**{step}. Restore {len(b)} licence "
                   f"{_plural(len(b), 'line')}** — by hand; this cannot be automated.")
        out.append("Put the original line back exactly, then add anything new beneath "
                   "it.")
        for p, line in b:
            out.append(f"- `{p}`\n  ```\n  - {line}\n  ```")
        out.append("")
    if a:
        step += 1
        out.append(f"**{step}. Add the modification notice to {len(a)} "
                   f"{_plural(len(a), 'file')}** — comment `/auto-fix` and apply the "
                   f"patch it posts.")
        out.extend(_files(a))
        out.append("")
    if d_src:
        step += 1
        out.append(f"**{step}. Say who wrote {len(d_src)} new "
                   f"{_plural(len(d_src), 'file')}.** If they are ours, comment:")
        out.append("```\n/std-licence {}\n```".format(" ".join(d_src)))
        out.append("If any is not ours — vendored, copied, generated — leave it out of "
                   "that command and add its real header by hand.")
        out.extend(f"- `{p}` — no licence header" for p in d_src)
        out.append("")

    # ------------------------------------------------------- for a human to answer
    #
    # Assets, submodules and foreign-headered additions are listed WITH the candidates
    # they are already in rather than in a section of their own. They were duplicated:
    # the same path appeared under "new files" with a question, and again under
    # "candidates" with a reason, and the reviewer answered it once.
    extra = ([(p, "asset added — where is it from?") for p in d_assets]
             + [(p, "submodule added — what is it, and under which licence?")
                for p in d_links]
             + [(p, why) for p, why in d_foreign])
    if claims:
        out.append(f"**{len(claims)} new ownership "
                   f"{_plural(len(claims), 'claim')}** — a file now asserts something "
                   f"it did not before. Confirm it in the disposition below, or remove "
                   f"the line.")
        for path, line in claims:
            out.append(f"- `{path}` — `{line}`")
        out.append("")

    seen = {p for p, _ in c}
    for p, why in extra:
        if p not in seen:
            c = list(c) + [(p, why)]
            seen.add(p)

    if c:
        out.append(f"**{len(c)} {_plural(len(c), 'candidate')} for "
                   f"{reviewer}.** Detection over-reports on purpose, so \"no\" is a "
                   f"normal answer — but every line needs one.")
        for p, why in c:
            out.append(f"- `{p}` — {why}")
        out.append("\nPaste this as a comment on this pull request. The check re-runs "
                   "on its own.\n")
        # The guidance goes INSIDE the block, beside the line it is about. It used to
        # follow as three paragraphs, which is where a person copying a template is
        # no longer reading.
        # One comment column, so the annotations read as a column rather than as
        # ragged noise trailing each line.
        rows = [(f"  commit: {head_short}", "leave as-is — the version you read")]
        rows += [(f"  {p} ->", "a register ID, or: not a replacement") for p, _ in c]
        col = max(len(left) for left, _ in rows) + 2
        out.append("```")
        out.append("example-log:")
        out.extend(f"{left.ljust(col)}# {note}" for left, note in rows)
        out.append("```\n")

    # ------------------------------------------------------------------- the folds
    #
    # Unchanged in substance. The verified box may only carry what this run actually
    # established, so the caveats stay exactly as they were.
    verified = []
    if not b:
        caveats = []
        if binary_skips:
            caveats.append(f"{len(binary_skips)} binary {_plural(len(binary_skips), 'file')} "
                           f"{_plural(len(binary_skips), 'has', 'have')} no lines to compare")
        if deleted_headers:
            caveats.append(f"{len(deleted_headers)} licensed "
                           f"{_plural(len(deleted_headers), 'file')} "
                           f"{_plural(len(deleted_headers), 'was', 'were')} deleted outright")
        verified.append(
            "no licence or copyright line deleted or altered"
            if not caveats else
            "no licence or copyright line deleted or altered in the files that still "
            "exist as text ({} — listed above for your decision instead)"
            .format(", ".join(caveats)))
    if not a:
        verified.append("every modified file with a header carries a notice")
    if not g and register_touched:
        verified.append("every row already recorded in the register is still there")
    if verified:
        out.append("<details><summary>✅ Verified automatically — you need not check "
                   "these</summary>\n")
        out.extend(f"- {v}" for v in verified)
        out.append("\n</details>\n")

    out.append("<details><summary>⚠️ Not checked — these need a human</summary>\n")
    out.append("- whether each candidate above is genuinely a replacement")
    out.append("- whether a stated rationale is the real one")
    out.append("- where an added asset actually came from")
    out.append("\n</details>\n")

    # The rationale, once, behind a fold - and mostly by reference. The README is
    # pinned at a SHA in every consuming workflow, so a link to it cannot drift from
    # the code that is running.
    if a or d_src or c:
        out.append("<details><summary>ℹ️ Why the gate asks for these</summary>\n")
        if a:
            out.append(f"- The notice is the fixed string `{notice}`, appended inside "
                       f"a header block that already exists. `/auto-fix` posts it as a "
                       f"patch rather than pushing: a bot push would make the bot the "
                       f"last pusher, which hands someone a self-approval and dismisses "
                       f"reviews already given.")
        if d_src:
            out.append("- New files are never stamped mechanically. The licence "
                       "follows from the path; the copyright holder follows from who "
                       "wrote it, which no diff can tell. Guessing is how someone "
                       "else's work ends up carrying yours.")
        if c:
            out.append("- The disposition must come from a second person, and it names "
                       "a commit so that a later push lapses the sign-off instead of "
                       "silently covering code nobody read.")
        out.append(f"\n[Full documentation]({DOCS})\n")
        out.append("</details>")

    return "\n".join(out).rstrip() + "\n"
