"""
Path configurations for data and files.

Mirrors openapi_rulesbank/config/paths.py — base paths derived from this
file's location so they resolve correctly regardless of the working
directory (notebooks, scripts, tests). No side effects on import.
"""

import os

# Anchor: resolve ROOT from this file's location.
# paths.py is at src/openapi_generator/config/paths.py → three levels up
# is the project root.
_THIS_FILE = os.path.abspath(__file__)
ROOT       = f"{os.path.dirname(_THIS_FILE)}/../../.."

# --------------------------------------------------------
# Project data layout
DATA_DIR             = f"{ROOT}/data"
INPUTS_DIR           = f"{DATA_DIR}/inputs"
INPUTS_3GPP_DIR      = f"{INPUTS_DIR}/3gpp"
INPUTS_RULES_DIR     = f"{INPUTS_DIR}/rules"
INPUTS_LEGACY_DIR    = f"{INPUTS_DIR}/legacy"
INPUTS_AUXILIARY_DIR = f"{INPUTS_DIR}/auxiliary"

OUTPUTS_DIR              = f"{DATA_DIR}/outputs"
OUTPUTS_TEST_LOADER_DIR  = f"{OUTPUTS_DIR}/test_loader"
OUTPUTS_TEST_PLANNER_DIR = f"{OUTPUTS_DIR}/test_planner"
OUTPUTS_TEST_PATCHER_DIR = f"{OUTPUTS_DIR}/test_patcher"

# --------------------------------------------------------
# Test fixtures consumed by the notebooks in tests/unit/.
# Pin the exact files we test against so glob ordering doesn't surprise us.
TEST_SPEC_PATH   = f"{INPUTS_3GPP_DIR}/28532-i00.md"
TEST_RULES_PATH  = f"{INPUTS_RULES_DIR}/rules_bank_28532-i00_full_20260427_214528.json"
TEST_LEGACY_PATH = f"{INPUTS_LEGACY_DIR}/rel_17_TS28532_ProvMnS.yaml"
