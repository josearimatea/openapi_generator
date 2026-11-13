"""
Invoke a structured-output chain and refuse an answer that did not parse.

`llm.with_structured_output(...)` can return None instead of raising: the model
answered, but not in a shape the schema accepts. Every caller here dereferences
the result (`verdict.fixes`, `review.issues`, ...), so a None that slips through
becomes an AttributeError far from its cause and takes the whole run with it.

The correction pass of the Patcher was the first to meet this and guarded it in
place; every other call had the same exposure. Raising here instead turns the
None into an ordinary failure of the call, which each caller already handles in
its `except` with the fallback it chose for a failed LLM call.
"""

from typing import Any, Dict


class EmptyStructuredOutput(ValueError):
    """The structured-output call returned nothing that parsed."""


def invoke_structured(chain: Any, inputs: Dict[str, Any], what: str = "") -> Any:
    """`chain.invoke(inputs)`, raising EmptyStructuredOutput on a None answer."""
    result = chain.invoke(inputs)
    if result is None:
        raise EmptyStructuredOutput(
            f"{what or 'structured-output call'} returned nothing that parsed"
        )
    return result
