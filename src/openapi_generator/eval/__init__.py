"""
Evaluation helpers — compare a generated OpenAPI document against a reference.

Kept out of the graph: nothing in the pipeline imports this. It exists to score
a run after the fact, from a notebook, a test, or the command line:

    python -m openapi_generator.eval generated.yaml reference.yaml
"""

from openapi_generator.eval.comparison import (
    Comparison,
    GroupScore,
    compare,
    format_report,
)

__all__ = ["Comparison", "GroupScore", "compare", "format_report"]
