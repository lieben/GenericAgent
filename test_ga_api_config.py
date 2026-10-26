#!/usr/bin/env python3
"""Regression tests for GA API configuration semantics; never calls the model."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "ga_api_server.py"
CONFIG_FILE = ROOT / "ga_api_config.json"

source = SOURCE.read_text(encoding="utf-8")
tree = ast.parse(source, filename=str(SOURCE))
helper = next(
    node for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "_api_max_turns"
)
code = compile(ast.Module(body=[helper], type_ignores=[]), str(SOURCE), "exec")
config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
namespace = {"CONFIG": config}
exec(code, namespace)

assert namespace["_api_max_turns"]() == config["chat_history"]["max_turns"]
assert config["chat_history"]["max_turns"] > 0

for invalid in ({}, {"chat_history": {}}, {"chat_history": {"max_turns": 0}},
                {"chat_history": {"max_turns": "not-a-number"}}):
    namespace["CONFIG"] = invalid
    try:
        namespace["_api_max_turns"]()
    except RuntimeError:
        pass
    else:
        raise AssertionError(f"Invalid config accepted: {invalid!r}")

print("PASS: configured value is used; missing/invalid values fail; no model call made")
