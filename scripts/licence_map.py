#!/usr/bin/env python3
"""Decide what header a new file should carry.

The split this module exists to enforce:

  * WHICH LICENCE is a function of the path. Mechanical, decidable, testable here.
  * WHO HOLDS COPYRIGHT is a function of who wrote it. Not decidable by any machine,
    so it is asserted by a human and only recorded here.

Conflating those two is how both of the real failures happened: a rule reading
"new files get our header" would stamp a vendored third-party asset with our
copyright, and a rule reading "our files get our licence" would drop a copyleft
file into a permissively-licensed example tree that exists to be copied.

Refusal is a first-class outcome. When the licence cannot be determined the answer
is "a human decides", never a guess - a wrong header looks settled and nobody
re-examines it.
"""

import collections
import re

DEFAULT_LICENCE = "Example-1.0"
COPYRIGHT = "Example project contributors"
YEAR = "2026"

# Longest prefix wins. In the real repositories this is the exception list: most of
# the tree is the repo's own licence, with a small number of subtrees licensed
# differently by upstream.
LICENCE_MAP = [
    ("src/permissive/", "Permissive-2.0"),
]

# Paths whose licence is genuinely undetermined - vendored code, third-party trees.
# Never guessed at.
UNMAPPED = [
    re.compile(r"^vendor/"),
    re.compile(r"^third[_-]party/"),
]

ASSET_RE = re.compile(r"\.(svg|png|jpg|jpeg|gif|ico|woff2?|ttf|dat)$", re.I)

# Every extension treated as source, and therefore every extension a licence header
# may have to be written into. A TUPLE rather than a regex literal, because the
# invariant below - every one of these has a known comment syntax - is only testable
# if the list can be enumerated. The regex is derived from it; there is nothing to
# keep in step by hand.
#
# The list this replaces was the example project's, not a real codebase's: eleven
# extensions, no `.php`, no `.vue`, no `.tsx`. The consuming repository is a PHP
# application with JS/TS front-ends, so the gap was not hypothetical. check_a and
# check_b never filtered by extension and were never affected; the hole was check_d,
# where a NEW file with an unlisted extension took the asset branch and so never
# reached the test that blocks a headerless new source file. A new `.php` file with no
# licence header at all merged with an advisory candidate and exit 0.
#
# THIS IS ALSO THE ONE DEFINITION. There were two, in this module and in the gate, and
# they disagreed about `.html`: stampable here, an asset there. One definition of each
# term, imported everywhere - three disagreeing definitions of "has a header" made a
# file invisible to every check once already.
SOURCE_EXTENSIONS = (
    "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts",
    "php", "py", "c", "h", "cpp", "java", "go", "rb", "pl", "lua", "sql",
    "kt", "swift", "m", "mm", "rs", "scala", "groovy", "gradle", "proto",
    "sh", "bash", "zsh", "ps1", "r", "tf",
    "css", "scss", "sass", "less", "styl",
    "html", "htm", "vue", "xml", "xsl",
)
SOURCE_RE = re.compile(r"\.(" + "|".join(SOURCE_EXTENSIONS) + r")$", re.I)

APPLY = "apply"
REFUSE = "refuse"


def licence_for(path):
    """The licence a file at this path falls under, or None if undetermined."""
    for pattern in UNMAPPED:
        if pattern.match(path):
            return None
    best = None
    for prefix, lic in LICENCE_MAP:
        if path.startswith(prefix):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, lic)
    return best[1] if best else DEFAULT_LICENCE


def classify(path, explicitly_named, has_header):
    """Decide what to do with one new file.

    `explicitly_named` is True when the human named this exact path in the command.
    EVERY file requires that. An earlier version required it only for assets, on the
    reasoning that contributors usually write their own source while icons and fonts
    are the things people copy. That reasoning is sound on average and worthless as a
    safeguard: it was defeated by putting third-party code in a `.js` file. The file
    said in its own comment that it was copied from elsewhere, and a blanket command
    stamped our copyright directly above that sentence.

    An assertion of authorship has to name what it is asserting. "Everything in this
    pull request is ours" is not a claim anyone has actually checked, and the whole
    point of this command is to record a claim someone is willing to stand behind.

    Returns (action, licence_or_None, reason).
    """
    if has_header:
        return (REFUSE, None, "already carries a header; not overwriting an existing claim")

    lic = licence_for(path)
    if lic is None:
        return (REFUSE, None, "outside the licence map; determine this by hand")

    if not explicitly_named:
        return (REFUSE, None,
                "not named in the command - an assertion of authorship must name the "
                "file it is asserting, whatever type it is")

    if not (SOURCE_RE.search(path) or ASSET_RE.search(path)):
        return (REFUSE, None, "not a source or asset file; decide by hand whether it needs a header")

    # Nowhere to put a header is a refusal, not a fallback. This used to be
    # `COMMENT_STYLE.get(ext, "#")` one layer down, which stamped `# SPDX-...` into
    # anything unrecognised - a .png included, since ASSET_RE admits binaries that have
    # no comment syntax at all and never will. Writing text into a binary is a
    # corruption the diff shows as an ordinary added line.
    if comment_style_for(path) is None:
        return (REFUSE, None,
                "no comment syntax is known for this file type, so there is nowhere to "
                "put a header; add the header by hand, or add the type to COMMENT_STYLE "
                "if it genuinely has one")

    return (APPLY, lic, "")


