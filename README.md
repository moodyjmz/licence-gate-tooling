# licence-gate-tooling

Composite actions enforcing licence-compliance rules on pull requests. Consumed by
other repositories, **pinned to a commit SHA**.

Generic by design: nothing here names a real project, licence, or file.

## Why the code lives here and not in the repository being gated

The gate must not be editable by the thing it is gating.

The first version put the checks in each repository's own `scripts/` directory. The
workflow checked out the pull request and ran the gate from that checkout — so a
pull request could edit the gate to report nothing, exit zero, and pass itself. No
permissions and no cleverness required: change the file you are being judged by.

The write-capable commands had the same shape with worse consequences. They hold
`contents: write`, so a script the pull request supplied ran under the workflow
token — an escalation from "can push a branch" to "can act as the workflow".

Consumed as a pinned action, the code that runs is fixed at the pin. The pull
request's *content* is read freely; that is the input. Its *code* is never executed.

## Pin to a SHA. Not a tag, not a branch.

```yaml
uses: moodyjmz/licence-gate-tooling/actions/gate@<40-character-sha>   # correct
uses: moodyjmz/licence-gate-tooling/actions/gate@main                 # wrong
uses: moodyjmz/licence-gate-tooling/actions/gate@v1                   # wrong
```

This is load-bearing, not style. Pinned to a SHA, a push here reaches nobody until
someone bumps the pin, and that bump is a reviewed pull request in the consuming
repository — which is why consuming repositories need no special protection, and why
this repository does not need to be locked down harder than they are.

Pinned to a branch or a tag, one push here changes the gate everywhere
simultaneously, with no review anywhere, and this repository becomes the most
security-critical thing in the estate. A tag is not immutable; it can be moved.

## The actions

| Action | Trigger | Permissions | What it does |
|---|---|---|---|
| `actions/gate` | `pull_request` | `contents: read`, `pull-requests: write` | Blocks on removed licence lines, missing modification notices, and new files needing a decision. Lists candidate replacement events. |
| `actions/acknowledgement` | `pull_request`, `issue_comment` | `contents: read`, `pull-requests: read`, `statuses: write` | Requires a reviewer — not the author — to disposition every candidate, against the current head. |
| `actions/auto-fix` | `issue_comment` | `contents: write`, `pull-requests: write` | Applies the modification notice to modified files. Mechanical; never touches new files. |
| `actions/std-licence` | `issue_comment` | `contents: write`, `pull-requests: write` | Applies headers to new files on an explicit assertion of authorship. |

### The division that the whole design rests on

**Which licence** is a function of the path. Mechanical, decidable, testable.

**Who holds copyright** is a function of who wrote it. Not decidable by any machine,
so it is asserted by a person and only recorded.

Conflating the two produces both of the real-world failures this exists to prevent:
a rule reading "new files get our header" stamps a vendored third-party asset with
your copyright, and a rule reading "our files get our licence" drops a copyleft file
into a permissively-licensed example tree that exists to be copied.

### Refusal is a first-class outcome

Where the licence cannot be determined, the answer is "a person decides" — never a
guess. A wrong header looks settled, so nobody re-examines it.

Candidate detection runs the other way and over-reports on purpose. It may say "this
might be a replacement"; it may never say "this isn't". A false prompt costs a
reviewer seconds. A false all-clear is a missing record nobody knows is missing.

## Consuming it

See [`examples/`](examples/) for the four caller workflows. They are thin: trigger,
permissions, checkout, and the pinned `uses:`. Everything else is here.

## Tests

```sh
cd scripts && python3 -m unittest test_licence_map -v
```

20 tests. Each names the real failure it guards against — a test whose purpose is not
obvious gets deleted in six months by someone who cannot see why it matters.
