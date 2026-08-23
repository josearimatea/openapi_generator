"""
Prompt for the Loader's cover-page pass.

`info` and `externalDocs` describe the document rather than the API, so no rule
in the bank covers them — but the specification states them outright on its
cover page. The trouble is where: a source arrives as PDF or DOCX and is
converted to Markdown, and the two produce different shapes for the same page
(a table of pipe-delimited cells, or a run of loose lines with arbitrary
breaks). The values are always written; their position is not. Reading the page
therefore beats matching a pattern against it.

Inputs interpolated into the prompt:
    cover_page — the opening of the converted specification, where the
                 identifying block sits.
"""

from langchain_core.prompts import ChatPromptTemplate

_SYSTEM = """\
You read the cover page of a technical specification and report what it says
about the document itself.

The page was converted from PDF or DOCX, so its layout is unpredictable: it may
appear as a table of cells, as loose lines, or with words split across breaks.
Read it as a person would, ignoring the conversion artefacts — pipe characters,
row rules, stray backslashes.

What to report:
  - the specification number, as printed (e.g. "28.532"): digits and dot only,
    dropping any "TS" prefix;
  - the document version (e.g. "18.0.0"), dropping any leading "V";
  - the release number alone (e.g. "18" from "(Release 18)");
  - the subject: the line naming what this document is ABOUT. A cover narrows
    from the organisation, through the group, to the subject — take the last
    step, not the earlier ones, and not the document number;
  - the copyright notice as a single line, from the © symbol through the
    rights reservation, worded exactly as printed and joined into one line if
    the conversion split it.

Report only what the page states. Leave a field empty when the page does not
say — never infer a version from a number, a release from a year, or a
copyright from a template.

Report the values in the language the page uses; these are quoted into the
published document, so they must read as the specification wrote them, not as
a paraphrase or a translation.
"""

_USER = """\
COVER PAGE:
{cover_page}

Report the DocumentMetadata now.
"""

loader_metadata_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _USER),
])