# THE one definition of "this line asserts ownership". Everything that needs to know
# whether a file carries a header asks this, and nothing re-implements it.
#
# There were three implementations before, and they disagreed. The gate looked for
# `Copyright (c)` or `licensed under the`; the stamping tool wrote SPDX tags; a third
# check tested for the literal string `SPDX-License-Identifier` in the first 15 lines.
# The consequence was not cosmetic: a file stamped by our own tool matched none of the
# gate's patterns, so it became permanently invisible to both blocking checks. Its
# licence could then be deleted outright and the report would certify that nothing had
# been altered. The tool that writes headers and the tool that enforces them have to
# agree on what one looks like.
#
# The pattern required the literal word "copyright" before any (c)/©/year, and that
# was a live false all-clear rather than a theoretical gap. `© 2020 Example Corp`,
# `(c) 2020 Example Corp` and `(C) 2020 Example Corp, all rights reserved` are all
# ordinary header forms and all three matched nothing - so deleting such a line
# passed the gate, and the report said, under "you need not check these", that no
# copyright line had been altered. That is the exact failure this tool exists to
# prevent, and it needed no attacker: the © form is what most European vendors ship.
#
# Broadening it has a boundary that must hold. A looser pattern - anything containing
# "copyright" - made the gate flag its own prose, and a compliance gate that fires on
# documentation is a gate people learn to click past. TestHeaderCorpus below is the
# statement of scope: a list of real header styles that must match and a list of
# prose lines that must not. The regex is whatever satisfies that list; the list is
# the specification. Add to it before you touch this.
#
# Two deliberate narrowings inside the broadening, both from running the pattern over
# ordinary text:
#   * a bare `(c)` needs a following year. `(c)` on its own is a list marker - "(a)
#     keep, (b) drop, (c) defer" - and matching it would fire on every enumeration.
#   * `©` takes a year or a capitalised word after it, so "the © symbol" is prose
#     while "© Example Corp" is a claim.
# Multi-line licence bodies are matched on a SHORT leading fragment only, because this
# predicate is handed single lines by check_b and by apply_fix's anchor scan as well
# as a joined region: a phrase long enough to wrap in a real header matches nothing.
HEADER_RE = re.compile(
    r"("
    r"copyright\s*(?:\(c\)|\(\d|©|\d{4}|by\b)"
    r"|(?:\(c\)|©|Ⓒ)\s*\d{4}"
    r"|[©Ⓒ]\s*[A-Z]"
    r"|all rights reserved"
    r"|licen[sc]ed under"
    r"|SPDX-FileCopyrightText:"
    r"|SPDX-License-Identifier:"
    r"|GNU (?:Lesser |Affero |)General Public License"
    r"|Permission is hereby granted"
    r"|Redistribution and use in source"
    r"|under the terms of the GNU"
    r"|is free software[:;]"
    # MPL 2.0's standard header names no copyright, no year, no (c) and no SPDX tag,
    # so every line of it was invisible and the whole block could be deleted with the
    # report certifying nothing had been altered. Matched on its three distinctive
    # phrases rather than on any ownership word, because it contains none.
    r"|Source Code Form is subject to"
    r"|subject to the terms of the Mozilla"
    r"|You can obtain one at"
    r"|If a copy of the"
    r"|proprietary and confidential"
    r")",
    re.I,
)


