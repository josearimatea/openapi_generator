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
