"""Paths are matched as paths, never by substring (P20)."""

import pytest

from openapi_generator.nodes import planner
from openapi_generator.nodes.planner import _rule_touches_op, _same_path
from tests.unit._fakes import FakeLLM


def _op(path, method):
    return {"rule_type": "path_operation", "source_name": f"{method} {path}",
            "openapi_mapping": {"openapi_object": f"paths.{path}", "openapi_field": method}}


def _path_param(path, name):
    return {"rule_type": "path_parameter", "source_name": name,
            "openapi_mapping": {"openapi_object": f"paths.{path}",
                                "openapi_field": f"parameters[in=path,name={name}]"}}


@pytest.mark.parametrize("a, b, same", [
    ("/alarms", "/alarms", True),
    ("/alarms", "/alarms/{alarmId}", False),          # nested: a different path
    ("/alarms/{alarmId}", "/alarms", False),
    ("/{MnSRoot}/FaultMnS/v1/alarms", "/alarms", True),  # a server prefix set aside
    ("/x/alarms", "alarms", False),                   # a bare suffix is not a path
    ("", "/alarms", False),
])
def test_same_path(a, b, same):
    assert _same_path(a, b) is same


def test_pass1_does_not_hand_a_nested_paths_parameter_to_its_parent():
    rule = _path_param("/alarms/{alarmId}", "alarmId")
    assert _rule_touches_op("/alarms/{alarmId}", "get", set(), rule)
    assert not _rule_touches_op("/alarms", "get", set(), rule)


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


def test_phase_b_attaches_a_path_parameter_only_to_its_own_path(planned):
    by_path = {op["path"]: op["source_rule_ids"] for op in planned["operations_plan"]}
    assert 2 in by_path["/alarms/{alarmId}"]
    assert 2 not in by_path["/alarms"]
