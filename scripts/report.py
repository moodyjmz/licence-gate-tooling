#!/usr/bin/env python3
"""The review comment, rendered from the checks' findings. Pure: no git, no IO.

Lifted OUT of licence-gate.py unchanged. It is pure string assembly over the checks'
outputs and was the only part of the gate that could not be exercised without
building a throwaway repository first - so the one part nobody added a case to.

Moved verbatim on purpose: this commit changes where the renderer lives and nothing
about what it writes, so the existing tests are the proof the move was faithful.
"""


def render(*, a, b, c, g, d_src, d_assets, d_links, d_foreign, claims,
           binary_skips, deleted_headers, register_touched, head_short, author,
           notice):
    """The comment body, as a string. Every argument is a finding list; none is git.

    `register_touched` is whether a register file appeared in this diff at all -
    passed in rather than recomputed, because the verified box may not claim that
    rows survived in a pull request that never touched one.
    """
    out = []

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
        out.append(f"```\n * {notice}\n```")
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
        out.append("\n`/auto-fix` deliberately will not touch these: the right "
                   "copyright holder depends on where the content came from, and "
                   "guessing is how someone else's work ends up carrying yours.\n")
        if d_src:
            # The report said "by hand" while the command that does it existed and went
            # unmentioned. A tool nobody is told about is a tool nobody uses, and the
            # instruction it replaced was the vaguest sentence in the whole report.
            named = " ".join(d_src)
            out.append("**If these are ours, say so and the headers are applied:** post "
                       "a comment reading\n")
            out.append("```\n/std-licence {}\n```".format(named))
            out.append("\nThat command is an assertion — *we wrote these* — which is why "
                       "it names every file rather than taking them all in one sweep, "
                       "and why a machine cannot issue it. The licence follows from "
                       "where each file sits in the tree; the copyright line records "
                       "your word for it.\n")
            out.append("If any of them is **not** ours — vendored, copied, generated "
                       "from something else — leave it out and add its real header by "
                       "hand.\n")

    if claims:
        out.append("### Ownership claimed — {} new line(s)\n".format(len(claims)))
        out.append("Someone has **added** a copyright or licence statement. Nothing was "
                   "removed, so the checks above have nothing to say about it — but a "
                   "file now asserts something it did not assert before, and only a "
                   "person can tell whether that is true.\n")
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
        # Say what to DO first, and only then what it means. This block described its
        # own purpose - "the commit line is required: it is what ties your signature to
        # the tree you actually looked at" - which is true, reads as homework, and
        # never mentions that the line is already filled in. The first person to meet
        # it asked what a register was and whether they had to go and find the commit.
        # Neither question should have been possible: there is nothing to look up and
        # nothing to compose.
        out.append("\n**What to do — four steps, nothing to look up:**\n")
        out.append("1. Copy the block below.")
        out.append("2. Paste it into the comment box at the bottom of this pull request.")
        out.append("3. After each `->`, type your answer.")
        out.append("4. Post the comment.\n")
        out.append("The check re-runs on its own within a minute or so and turns green. "
                   "You do not need to do anything else, and nobody has to re-run it "
                   "for you.\n")
        out.append("```\nexample-log:")
        # Resolve, never echo. This printed whatever ref it was handed, so a caller
        # passing a branch name put a branch name in the block the reviewer copies -
        # while the acknowledgement gate compares against the head SHA. The reviewer
        # would have followed the instructions exactly and been told, for ever, that
        # their disposition did not name the current head.
        out.append(f"  commit: {head_short}")
        for p, _ in c:
            out.append(f"  {p} -> ")
        out.append("```")
        out.append("\nAn answer is either a **register ID** (if the file replaces "
                   "something) or the words **`not a replacement`** (if it does not). "
                   "Every line needs one — a blank is not an answer, and the gate will "
                   "say so.\n")
        out.append("The `commit:` line is already filled in; leave it as it is. It "
                   "records which version you read, so if anyone pushes again your "
                   "sign-off lapses instead of silently covering code you never saw.\n")
        out.append("It cannot be the pull request's author who posts this. The record is "
                   "only worth having because a second person looked.\n")

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
    if not g and register_touched:
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

    return "\n".join(out)
