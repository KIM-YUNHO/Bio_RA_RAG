"""Exact-budget packing with source offsets and hard structural boundaries.

Offsets refer to cleaned source units, never to raw PDF bytes. Page/region and
block identifiers resolve those units back to the parsed source artifacts.
"""
from dataclasses import dataclass, replace
import re


@dataclass(frozen=True)
class Fragment:
    text: str
    uid: int
    start: int = 0
    hard_split: bool = False

    @property
    def end(self):
        return self.start + len(self.text)


@dataclass
class Packed:
    fragments: list
    reason: str
    overlap_tokens: int = 0

    @property
    def text(self):
        return "\n".join(f.text for f in self.fragments)


def split_fragment(fragment, limit, counter):
    """Prefer lines, sentences, whitespace; character windows are the last resort.

    Every fragment is an exact substring of its cleaned source unit. Budget
    checks tokenize the assembled string, including separators.
    """
    if counter.one(fragment.text) <= limit:
        return [fragment]
    pieces = [fragment]
    for pattern in (r"\n+", r"(?<=[.!?])\s+", r"\s+"):
        out = []
        for piece in pieces:
            if counter.one(piece.text) <= limit:
                out.append(piece)
                continue
            start = 0
            for match in re.finditer(pattern, piece.text):
                if match.start() > start:
                    out.append(replace(piece, text=piece.text[start:match.start()], start=piece.start + start))
                start = match.end()
            if start < len(piece.text):
                out.append(replace(piece, text=piece.text[start:], start=piece.start + start))
        pieces = out
    out = []
    for piece in pieces:
        start = 0
        while start < len(piece.text):
            remaining = piece.text[start:]
            if counter.one(remaining) <= limit:
                out.append(replace(piece, text=remaining, start=piece.start + start,
                                   hard_split=piece.hard_split or start > 0))
                break
            # Do not decode token windows: decoding can change the source text.
            lo, hi = 1, len(remaining)
            if counter.one(remaining[:1]) > limit:
                raise ValueError("Token budget cannot fit one source character")
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if counter.one(remaining[:mid]) <= limit:
                    lo = mid
                else:
                    hi = mid - 1
            out.append(replace(piece, text=remaining[:lo], start=piece.start + start, hard_split=True))
            start += lo
    # Restore original separators while packing the short pieces. In particular,
    # do not turn a long sentence into one newline per word.
    packed = []
    for piece in out:
        if packed:
            prev = packed[-1]
            joined = fragment.text[prev.start - fragment.start:piece.end - fragment.start]
            if counter.one(joined) <= limit:
                packed[-1] = replace(prev, text=joined, hard_split=prev.hard_split or piece.hard_split)
                continue
        packed.append(piece)
    return packed


def pack_units(texts, counter, limit, target, overlap, breaks=(), prefix=""):
    """Pack source units without crossing a section/caption boundary.

    prefix (e.g. a table caption and textual column headings) is repeated in
    every part and counted against the same body limit. Overlap consists of
    whole fragments within the same structural segment only.
    """
    if limit < 1 or target < 1 or overlap < 0:
        raise ValueError("Invalid chunk token budget")
    prefix = prefix.rstrip("\n")

    def body(fragments):
        return "\n".join(([prefix] if prefix else []) + [f.text for f in fragments])

    available = limit - counter.one(prefix + "\n") if prefix else limit
    if available < 1:
        raise ValueError("Repeated context exhausts the chunk body budget")
    expanded = []
    for uid, text in enumerate(texts):
        expanded.extend(split_fragment(Fragment(text, uid), available, counter))
    chunks, current, current_overlap = [], [], 0
    reason, last_uid = "page_start", None
    for fragment in expanded:
        boundary = fragment.uid in breaks and fragment.uid != last_uid
        exceeds = current and counter.one(body(current + [fragment])) > limit
        at_target = current and counter.one(body(current)) >= target
        if current and (boundary or exceeds or at_target):
            chunks.append(Packed(current, reason, current_overlap))
            tail = []
            if not boundary:
                for prev in reversed(current):
                    candidate = [prev] + tail
                    if counter.one("\n".join(x.text for x in candidate)) > overlap:
                        break
                    if counter.one(body(candidate + [fragment])) > limit:
                        break
                    tail = candidate
            current = tail
            current_overlap = counter.one("\n".join(x.text for x in tail)) if tail else 0
            reason = "structure_boundary" if boundary else "size_limit" if exceeds else "target_size"
        # Token counts of prefix + body need not equal the sum of counts.
        if counter.one(body(current + [fragment])) > limit:
            # Re-split with an exact assembled-budget guard for tokenizer merges.
            smaller = split_fragment(fragment, max(1, available - 8), counter)
            if len(smaller) == 1 and smaller[0].text == fragment.text:
                raise ValueError("Source fragment exceeds exact assembled token budget")
            sub = pack_units([x.text for x in smaller], counter, limit, target, 0, prefix=prefix)
            if current:
                chunks.append(Packed(current, reason, current_overlap))
            for packed in sub:
                mapped = []
                for f in packed.fragments:
                    origin = smaller[f.uid]
                    mapped.append(replace(f, uid=fragment.uid, start=origin.start + f.start,
                                          hard_split=origin.hard_split or f.hard_split))
                chunks.append(Packed(mapped, "size_limit"))
            current, current_overlap, reason = [], 0, "size_limit"
        else:
            current.append(fragment)
        last_uid = fragment.uid
    if current:
        chunks.append(Packed(current, reason, current_overlap))
    return chunks