# Case-SENSITIVE, and separate for that reason alone. A copyright with no year -
# "Copyright Example Corp" - can only be told from prose by what follows the word: a
# capitalised name rather than "and licensing", "questions", "holder", "of each".
# Putting that alternative in HEADER_RE does not work, because HEADER_RE carries re.I
# and `[A-Z]` under re.I matches lowercase too - so it silently matched every prose
# line in the corpus. A rule about capitalisation cannot live in a case-insensitive
# pattern, however tidy it would be to keep them together.
NAMED_COPYRIGHT_RE = re.compile(r"[Cc]opyright\s+(?:\(c\)\s*)?[A-Z]")


def has_licence_header(text):
    """Whether this text asserts ownership. The single predicate; do not re-derive it."""
    text = text or ""
    return bool(HEADER_RE.search(text) or NAMED_COPYRIGHT_RE.search(text))


# One record per line of the leading comment region, in file order.
#
#   index      the line's position in content.split("\n"), so a caller can edit it
#   kind       "blank", "line" (a // or # comment) or "block" (inside /* */, <!-- -->)
#   token      the marker a "line" uses, or the closing token a "block" needs
#   closes_at  for a "block" line that contains its closing token, the column that
#              token starts at; None otherwise
CommentLine = collections.namedtuple("CommentLine", "index kind token closes_at")

_BLOCK_STYLES = (("/*", "*/"), ("<!--", "-->"))

# Line-comment markers this scan recognises. `--` is here because SQL and Lua are now
# classified as source, and a source file whose comment marker this scan does not know
# has an EMPTY leading comment region - so check_a reads it as carrying no header and
# demands no modification notice, in silence. Widening what counts as source without
# widening this is how a file gets moved out of the asset path, where it at least
# raised a candidate, into the source path, where nothing looks at it.
_LINE_MARKERS = ("//", "#", "--")


def leading_comment_lines(content):
    """Classify every line of the file's leading comment region.

    The scan already had to track block-open/close state to know where the region
    ended; it simply threw that away and returned text. Anything that then wanted to
    know whether a given line was inside a block comment had to re-derive it, and
    re-deriving it per line is guessing: apply_fix matched the leading `*` of a
    block-comment body with a LINE-comment marker pattern and appended the
    modification notice after the `*/`, i.e. outside the comment, as a bare
    statement. `node --check` rejected the result and /auto-fix committed and pushed
    it. The state is knowable, so hand it out rather than making callers guess.
    """
    lines = content.split("\n")
    i = 0
    if lines and any(p.match(lines[0]) for p in _MUST_STAY_FIRST):
        i = 1
    out, closing = [], None
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if closing:
            at = line.find(closing)
            out.append(CommentLine(i, "block", closing, at if at >= 0 else None))
            if at >= 0:
                closing = None
            i += 1
            continue
        if not stripped:
            out.append(CommentLine(i, "blank", None, None))
            i += 1
            continue
        opener = next((o for o, _ in _BLOCK_STYLES if stripped.startswith(o)), None)
        if opener:
            closer = dict(_BLOCK_STYLES)[opener]
            at = line.find(closer, line.find(opener) + len(opener))
            out.append(CommentLine(i, "block", closer, at if at >= 0 else None))
            if at < 0:
                closing = closer
            i += 1
            continue
        if stripped.startswith("*"):
            # A block-comment body or closer whose opener is not in view - a file
            # that begins mid-comment. Kept in the region (dropping it would shrink
            # what counts as a header, and under-detection is the failure nobody
            # notices), but recorded as block rather than as a line comment: its `*`
            # is not a comment marker that can be repeated on a new line.
            at = line.find("*/")
            out.append(CommentLine(i, "block", "*/", at if at >= 0 else None))
            i += 1
            continue
        marker = next((m for m in _LINE_MARKERS if stripped.startswith(m)), None)
        if marker:
            out.append(CommentLine(i, "line", marker, None))
            i += 1
            continue
        break
    return out


def leading_comment_region(content):
    """The file's leading comment block: where a file's own licence header lives.

    This replaces a fixed 40-line window. The window was not arbitrary - it encoded
    the real rule that a file's header sits at the top - but the number was, and 45
    lines of filler comment pushed a header past it, so the gate concluded the file
    had no header and demanded no modification notice.

    Scanning the whole file instead would be worse, not better. Any file quoting a
    licence in prose, or a vendored bundle carrying per-section headers far down,
    would start registering as "has a header" and demanding notices it does not need.
    False positives are how people learn to click past a compliance gate.

    So keep the rule and drop the number: read from the top until the first line that
    is neither blank nor part of a comment.
    """
    lines = content.split("\n")
    return "\n".join(lines[r.index] for r in leading_comment_lines(content))


