"""Deterministic context compression: send snippets, not whole files.

Step 7 fixed *retrieval* - fifteen relevant files now reach the backend. It did
not fix the *prompt*: with an 8,000-character budget and 2,500-character
whole-file blocks, only about two files ever reached the model. Raising the
limit would slow every analysis; the better answer is to send the parts of each
file that carry meaning.

The unit of compression is a **snippet**: a contiguous run of real lines around
a declaration that Step 4 already extracted, labelled with its true line range.

Two properties are non-negotiable, because the whole evidence system rests on
them:

* **Line numbers are original.** A snippet spanning lines 412-448 is rendered
  with those numbers, so a citation the model makes points at the real file.
  Nothing is renumbered.
* **A snippet is never cut in half.** Blocks are whole or absent. A half-block
  would invite a citation to a line the model only partly saw.

No model is involved. The same file and the same budget always produce the same
snippets.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.analysis.code_structure import CodeSymbol, FileStructure

# --- shape of a snippet -------------------------------------------------------

#: Lines of lead-in kept above a declaration, so decorators and the docstring
#: line above a function are not severed from it.
LEAD_IN_LINES = 2

#: Hard cap on one snippet, whatever the block's real length.
MAX_SNIPPET_LINES = 45

#: A file at or below this many characters is sent whole - compressing it would
#: cost more in markers than it saves.
WHOLE_FILE_THRESHOLD = 1_200

#: Rough characters per line of source, used only to bound how many candidate
#: ranges are collected before the fitting loop runs.
AVERAGE_LINE_CHARS = 40

#: A file allowance below this is not worth spending; the file is omitted
#: instead, so the budget goes to a file that can actually say something.
#:
#: This is the breadth-versus-depth dial. At 220 an 8,000-character budget
#: reaches sixteen files at six lines each - enough to name a class, not enough
#: to reason about it. At 400 it reaches nine files at a dozen lines, which is
#: a real declaration with its body. Nine files of substance beat sixteen
#: glimpses, and both beat the two whole files that fitted before Step 8.
MIN_USEFUL_ALLOWANCE = 400

#: Rendered above every snippet run so the model knows it is seeing extracts.
ELISION_MARKER = "        ...   (lines omitted)"

#: Closes an extract that had to be cut for budget. Stated plainly so the model
#: does not read the last line shown as the end of the block.
TRUNCATED_EXTRACT_NOTE = "        ...   (extract truncated for length)"


@dataclass(frozen=True)
class Snippet:
    """A contiguous run of original lines, with its true position."""

    path: str
    start_line: int
    end_line: int
    text: str
    #: What made this range worth showing: "class Session", "route GET /x".
    reason: str

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class CompressedFile:
    """One file, reduced to what fits."""

    path: str
    snippets: list[Snippet] = field(default_factory=list)
    #: True when the file was small enough to send in full.
    whole: bool = False
    #: Characters the rendered block occupies.
    chars: int = 0
    #: Lines shown out of the file's total.
    lines_shown: int = 0
    total_lines: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.snippets


# --- symbol prioritisation ----------------------------------------------------

#: What to show first when a file has more declarations than budget. Routes and
#: classes describe a system's shape; a helper function rarely does.
_SYMBOL_PRIORITY: dict[str, int] = {
    "route": 0,
    "class": 1,
    "function": 2,
    "method": 3,
    "import": 9,
}


def _symbol_rank(symbol: CodeSymbol, terms: tuple[str, ...]) -> tuple:
    """Order symbols within a file. Query matches come first."""
    matches_query = any(term in symbol.name.lower() for term in terms)
    return (
        0 if matches_query else 1,
        _SYMBOL_PRIORITY.get(symbol.kind, 5),
        symbol.line,
    )


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _block_end(lines: list[str], start_index: int) -> int:
    """Find where a declaration's body ends, as a 0-based inclusive index.

    Deterministic and language-agnostic: the block runs until a non-blank line
    that is indented no further than the declaration itself. That is exact for
    Python and a good approximation for brace languages, where the closing brace
    sits at the declaration's own indentation.
    """
    if start_index >= len(lines):
        return start_index

    base_indent = _indent_of(lines[start_index])
    limit = min(len(lines), start_index + MAX_SNIPPET_LINES)

    end = start_index
    for index in range(start_index + 1, limit):
        line = lines[index]
        if not line.strip():
            end = index
            continue
        if _indent_of(line) <= base_indent:
            break
        end = index

    # Trim trailing blank lines - they cost budget and carry nothing.
    while end > start_index and not lines[end].strip():
        end -= 1

    return end


#: A range is `(start_line, end_line, reason, priority)`. Priority is the
#: selection order of the declaration that pulled the range in - 0 is the most
#: wanted - and it survives merging, so the budget is spent on what matters
#: rather than on whatever happens to sit nearest the top of the file.
Range = tuple[int, int, str, int]


def _merge(ranges: list[Range]) -> list[Range]:
    """Merge overlapping or adjacent line ranges.

    Prevents the same lines being sent twice when a class and its first method
    were both selected. A merged range keeps the earlier reason and the stronger
    (lower) priority of the two.
    """
    if not ranges:
        return []

    ordered = sorted(ranges, key=lambda item: (item[0], item[1]))
    merged: list[Range] = [ordered[0]]

    for start, end, reason, priority in ordered[1:]:
        last_start, last_end, last_reason, last_priority = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (
                last_start,
                max(last_end, end),
                last_reason,
                min(last_priority, priority),
            )
        else:
            merged.append((start, end, reason, priority))

    return merged


def _range_cost(lines: list[str], item: Range) -> int:
    """Characters a range occupies once rendered, header included."""
    start, end, reason, _priority = item
    return sum(len(line) + 1 for line in lines[start - 1 : end]) + len(reason) + 24


def _describe(symbol: CodeSymbol) -> str:
    if symbol.kind == "route":
        return f"route {symbol.detail or symbol.name}"
    return f"{symbol.kind} {symbol.name}"


def extract_snippets(
    path: str,
    content: str,
    structure: FileStructure | None,
    *,
    allowance: int,
    terms: tuple[str, ...] = (),
) -> CompressedFile:
    """Reduce one file to the snippets that fit inside `allowance` characters.

    Args:
        path: Repository-relative path, used for labelling only.
        content: The file's full text, exactly as retrieved.
        structure: Step 4's extraction for this file, if it has one. Without it
            the file's opening lines are used, which is the best that can be
            said about a file nothing could be parsed from.
        allowance: Character budget for this file's rendered block.
        terms: Query terms; matching declarations are shown first.

    Returns:
        A `CompressedFile`. Line numbers on every snippet are the file's own.
    """
    result = CompressedFile(path=path)
    if not content:
        return result

    lines = content.splitlines()
    result.total_lines = len(lines)

    # Small files are sent whole: extracting from them costs more in markers
    # than it saves, and a whole small file is strictly more informative.
    if len(content) <= min(WHOLE_FILE_THRESHOLD, allowance):
        result.snippets = [
            Snippet(
                path=path,
                start_line=1,
                end_line=len(lines),
                text=content.rstrip("\n"),
                reason="whole file",
            )
        ]
        result.whole = True
        result.lines_shown = len(lines)
        result.chars = len(content)
        return result

    symbols: list[CodeSymbol] = []
    if structure is not None:
        symbols = [
            *structure.routes,
            *structure.classes,
            *structure.functions,
            *structure.methods,
        ]

    ranges: list[Range] = []

    if symbols:
        ranked = sorted(symbols, key=lambda item: _symbol_rank(item, terms))
        for priority, symbol in enumerate(ranked):
            index = symbol.line - 1
            if not (0 <= index < len(lines)):
                continue

            start = max(0, index - LEAD_IN_LINES)
            end = _block_end(lines, index)
            ranges.append((start + 1, end + 1, _describe(symbol), priority))

            # Bound the candidate list: once the merged ranges hold roughly
            # twice as many characters as the allowance, nothing further down
            # the priority order can win a place. The fitting loop below is
            # quadratic in this list, so it is worth keeping short.
            shown = sum(item[1] - item[0] + 1 for item in _merge(ranges))
            if shown * AVERAGE_LINE_CHARS > allowance * 2:
                break
    else:
        # Nothing parseable: the head of a file is where imports, module
        # docstrings and configuration live, so it is the honest default.
        head = min(len(lines), MAX_SNIPPET_LINES)
        ranges.append((1, head, "opening lines", 0))

    # --- fit inside the allowance, never cutting a snippet in half ----------
    # Candidates are admitted in *selection* order, not file order: when a query
    # names authentication, the authentication function must win the budget even
    # though it sits at the bottom of the file. Merging happens over the set
    # admitted so far, so neighbouring blocks still coalesce into one run - but
    # a run is never allowed to swallow the budget before a higher-priority
    # declaration has had its chance.
    selected: list[Range] = []
    used = 0
    for candidate in sorted(ranges, key=lambda item: (item[3], item[0])):
        trial = _merge([*selected, candidate])
        cost = sum(_range_cost(lines, item) for item in trial)
        if cost > allowance:
            continue
        selected.append(candidate)
        used = cost

    snippets = [
        Snippet(
            path=path,
            start_line=start,
            end_line=end,
            text="\n".join(lines[start - 1 : end]),
            reason=reason,
        )
        for start, end, reason, _priority in _merge(selected)
    ]

    # Last resort: nothing fitted whole, usually because the file is one
    # enormous line (minified output, a single-line JSON blob). Omitting a
    # high-priority file over that would be worse than showing its opening.
    # The cut is made at a line boundary wherever possible, so every line shown
    # is complete and its number is still exact.
    if not snippets and ranges:
        start, end, reason, _priority = min(ranges, key=lambda item: (item[3], item[0]))
        snippets = [_truncated_snippet(path, lines, start, end, reason, allowance)]
        used = len(snippets[0].text)

    result.snippets = snippets
    result.chars = used
    result.lines_shown = sum(item.line_count for item in snippets)
    return result


def _truncated_snippet(
    path: str,
    lines: list[str],
    start: int,
    end: int,
    reason: str,
    allowance: int,
) -> Snippet:
    """Fit one oversized range into `allowance`, cutting at a line boundary.

    Whole lines are kept for as long as they fit, so `end_line` still names a
    line that was shown in full. Only when the very first line is itself too
    long is a character-level cut made - and then the range is a single line, so
    its number remains exact either way.
    """
    budget = max(0, allowance - len(reason) - 40)
    kept: list[str] = []
    used = 0

    for line in lines[start - 1 : end]:
        cost = len(line) + 1
        if used + cost > budget:
            break
        kept.append(line)
        used += cost

    if kept:
        return Snippet(
            path=path,
            start_line=start,
            end_line=start + len(kept) - 1,
            text="\n".join(kept) + f"\n{TRUNCATED_EXTRACT_NOTE}",
            reason=reason,
        )

    # Not even one whole line fits: show the head of that single line.
    head = lines[start - 1][:budget] if budget > 0 else ""
    return Snippet(
        path=path,
        start_line=start,
        end_line=start,
        text=f"{head}\n{TRUNCATED_EXTRACT_NOTE}",
        reason=reason,
    )


def render(compressed: CompressedFile, domain: str) -> str:
    """Render a compressed file as a labelled block for the prompt.

    Every snippet states the exact original line range it came from, so a model
    citing a line is citing the real file.
    """
    header = f"--- FILE: {compressed.path} [{domain}] ---"

    if compressed.whole and compressed.snippets:
        body = compressed.snippets[0].text
        return f"{header}\n{body}\n--- END FILE: {compressed.path} ---\n"

    parts: list[str] = [
        header,
        f"(showing {compressed.lines_shown} of {compressed.total_lines} lines "
        "as extracts; line numbers are the file's own)",
    ]

    for index, snippet in enumerate(compressed.snippets):
        if index > 0:
            parts.append(ELISION_MARKER)
        parts.append(f"[lines {snippet.start_line}-{snippet.end_line}] {snippet.reason}")
        parts.append(snippet.text)

    parts.append(f"--- END FILE: {compressed.path} ---")
    return "\n".join(parts) + "\n"


# --- budget allocation --------------------------------------------------------

#: Share weights per priority band. A high-priority file earns three times the
#: room of a low-priority one, rather than an equal slice.
BAND_WEIGHTS: dict[str, int] = {"high": 3, "medium": 2, "low": 1}


def allocate(
    weights: list[tuple[str, str]], budget: int
) -> dict[str, int]:
    """Split a character budget between files by priority band.

    Args:
        weights: `(path, band)` in priority order.
        budget: Characters available for all file blocks together.

    Returns:
        path -> allowance. Deterministic: the same input always splits the same
        way, and every allowance is at least `MIN_USEFUL_ALLOWANCE` or zero.
    """
    allowances: dict[str, int] = {path: 0 for path, _ in weights}
    if not weights or budget <= 0:
        return allowances

    # Dividing the budget across every retrieved file would hand each one a
    # sliver too small to be worth spending - and a manifest competing with
    # twenty source files would lose its place altogether. So the window of
    # funded files is shrunk from the bottom of the priority order until every
    # file still inside it earns a share worth printing.
    window = list(weights)
    while window:
        total_weight = sum(BAND_WEIGHTS.get(band, 1) for _, band in window)
        smallest = min(
            int(budget * BAND_WEIGHTS.get(band, 1) / total_weight) for _, band in window
        )
        if smallest >= MIN_USEFUL_ALLOWANCE:
            break
        window.pop()

    if not window:
        return allowances

    total_weight = sum(BAND_WEIGHTS.get(band, 1) for _, band in window)
    remaining = budget

    for path, band in window:
        share = min(int(budget * BAND_WEIGHTS.get(band, 1) / total_weight), remaining)
        allowances[path] = share
        remaining -= share

    return allowances
