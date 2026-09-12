# v2 — the decision moves into the diff

Working branch. `main` stays as it is and keeps serving the red-team exercise; nothing
here reaches any consumer until a reviewed pin bump, because consumers pin a SHA.

Full reasoning: `~/cm-findings/Euro-Office/internal/licence-gate-PLAN.md`

## The premise

Two sentences explain twenty-six findings.

**The gate infers what it should demand.** Is this file ours, is this header real, is
this a replacement — every guess was eventually wrong, and wrong answers looked exactly
like right ones.

**We reinvented a solved pattern.** Mature compliance systems separate scanner findings
from human decisions and store the decisions as versioned files reviewed like code. We
stored ours as a comment parsed by a bot, which is where `virus`, blank templates,
hand-rolled SHA-binding and hand-rolled eligibility filtering all came from.

## The change

The register row moves into the diff. The **author** writes it; the **reviewer approves
the pull request**; the approval is the signature.

GitHub already binds approvals to a commit, dismisses them on a new push, refuses them
from the last pusher, and logs them. We were rebuilding that, badly.

The gate is then left with one mechanical question: **for every candidate, is there a
register row in this diff?**

## Where the rationale lives

The rationale is the field that carries the judgement, so it needs saying explicitly.

It is a **column in the register row**: written by the author, contested through
ordinary review. The reviewer never edits it themselves — they request a change and the
author revises — which keeps `require_last_push_approval` intact.

This is stronger than the comment it replaces, not weaker:

- a rationale in a comment was free text, unvalidated, and impossible to diff later
- a rationale in the row is versioned, sits where reviewers already look, and stays
  attached to the record
- `dismiss_stale_reviews` means **editing the rationale drops the approval**, so a
  sign-off attaches to specific words. The old design could not do that: a reviewer
  wrote a rationale in a comment and the author could push anything afterwards

What does not change: *whether a stated rationale is the real one* stays in the
**not checked** list, permanently. No machine reaches it. The gain is that it is now in
a diff rather than a comment thread.

## What goes

| Removed | Replaced by |
|---|---|
| `actions/acknowledgement` comment parsing | a row in the diff + branch protection |
| answer vocabulary, commit line, staleness, eligibility filter | GitHub review semantics |
| `check_d`, `licence_map.licence_for` | `reuse lint` |
| `apply-std-licence.py` | `reuse annotate` |

## What stays

- the tests, including the integration harness — twenty-six attacks that worked
- candidate detection: renames, deletions, assets, gitlinks, binaries, vendored trees,
  extensionless files
- `git_io` — strict, mode-aware git access
- the modification notice check (AGPL §5(a), ONLYOFFICE Term 2)
- the report work: what must happen, who can do it

## Order

1. register row in the diff, approval as signature
2. configuration assertion across consuming repos
3. REUSE for declaration
4. supply chain: pin transitive actions, sign bot commits, show the diff on a pin bump
5. ScanCode spike, measured before committing

## Rule for this branch

Every deletion is a commit of its own, explaining what the removed code was for and why
it is not needed. The next person must be able to tell a considered removal from an
oversight.
