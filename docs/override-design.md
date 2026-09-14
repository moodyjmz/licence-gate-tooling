# Attributed override — design note

Status: **not built.** This records the intended shape and its sharp edges so the
feature is designed deliberately, not bolted on. See AGENTS.md, "The register is the
product".

## The gap

Checks A, B, D(source) and G hard-block: `blocking = bool(a or b or d_src or g)`,
`return 1`. There is no command to clear a block. With `enforce_admins` on, the only
way past one today is a code fix or an admin force-push — and a force-push records
nothing about who decided the stop was wrong or why. The tooling's purpose is a record
of who decided what; a stop that can only be cleared invisibly is a hole in that
record, not a strength.

## Principle

A reviewer can clear any *blocking* stop the gate raises, with one exception below.
Clearing it is a **decision that is recorded and attributed**, not a bypass. The
difference from an admin force-push is the whole point: the override names who took the
responsibility, against which commit, and why.

**The register is the exception: it can never be rewritten, only appended.** Check G
is not overridable, at any signature count. A rewrite is not a decision to ratify; it
is undone by restoring the rows and appending beneath them, a mechanical fix always
available to the author. An append-only record whose removals can be waived is not
append-only.

## Shape (reuse, don't rebuild)

The acknowledgement gate already has every part this needs. An override is a
disposition aimed at a blocking check instead of a candidate:

- **Trigger:** a register comment, same `example-log:` prefix, with an override line
  per blocked path — e.g. `override: <path> -> <reason>`. The reason is a written
  statement the reviewer stands behind, recorded verbatim in the register; a blank, a
  token, or the template echoed back is not a decision. Overriding should read as
  signing something, not firing a flag — that weight is the point, and it is the one
  place deliberate friction is correct.
- **Eligibility:** reviewer ≠ author, collaborator permission `write`/`maintain`/
  `admin`, checked *before* selection — the exact rules the acknowledgement gate
  already applies.
- **Pinned to the head SHA.** An override names the commit it was made against; a new
  push produces a new SHA that no existing override names, so the stop returns. Same
  staleness rule as a disposition.
- **Persisted in the register**, attributed to the overrider. The record is the
  artefact; the green check is a side effect of it.
- **Dual control on B.** An override of B — a reviewer signing off on deleting licence
  text — counts only when **two** distinct eligible reviewers sign the same path
  against the current head, neither the author and not each other. A (missing notice)
  and D (new-file decision) take one. You cannot co-sign your own override, which is
  what makes it a decision rather than a self-cleared flag. (G is not overridable at
  all — see Principle.)
- **Effect:** the named check treats that path as *decided* rather than *blocking*.

## Sharp edges — design against these, do not discover them later

1. **An override on check B is a reviewer signing off on deleting licence text.** That
   is legitimate accountability *or* a laundering path, decided entirely by whether the
   register is read. So an override must be **loud in the record and in the report** —
   the report says "`<path>` — licence line removal overridden by @X against `<sha>`:
   `<reason>`", never a silent flip to green. An override that makes the report quieter
   has rebuilt the false all-clear this whole tool exists to prevent.

   Concretely, guard the `verified` box in `main()`. An overridden check B drops out of
   `b`, which makes `if not b:` true and prints "no licence or copyright line deleted
   or altered" under a heading that tells the reviewer not to look — the exact false
   all-clear that box exists to avoid. The override must add a caveat there, the way
   `binary_skips` and `deleted_headers` already do, so the box states what was
   overridden rather than certifying past it.
2. **Per-path, never blanket.** An override names each path, like `/std-licence` names
   each file. "Override everything" is not a decision anyone checked.
3. **Every overridable stop must stay clearable.** A block that no reachable set of
   reviewers can clear is the get-in-the-way failure this philosophy rejects. Dual
   control on B assumes a team with two eligible reviewers besides the author, which
   holds here. G is the deliberate exception, and it is not a wall either: its remedy
   is to restore the rows and append, always available to the author.
4. **The override is itself attackable.** Everything that guards a disposition guards
   this: a comment body is data not code; eligibility is the collaborator API not
   `author_association`; staleness is SHA-named not timestamped. A stale or
   author-posted override must not count. Under dual control, the two signatures must
   be distinct logins — one person cannot supply both.

5. **Override granularity must match the violation's.** `check_b` records violations as
   `(path, line)`, so one file can carry several unrelated ones. An override keyed on
   the path alone lets two reviewers signing off *one disclosed* licence-line removal in
   `vendor/foo.js` silently clear a *second, undisclosed* removal further down the same
   file — they never saw it, because the override names only the path. Key the override
   on `(path, line)` the way `b` is keyed, or require the reason to enumerate the lines
   and fail closed when that set does not match `b`'s entries for the path. This is the
   same shape as the path-vs-per-end and region-vs-whole-file mistakes already closed
   here twice.

6. **There is no data path today from a comment to the gate's report.**
   `licence-gate.py` reads git and nothing else — no token, no comments. Only
   `actions/acknowledgement` reads comments, and it writes a 138-character commit status,
   not the markdown report. So "loud in the report" (edge 1) needs either the
   acknowledgement action editing the gate's comment, or the gate script learning to read
   comments — and the second would duplicate the eligibility and staleness logic, which
   "one definition per concept" exists to prevent. Decide which before building, not
   mid-build.

## Testing

Bypass attempts are expected to be found by red-teaming (see the target repo's
challenge), and the override widens that surface deliberately. The tests that matter:

- an override always appears in the register and the report, attributed and with its
  reason — never a silent pass;
- an override from the author, or naming a stale SHA, does not count;
- a blanket or reason-less override does not count;
- an override clears only the paths it names, and only the checks policy allows.

## Open questions

- One command for candidate dispositions and overrides, or two? (One prefix, two verbs
  is likely cleanest — the reviewer is doing one thing: recording decisions.)
- What is the written reason's bar? It must not be blank or the template echoed back
  (the acknowledgement gate already learned that); whether to require more than one
  substantive sentence, and how to test "substantive" without a gameable length rule,
  is open.
