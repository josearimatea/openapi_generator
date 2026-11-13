"""Stand-ins for the LLM, so node logic can be tested without a provider."""

from typing import Any

from langchain_core.runnables import RunnableLambda


class FakeLLM:
    """A chat model whose structured output is always `answer`.

    `answer=None` reproduces a provider that replied in a shape the schema did
    not accept: `with_structured_output(...)` then returns None instead of
    raising.
    """

    def __init__(self, answer: Any = None) -> None:
        self.answer = answer
        self.calls = 0

    def with_structured_output(self, schema: Any, **kwargs: Any) -> RunnableLambda:
        def respond(_: Any) -> Any:
            self.calls += 1
            return self.answer

        return RunnableLambda(respond)
