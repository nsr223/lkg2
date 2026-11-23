"""Check the bracket numbers in an answer against the sources it was given.

Small models often write the right article in words but put the number of a different fragment next to it,
e.g. "artículo 1964 del Código Civil [2]" when [2] is article 1967 and article 1964 is [1].
check() looks at each [n], finds the article named just before it, and:
  - if [n] is that article: nothing to do;
  - if exactly one other source is that article: changes [n] to that source's number;
  - otherwise (the article is not among the sources, or several are): leaves the answer as it is and notes it.
Only article numbers are compared (with bis, ter...); a bracket with no article named before it is not checked.
"""
import re

SUFFIX = r"(?:bis|ter|quater|quáter|quinquies|sexies|septies|octies|nonies|decies)"
ART = re.compile(rf"\b(?:art[íi]culos?|arts?\.)\s*(\d+)(?:\.\d+)*(?:\s+({SUFFIX})\b)?", re.I)
UNIT = re.compile(rf"^art[íi]culo\s+(\d+)(?:\s+({SUFFIX})\b)?", re.I)
BRACKET = re.compile(r"\[(\d+)\]")
WINDOW = 200   # look this far back for the article a bracket belongs to, but not past the end of a sentence
SENTENCE_END = re.compile(r"(?<![Aa]rt)(?<![Aa]rts)\.\s|\n")


def _key(number, suffix):
    return f"{int(number)} {suffix.lower().replace('á', 'a')}" if suffix else str(int(number))


def unit_key(unit):
    m = UNIT.match(unit or "")
    return _key(*m.groups()) if m else None


def check(answer, sources):
    """Returns (fixed answer, notes). sources: [{"n": 1, "law": ..., "unit": "Artículo 1964"}, ...]."""
    by_n = {s["n"]: s for s in sources}
    notes, out, last, prev_end = [], [], 0, 0
    for m in BRACKET.finditer(answer):
        n = int(m.group(1))
        start = max(prev_end, m.start() - WINDOW)
        ends = list(SENTENCE_END.finditer(answer, start, m.start()))
        if ends:
            start = ends[-1].end()
        prev_end = m.end()
        named = list(ART.finditer(answer, start, m.start()))
        new = n
        if named and n in by_n:
            want = _key(*named[-1].groups())
            if unit_key(by_n[n]["unit"]) != want:
                same = [s for s in sources if unit_key(s["unit"]) == want]
                if len(same) > 1:   # one article number in several laws: prefer the law of the cited source
                    same = [s for s in same if s["law"] == by_n[n]["law"]] or same
                if len(same) == 1:
                    new = same[0]["n"]
                    notes.append({"bracket": n, "fixed_to": new, "article": want})
                else:
                    notes.append({"bracket": n, "article": want,
                                  "problem": "not among the sources" if not same else "in several sources"})
        out.append(answer[last:m.start()] + f"[{new}]")
        last = m.end()
    out.append(answer[last:])
    return "".join(out), notes
