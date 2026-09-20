"""
Parsing utilities for 3GPP specification markdown documents.

Splitting strategy mirrors openapi_rulesbank/utils/parsers.py so both
packages process the same spec the same way (section_id values line up
across them).

Unlike the rulesbank version, this module does NOT filter sections by
keyword: the generator iterates the rules from rules_bank rule-by-rule
and consults sections on demand, so every section must remain available.
The only filter kept is the symbolic-title guard, which discards 3GPP
cover-page table borders (titles made entirely of '+', '-', '=').
"""

import re

from openapi_generator.config.settings import FILTER_SYMBOLIC_TITLES


def _has_real_words(title: str) -> bool:
    """Return True if the title contains at least one word with 3+ letters.

    Used to discard symbolic titles like '+---+---+' or '====' that appear
    in 3GPP cover-page tables. Controlled by FILTER_SYMBOLIC_TITLES.
    """
    return bool(re.search(r"[a-zA-Z]{3,}", title))


# An annex heading, as it appears both in the contents listing and where the
# annex itself begins.
_ANNEX_HEADING = re.compile(r"^#*\s*Annex\s+[A-Z]\b", re.IGNORECASE | re.MULTILINE)

# A contents entry ends in a page reference — "[206](#annex-a-...)" — which is
# what separates the listing from the body that repeats the same headings.
_CONTENTS_ENTRY = re.compile(r"\[\d+\]\(#[^)]*\)\s*$", re.MULTILINE)


def normative_only(md_text: str) -> str:
    """The specification's normative text: no contents listing, no annexes.

    Applied once, when the document is read, so that everything downstream
    works on text that cannot contain the answer. A 3GPP specification closes
    with the finished OpenAPI documents in an annex — the very artefact this
    pipeline exists to derive — and any search over the raw document can reach
    them: a query for the service name finds the annex's `title:` line, a query
    for a URI finds its `servers:` block. The result looks like a correct
    reading of the specification and is a copy of the target.

    Cutting once, at the source, is what makes that structural rather than a
    rule each caller has to remember.

    The two ends are entangled, hence the order. The contents listing names
    every annex before the body begins, so each annex heading appears twice —
    once as a listing entry, once where the annex starts. Cutting at the last
    heading would keep every annex but the final one, which in a 3GPP
    specification means keeping the one that holds the OpenAPI documents. So
    the listing goes first, and the annexes are cut at the first heading left.
    """
    body = md_text

    entries = list(_CONTENTS_ENTRY.finditer(body))
    if entries:
        body = body[entries[-1].end():]

    annexes = list(_ANNEX_HEADING.finditer(body))
    return body[: annexes[0].start()] if annexes else body


def parse_sections(md_text: str) -> tuple[list[dict], list[dict]]:
    """Split a Markdown document into kept and excluded sections.

    Strategy:
        1. Split the document on '## '. Because '### ', '#### ', '##### '
           all share that prefix, this captures every header from level 2
           onward. 3GPP specs typically start at level 3, so this is
           intentional.
        2. First line of each chunk is the section title; rest is the body.
        3. If FILTER_SYMBOLIC_TITLES is True (default), discard sections
           whose title contains no real words — these are 3GPP cover-page
           table borders, not real sections.

    Returns:
        (kept, excluded), each a list of dicts:
          kept:     {"section_id": str, "title": str, "content": str}
          excluded: {"section_id": str, "title": str,
                     "reason": "symbolic_title"}
    """
    raw_sections = [s.strip() for s in md_text.split("## ") if s.strip()]

    sections: list[dict] = []
    excluded: list[dict] = []

    for i, section in enumerate(raw_sections):
        lines = section.splitlines()
        title = lines[0].strip()
        content = "\n".join(lines[1:]).strip()

        if FILTER_SYMBOLIC_TITLES and not _has_real_words(title):
            excluded.append({
                "section_id": str(i),
                "title": title,
                "reason": "symbolic_title",
            })
            continue

        sections.append({
            "section_id": str(i),
            "title": title,
            "content": content,
        })

    return sections, excluded
