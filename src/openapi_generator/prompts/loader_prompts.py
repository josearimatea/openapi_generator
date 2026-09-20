"""
Prompt for the Loader's cover-page pass.

`info` and `externalDocs` describe the document rather than the API, so no rule
in the bank covers them — but the specification states them outright on its
cover page. The trouble is where: a source arrives as PDF or DOCX and is
converted to Markdown, and the two produce different shapes for the same page
(a table of pipe-delimited cells, or a run of loose lines with arbitrary
breaks). The values are always written; their position is not. Reading the page
therefore beats matching a pattern against it.

The same pass also settles the document's title. A specification defines
several management services and the generated document covers one, so its
title is the service's full name — which the cover cannot supply, since the
cover describes the whole specification. The name is written in the normative
clauses instead, so passages mentioning the target's short name travel with the
cover page and the one pass answers both questions.

Inputs interpolated into the prompt:
    cover_page       — the opening of the converted specification, where the
                       identifying block sits.
    target           — short name of the service this document covers; empty
                       when the run did not name one.
    service_mentions — passages from the normative clauses mentioning that
                       short name; empty when there is no target or no mention.
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

THE SERVICE NAME comes from a different place and answers a different question.
A specification defines several services and the document being generated
covers ONE of them, identified by the short name given as the target. Passages
mentioning that short name follow the cover page. Read them and settle three
things:

  1. `services_defined` — the services this specification defines, as the
     passages name them. List what you find; an empty list is the right answer
     when the passages name none.

  2. `service_recognised` — whether this specification defines the service the
     target names. False when it does not: a target belonging to another
     specification, or misspelt, is a mistake to report rather than a name to
     invent. Report True when no target was given, since nothing contradicts
     it.

  3. `service_title` — how the document should name that service. Prefer the
     full name the passages write for it. When they only ever use the short
     name, answer with the short name itself: it is what the specification
     calls the service, and is correct where an expansion you compose would
     be a guess. Take the name of the service, not of the clause mentioning
     it and not of the specification, and never a description in place of a
     name.

Leave `service_title` empty when no target was given or when
`service_recognised` is False. What goes here becomes the title of the
published document, so a name you are unsure of is worse than none.
"""

_USER = """\
COVER PAGE:
{cover_page}

TARGET SERVICE (short name; empty when the run did not name one):
{target}

PASSAGES MENTIONING THAT SHORT NAME (from the normative clauses; may be empty):
{service_mentions}

Report the LoaderDocumentMetadata now.
"""

loader_metadata_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _USER),
])
