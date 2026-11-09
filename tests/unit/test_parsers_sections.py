# tests/unit/test_parsers_sections.py

"""
Unit tests for parse_sections() — no external deps.

The cut is qdrant_3gpp_rag's split_sections (unstyled_titles=True), the same one
openapi_rulesbank uses: every heading level is a section, the pandoc {#attrs} leave
the title, and a clause title the document left as plain text is a section of its own.
"""

from openapi_generator.utils.parsers import parse_sections


def _titles(md: str) -> list[str]:
    kept, _ = parse_sections(md)
    return [s["title"] for s in kept]


def test_level1_heading_is_its_own_section():
    md = "## 11.6.2 Last\nlast body\n\n# 12 Next\nnext body\n\n## 12.1 Child\nchild body\n"
    kept, _ = parse_sections(md)
    assert [s["title"] for s in kept] == ["11.6.2 Last", "12 Next", "12.1 Child"]
    assert kept[0]["content"] == "last body"


def test_deeper_headings_leave_no_marker_behind():
    md = "## 1 Resources\nbody a\n\n#### 1.1 Resource structure\nbody b\n"
    kept, _ = parse_sections(md)
    assert kept[0]["content"] == "body a"


def test_pandoc_attributes_are_removed_from_the_title():
    kept, _ = parse_sections("## 1.1 HTTP PUT {#http-put .H6}\nbody\n")
    assert kept[0]["title"] == "1.1 HTTP PUT"


def test_a_plain_clause_title_becomes_a_section():
    md = "## 1.1 Parent\n\n###### 1.1.9 Type A\n\ntext a\n\n1.1.10 Type B\n\ntext b\n"
    kept, _ = parse_sections(md)
    b = next(s for s in kept if s["title"] == "1.1.10 Type B")
    assert b["content"] == "text b"


def test_a_sentence_alone_in_its_paragraph_is_left_alone():
    md = "## 1.1 Parent\n\n1.1.1 Spurious responses are applicable.\n\ntext\n"
    assert _titles(md) == ["1.1 Parent"]


def test_text_before_the_first_heading_is_front_matter():
    kept, excluded = parse_sections("cover page\n\n## 1 Scope\nbody\n")
    assert [s["title"] for s in kept] == ["1 Scope"]
    assert excluded[0]["reason"] == "front_matter"