def header_texts(licence):
    """The header's content, with no comment syntax. One place; everything else formats."""
    return [
        f"SPDX-FileCopyrightText: {YEAR} {COPYRIGHT}",
        f"SPDX-License-Identifier: {licence}",
    ]


def header_lines(licence, comment="//"):
    return [f"{comment} {t}" for t in header_texts(licence)]


# How each extension spells a comment. The value is the OPENING token; block styles
# are recognised by it and closed accordingly.
#
# EVERY EXTENSION IN SOURCE_EXTENSIONS MUST APPEAR HERE, and a test enforces it. A
# file classified as source is a file the gate will demand a header in and this tool
# may be asked to write one into; if its comment syntax is unknown, both of those are
# guesses. The old fallback made the guess silently - `COMMENT_STYLE.get(ext, "#")` -
# so an unknown type got a `#` header whether or not `#` starts a comment in it.
COMMENT_STYLE = {
    ".js": "//", ".ts": "//", ".c": "//", ".h": "//", ".cpp": "//",
    ".java": "//", ".go": "//", ".css": "/*", ".less": "/*",
    ".py": "#", ".sh": "#", ".rb": "#",
    ".html": "<!--", ".htm": "<!--", ".svg": "<!--",
    # Added with SOURCE_EXTENSIONS above.
    ".jsx": "//", ".mjs": "//", ".cjs": "//", ".tsx": "//",
    ".mts": "//", ".cts": "//",
    # PHP's header goes after the `<?php` line - see _MUST_STAY_FIRST - because a
    # comment above it is not a comment, it is page output.
    ".php": "//",
    ".kt": "//", ".swift": "//", ".rs": "//", ".scala": "//",
    ".groovy": "//", ".gradle": "//", ".proto": "//", ".styl": "//",
    # `.m` and `.mm` are taken as Objective-C. `.m` is also MATLAB, whose comment
    # marker is `%` - see the module note; this picks the likelier of the two rather
    # than pretending the ambiguity is resolved.
    ".m": "//", ".mm": "//",
    ".scss": "/*",
    # Indented Sass has no `/* */`; `//` is its only comment form.
    ".sass": "//",
    ".bash": "#", ".zsh": "#", ".ps1": "#", ".r": "#", ".tf": "#", ".pl": "#",
    # SQL and Lua both comment with `--`, which leading_comment_lines now recognises.
    ".sql": "--", ".lua": "--",
    ".xml": "<!--", ".xsl": "<!--", ".vue": "<!--",
}


def comment_style_for(path):
    """The comment syntax this file type uses, or None when we do not know.

    None is an ANSWER, and callers must treat it as one. The lookup it replaces ended
    `.get(ext, "#")`, which turned "we have never heard of this type" into "use a hash"
    - writing `# SPDX-License-Identifier: ...` into a .png as readily as into a .py.
    """
    return COMMENT_STYLE.get("." + path.rsplit(".", 1)[-1].lower())


def header_block(path, licence):
    """The header for this file, as lines, in the comment syntax its type uses.

    Raises ValueError rather than guessing a syntax. classify() refuses such a path
    before anything gets here, so this is the second lock on the same door: a remedy
    may refuse, it may not guess.
    """
    style = comment_style_for(path)
    if style is None:
        raise ValueError(
            f"no comment syntax is known for `{path}`, so a header cannot be written "
            f"into it without guessing; add its extension to COMMENT_STYLE or add the "
            f"header by hand")
    texts = header_texts(licence)
    if style == "/*":
        return ["/*"] + [f" * {t}" for t in texts] + [" */"]
    if style == "<!--":
        return ["<!--"] + [f"  {t}" for t in texts] + ["-->"]
    return [f"{style} {t}" for t in texts]


# Lines that MUST stay first in the file. Prepending above a shebang stops a script
# executing; prepending above an XML declaration makes the document invalid; prepending
# above `<?php` puts the text outside PHP mode, where it is not a comment but output
# written straight to the page - and output before a header breaks every redirect and
# cookie the request goes on to set. All three fail quietly, in the sense that the
# header looks perfectly correct in the diff.
_MUST_STAY_FIRST = (
    re.compile(r"^#!"),
    re.compile(r"^<\?xml[\s?]", re.I),
    re.compile(r"^<\?php\b", re.I),
)


def insert_header(content, path, licence):
    """Return `content` with the header inserted at the first position it may occupy."""
    lines = content.split("\n")
    at = 0
    if lines and any(p.match(lines[0]) for p in _MUST_STAY_FIRST):
        at = 1
    block = header_block(path, licence)
    return "\n".join(lines[:at] + block + lines[at:])
