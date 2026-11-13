"""The model's report on its correction never reaches the document (P10, the
filter part), at any depth — while a name spelled like a report key is kept."""

from openapi_generator.nodes import patcher, reflector
from openapi_generator.nodes.patcher import _strip_report_keys
from openapi_generator.schemas.operation import PatcherCorrectedDocument
from tests.unit._fakes import FakeLLM


def test_report_keys_are_dropped_at_any_depth():
    paths = {
        "/x": {
            "put": {
                "skipped": ["schemas could not go at the root"],
                "callbacks": {"onEvent": {"applied": ["a.b"], "removed": []}},
                "responses": {"200": {"description": "ok"}},
            },
        },
    }
    dropped = _strip_report_keys(paths, "paths", names=True)
    assert sorted(dropped) == [
        "paths./x.put.callbacks.onEvent.applied",
        "paths./x.put.callbacks.onEvent.removed",
        "paths./x.put.skipped",
    ]
    assert paths == {"/x": {"put": {"callbacks": {"onEvent": {}},
                                    "responses": {"200": {"description": "ok"}}}}}


def test_names_spelled_like_report_keys_are_content():
    components = {
        "schemas": {
            "removed": {"type": "object"},  # a schema called `removed`
            "Change": {
                "type": "object",
                "properties": {"applied": {"type": "boolean"}},
                "required": ["applied"],
            },
        },
    }
    before = repr(components)
    assert _strip_report_keys(components, "components") == []
    assert repr(components) == before


def test_correction_merges_content_and_drops_the_nested_report(monkeypatch):
    monkeypatch.setattr(reflector, "document_rag_context", lambda *a, **k: ("", ""))
    document = {"paths": {"/x": {"get": {"responses": {"200": {"description": "old"}}}}},
                "components": {"schemas": {}}}
    answer = PatcherCorrectedDocument(
        paths={"/x": {"get": {"responses": {"200": {"description": "new"}},
                              "applied": ["paths./x.get.responses.200"]}}},
        applied=["paths./x.get.responses.200"],
    )
    state = {
        "final_openapi": document,
        "validation_errors": [{"action": "change", "ref": "paths./x.get.responses.200",
                               "instruction": "reword"}],
    }
    out = patcher.patcher_node(state, llm=FakeLLM(answer))
    assert out["final_openapi"]["paths"] == {
        "/x": {"get": {"responses": {"200": {"description": "new"}}}}
    }


def test_a_correction_that_does_not_parse_spends_the_round_and_keeps_the_document(monkeypatch):
    monkeypatch.setattr(reflector, "document_rag_context", lambda *a, **k: ("", ""))
    state = {"final_openapi": {"paths": {}}, "op_iteration_count": 1,
             "validation_errors": [{"action": "add", "ref": "paths./y", "instruction": "i"}]}
    out = patcher.patcher_node(state, llm=FakeLLM(None))
    assert out == {"op_iteration_count": 2, "validation_errors": []}
