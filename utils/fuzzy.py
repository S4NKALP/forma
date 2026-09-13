"""
Subsequence matching over name/genericName/keywords with a small score
(0 = prefix hit, 1 = substring hit, 2 = subsequence hit), then a tie-break on
prior launch count, then alphabetical. Empty query sorts strictly by usage then
name.
"""

from __future__ import annotations


def subsequence(needle: str, hay: str) -> bool:
    j = 0
    for ch in hay:
        if j < len(needle) and ch == needle[j]:
            j += 1
    return j == len(needle)


def score(entry, q: str) -> int:
    name = (entry.name or "").lower()
    if name.startswith(q):
        return 0
    best = 99
    parts = []
    if entry.name:
        parts.append(str(entry.name))
    if entry.generic_name:
        parts.append(str(entry.generic_name))
    if entry.keywords:
        parts.extend(str(k) for k in entry.keywords)
    for part in parts:
        f = part.lower()
        if q in f:
            best = min(best, 1)
        elif subsequence(q, f):
            best = min(best, 2)
    return best


def uses(usage: dict, entry) -> int:
    if not usage or not getattr(entry, "id", None):
        return 0
    count = usage.get(entry.id)
    return count if isinstance(count, (int, float)) else 0


def _usage_sort_key(usage: dict):
    def key(entry):
        return -uses(usage, entry)

    return key


def rank(entries: list, query: str, usage: dict):
    """Rank visible entries by fuzzy score, then usage, then alpha."""
    usage = usage or {}
    visible = [e for e in entries if not getattr(e, "no_display", False)]

    q = (query or "").strip().lower()
    if not q:
        return sorted(
            visible,
            key=lambda e: (
                _usage_sort_key(usage)(e),
                (e.name or "").lower(),
            ),
        )

    scored = []
    for entry in visible:
        s = score(entry, q)
        if s < 99:
            scored.append((s, entry))
    scored.sort(
        key=lambda pair: (
            pair[0],
            _usage_sort_key(usage)(pair[1]),
            (pair[1].name or "").lower(),
        )
    )
    return [entry for _, entry in scored]
