# Agent instructions

For AI coding agents working on this repository. Human contributors: see `README.md`.

This repo is licence-compliance **tooling**, consumed by other repositories as
SHA-pinned GitHub Actions. It works *with* people, not against them: if it gets in the
way, it has failed. Its job is to **highlight licence issues to the reviewer, help
prevent mistakes before they land, and track the decisions into the register** — not
to police contributors or wall them off.

The register is only worth trusting if it cannot be quietly fooled, so the tooling
also has a threat model: a pull request making it pass something it should have
flagged, or record a decision its author was not entitled to make. Hold both at once —
help the reviewer, and do not be fooled. When they seem to conflict, the friction is
usually the bug: find the way that informs without obstructing.

## Branch from fresh `main`

```bash
git fetch origin && git checkout -b <branch> origin/main
```

## The gate must never trust what it is judging

The checks run as an action pinned to a commit SHA so a pull request cannot edit the
code that judges it. Keep it that way:

- **Take the commits to compare from the event payload, not from action inputs.** The
  workflow file on a `pull_request` event is the PR's own copy; inputs it supplies are
  attacker-controlled. `actions/gate` verifies `base-sha`/`head-sha` against
  `github.event.pull_request.*` and refuses a mismatch. Do not weaken that.
- **Authorise against the collaborator permission API, never `author_association`.**
  The latter reports org membership, not access to this repository.
- **A comment body is data, never code.** Read it; never interpolate it into a shell.
- **Pins are SHAs, not tags or branches.** A tag lets one push here change every
  consumer at once. A SHA reaches a consumer only through a reviewed pin bump.

## Empty is never "nothing to report"

The most dangerous thing this tool can emit is a clean result it did not establish —
a green check on a tree nothing examined. The rule is honesty, not blocking: never
report a clean result you did not reach. So:

- A git command whose failure means the gate did not run **says so and stops**, rather
  than returning empty output that reads as "found nothing". All git goes through
  `git_io.py`; use `sh_strict`/`git_show` and honour the `None` vs `""` distinction.
- A check that cannot run reports that it could not, and does not certify past it. Like
  any other stop a reviewer can clear it — what it must never do is stay silent.
- The report and the candidate list come from the same enumeration; if you add a
  candidate source, add it to both call sites, not one.

## One definition per concept

"Has a licence header", "is a source file", "the leading comment region", and the
diff parser each have exactly one implementation. Import it; do not re-derive a second
copy that will drift. Two of the fixed bypasses were two definitions disagreeing.

## Decide the licence, never the author

The tooling decides which licence a path falls under — mechanical, from the path, in
`licence_map.py`. It never decides or invents *who wrote* something: that is asserted
by a person and only recorded. This split is why a machine does not stamp a
first-party header onto a vendored third-party file. Any rule that would have the
tooling name a copyright holder is the rule to stop and question.

## The register is the product; the tooling serves the reviewer

Highlight the issue, help avoid the mistake, record the decision. That is the job.
Every stop means "a decision is required here", and a person makes it — prefer
surfacing a case to a reviewer over blocking it, and blocking over failing silently,
but a block is never a dead end. A check that fires on ordinary work has got in the
way, and a tool that gets in the way gets switched off, which is the same as not
existing. When unsure whether a case blocks or prompts, prompt.

**An attributed override is a first-class decision, not a bypass.** A reviewer (never
the author) can clear a stop, and the clearance is recorded in the register and
attributed to them — the opposite of an *unattributed* admin force-push that records
nothing. An override of check B (deleting licence text) takes two distinct reviewers,
because signing off on that should not be a solo act.

**The register is the one thing no override touches: it can never be rewritten, only
appended.** Check G is not overridable at any signature count — a rewrite is undone by
restoring the rows and appending, a mechanical fix always open to the author, not a
decision to ratify.

Today checks A, B, D(source) and G hard-block and **no override command exists yet**;
clearing one needs a fix or an admin. Its intended shape and sharp edges are in
`docs/override-design.md`. Until it exists, do not harden A/B/D in ways that assume a
stop can never legitimately be cleared, and do not read the older comments' warnings
about "bypassing" as applying to a recorded, attributed override — they mean
unattributed clearances, not this.

## Every check ships with its corpus entry

The test corpora are the specification, not an afterthought — `TestHeaderCorpus` for
header shapes, the integration tests for end-to-end behaviour. A change to a pattern
or a check lands with the cases that pin it, in the same commit. A new bypass you
close lands with the test that reproduces it. Run both language ends:

```bash
cd scripts && python3 -m unittest discover -p 'test_*.py'
```

The JS actions are exercised by that suite too; Node 20 and Python 3.9–3.13 are the
supported range.

## Comments and docstrings: the fact, not the story

A docstring says what the code guarantees and which failure it prevents, in as few
lines as that takes. It is read every time someone works here, so it must not cost
them a narrative to get through.

- State the invariant and the concrete failure it stops. Keep those.
- Cut the closing maxim, the caps-lock emphasis, and the retelling of how the bug was
  found. "Restore the licence line; this cannot be automated" is guidance. "A gate
  people learn to click past" is a sermon — drop it.
- Write for the next maintainer, not as a diary of the change. If a sentence only
  makes sense as the author's reflection, it does not belong in the code.

The user-facing report text follows the same rule: tell the reviewer what to do, not
why the process is virtuous.

## Commit discipline

One concern per commit — a check change, a cleanup, and a docs pass are three commits.
Conventional headline (`fix:`, `feat:`, `docs:`, `refactor:`, `test:`) and DCO
sign-off (`git commit -s`). A commit substantially written by an AI agent carries a
`Co-Authored-By:` trailer naming the tool alongside the sign-off.

Commit messages are written for a reviewer who has only the diff. Confident AI output
still needs splitting: a correct refactor and a real regression can arrive in one
plausible-looking commit.

## Shipping a change to consumers

A change here reaches a consuming repository only when its pinned SHA is bumped, in a
separate reviewed pull request in that repo. Say so in the PR description when a bump
is needed; never assume merging here is enough.

## When the call is contestable, ask

Whether a check should block or prompt, whether a pattern broadening is safe against
the corpus, whether a new event trigger is verifiable — anywhere a reviewer could
reasonably want the other option, put the choice to the human driving the session
before committing to it. Unattended, take the safer option: surface to a reviewer over
blocking, refuse over guessing, and never record a decision nobody made. Flag the call
in the PR description.
