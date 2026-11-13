"""The graph itself drops the run's specification collection (P23)."""

from openapi_generator.graph import openapi_gen_graph
from openapi_generator.rag import spec_index


def test_the_graph_ends_by_dropping_the_runs_collection(monkeypatch):
    dropped = []
    monkeypatch.setattr(spec_index, "drop_spec_collection", dropped.append)
    out = openapi_gen_graph.finish_node({"spec_collection": "spec_tmp_abc"})
    assert dropped == ["spec_tmp_abc"]
    assert out == {"spec_collection": ""}

    edges = openapi_gen_graph.build_openapi_gen_graph().get_graph().edges
    assert any(e.source == "finish" and e.target == "__end__" for e in edges)
    assert not any(e.source == "validator" and e.target == "__end__" for e in edges)
