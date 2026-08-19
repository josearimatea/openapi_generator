"""Unit tests for the eval comparison — no LLM, no Qdrant."""

from openapi_generator.config.settings import OPENAPI_VERSION
from openapi_generator.eval import compare
from openapi_generator.eval.comparison import classify, flatten


def _doc(schemas=None, paths=None, **top):
    return {
        "openapi": OPENAPI_VERSION,
        "info": {"title": "t", "version": "1.0"},
        "paths": paths if paths is not None else {"/x": {"post": {"responses": {"204": {}}}}},
        "components": {"schemas": schemas or {}},
        **top,
    }


def test_flatten_keeps_list_position():
    leaves = flatten({"allOf": [{"$ref": "X"}, {"type": "object"}]})
    assert leaves == {"allOf[0].$ref": "X", "allOf[1].type": "object"}


def test_classify_splits_the_three_groups():
    assert classify("components.schemas.Foo.type") == "contract"
    assert classify("paths./x.post.responses.204.description") == "prose"
    assert classify("info.version") == "metadata"
    assert classify("servers[0].url") == "metadata"
    # A description inside a schema is still prose — judged by meaning.
    assert classify("components.schemas.Foo.properties.bar.description") == "prose"


def test_identical_documents_score_perfectly():
    doc = _doc({"Foo": {"type": "string", "enum": ["A"]}})
    result = compare(doc, doc)
    assert result.contract.absent == []
    assert result.contract.differing == []
    assert result.contract.ratio == 1.0
    assert result.schemas_missing == [] and result.schemas_extra == []


def test_missing_schema_is_reported_as_absent_contract():
    reference = _doc({"Foo": {"type": "string"}, "Bar": {"type": "integer"}})
    result = compare(_doc({"Foo": {"type": "string"}}), reference)
    assert result.schemas_missing == ["Bar"]
    assert "components.schemas.Bar.type" in result.contract.absent


def test_wrong_type_is_reported_as_differing():
    result = compare(_doc({"Foo": {"type": "object"}}), _doc({"Foo": {"type": "string"}}))
    assert [p for p, _, _ in result.contract.differing] == ["components.schemas.Foo.type"]


def test_prose_difference_does_not_touch_the_contract():
    reference = _doc(paths={"/x": {"post": {"description": "Send it", "responses": {"204": {}}}}})
    generated = _doc(paths={"/x": {"post": {"description": "Sends it.", "responses": {"204": {}}}}})
    result = compare(generated, reference)
    assert result.contract.differing == []
    assert result.contract.absent == []
    assert len(result.groups["prose"].differing) == 1


def test_missing_servers_lands_in_metadata_not_contract():
    result = compare(_doc(), _doc(servers=[{"url": "{root}"}]))
    assert result.contract.absent == []
    assert "servers[0].url" in result.groups["metadata"].absent


def test_extra_contract_leaf_is_listed_not_counted_as_error():
    """A rule may state what the reference leaves implicit (a `required` list)."""
    reference = _doc({"Foo": {"type": "object", "properties": {"a": {"type": "string"}}}})
    generated = _doc({
        "Foo": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    })
    result = compare(generated, reference)
    assert result.contract.absent == [] and result.contract.differing == []
    assert result.contract.extra == ["components.schemas.Foo.required[0]"]


def test_ref_siblings_are_flagged():
    generated = _doc({
        "Foo": {"properties": {
            "a": {"$ref": "#/components/schemas/Bar", "description": "dead text"},
            "b": {"$ref": "#/components/schemas/Bar"},
        }}
    })
    result = compare(generated, _doc())
    assert result.refs_total == 2
    assert result.refs_with_siblings == ["components.schemas.Foo.properties.a"]


def test_composition_member_order_is_significant():
    """allOf members are positional: a schema that moved is not a match."""
    reference = _doc({"Foo": {"allOf": [{"$ref": "X"}, {"type": "object"}]}})
    generated = _doc({"Foo": {"allOf": [{"type": "object"}, {"$ref": "X"}]}})
    result = compare(generated, reference)
    assert result.contract.absent or result.contract.differing
