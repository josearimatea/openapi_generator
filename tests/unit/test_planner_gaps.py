"""The rules the Planner could not place reach the state and the document (P22)."""

import pytest
from langgraph.graph import END, StateGraph

from openapi_generator.graph.state import OpenAPIGenState
from openapi_generator.nodes import planner
from tests.unit._fakes import FakeLLM
from tests.unit.test_planner_paths import _op, _path_param


@pytest.fixture
def planned(monkeypatch):
    """Run the Planner over two nested paths, with every LLM answer failing so
    each operation group lands as its own 'new' operation (Phase A fallback)."""
    monkeypatch.setattr(planner, "_openapi_rag", lambda query: "")
    rules = [
        _op("/alarms", "get"),
        _op("/alarms/{alarmId}", "get"),
        _path_param("/alarms/{alarmId}", "alarmId"),
        # A type the router does not know: it is placed nowhere.
        {"rule_type": "header_parameter", "source_name": "X-Trace",
         "openapi_mapping": {"openapi_object": "paths./alarms"}},
    ]
    document = {"x-ai-generation": {"generator": "openapi_generator"}, "paths": {}}
    return planner.planner_node(
        {"rules_bank": {"rules": rules}, "final_openapi": document},
        llm=FakeLLM(None),
        retriever=lambda q, k=4: [],
    )


def test_unplaced_rules_reach_the_plan_and_the_document(planned):
    gaps = planned["extraction_plan"]["probable_gaps"]
    assert len(gaps) == 1 and "header_parameter" in gaps[0]
    generation = planned["final_openapi"]["x-ai-generation"]
    assert generation["unplaced-rules"] == gaps
    assert generation["generator"] == "openapi_generator"  # the rest is kept


def test_nothing_is_written_to_the_document_when_every_rule_was_placed(monkeypatch):
    monkeypatch.setattr(planner, "_openapi_rag", lambda query: "")
    out = planner.planner_node(
        {"rules_bank": {"rules": [_op("/x", "get")]}, "final_openapi": {"paths": {}}},
        llm=FakeLLM(None),
        retriever=lambda q, k=4: [],
    )
    assert "final_openapi" not in out


def test_the_state_keeps_the_extraction_plan():
    # LangGraph drops an update key the state does not declare, silently.
    graph = StateGraph(OpenAPIGenState)
    graph.add_node("plan", lambda state: {"extraction_plan": {"probable_gaps": ["r"]}})
    graph.set_entry_point("plan")
    graph.add_edge("plan", END)
    out = graph.compile().invoke({"spec_doc_path": "x"})
    assert out["extraction_plan"] == {"probable_gaps": ["r"]}
