"""The Validator writes its fixes against the whole document (P17)."""

from openapi_generator.nodes.validator import _document_yaml


def test_validator_reads_the_document_whole():
    schemas = {f"S{i}": {"type": "string", "description": "x" * 200} for i in range(200)}
    text = _document_yaml({"components": {"schemas": schemas}})
    assert len(text) > 24000
    assert "S199" in text and "truncated" not in text
