"""A structured-output call that returns None must not take the run down (P18).

Each node is driven with an LLM whose every structured answer is None, and must
fall back the way it already does for a call that raised.
"""

import pytest

from openapi_generator.nodes import planner, reflector, validator
from openapi_generator.utils.structured import EmptyStructuredOutput, invoke_structured
from tests.unit._fakes import FakeLLM


@pytest.fixture(autouse=True)
def no_rag(monkeypatch):
    """Keep the tests off Qdrant: every retrieval comes back empty."""
    monkeypatch.setattr(reflector, "document_rag_context", lambda *a, **k: ("", ""))
    monkeypatch.setattr(planner, "_openapi_rag", lambda query: "")


def test_invoke_structured_raises_on_none_and_passes_answers_through():
    chain = FakeLLM(None).with_structured_output(object)
    with pytest.raises(EmptyStructuredOutput):
        invoke_structured(chain, {}, "probe")
    assert invoke_structured(FakeLLM("ok").with_structured_output(object), {}) == "ok"


def test_validator_accepts_the_document_when_its_answer_does_not_parse():
    state = {
        "final_openapi": {"paths": {"/x": {"get": {"responses": {"200": {"description": "ok"}}}}}},
        "fragment_reflection": {"problems": [{"where": "paths./x.get", "problem": "p"}]},
    }
    out = validator.validator_node(state, llm=FakeLLM(None))
    assert out["validation_errors"] == []


def test_reflector_returns_the_unweighed_findings_when_the_second_pass_does_not_parse():
    document = {"paths": {"/x": {"get": {"responses": {}}}}, "components": {"schemas": {}}}
    out = reflector.reflector_node(
        {"final_openapi": document, "operations_plan": []},
        llm=FakeLLM(None),
        retriever=lambda query, k=6: [],
    )
    reflection = out["fragment_reflection"]
    assert reflection["confirmed"] is False
    # The structural check still found the operation without responses.
    assert any("no responses" in p["problem"] for p in reflection["problems"])


def test_planner_falls_back_on_every_phase_when_no_answer_parses():
    rules = [
        {"rule_type": "path_operation", "source_name": "op",
         "openapi_mapping": {"openapi_object": "paths./x", "openapi_field": "get"}},
        {"rule_type": "schema_property", "source_name": "Foo",
         "openapi_mapping": {"openapi_object": "components/schemas/Foo"}},
    ]
    out = planner.planner_node(
        {"rules_bank": {"rules": rules}}, llm=FakeLLM(None), retriever=lambda q, k=4: []
    )
    plan = out["operations_plan"]
    assert [(op["path"], op["method"]) for op in plan] == [("/x", "get")]
    # Phase C fell back to carrying the schema on the first operation.
    assert sorted(plan[0]["source_rule_ids"]) == [0, 1]
