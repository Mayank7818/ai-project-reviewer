"""Tests for code structure extraction.

The contract these tests enforce: every line number reported is a *real* line
number in the supplied text. That is what lets the analysis cite evidence
without the model ever guessing.
"""

from __future__ import annotations

from app.services.analysis.code_structure import (
    MAX_ITEMS_PER_KIND,
    aggregate_signals,
    detect_language,
    extract_all,
    extract_structure,
)

PYTHON_SOURCE = '''import os
from fastapi import FastAPI, HTTPException

app = FastAPI()


class UserService:
    """Docstring."""

    def get_user(self, user_id):
        return db.execute("SELECT * FROM users WHERE id = ?", [user_id])

    async def delete_user(self, user_id):
        pass


def helper(value):
    return value


@app.get("/users/{user_id}")
async def read_user(user_id: int):
    return UserService().get_user(user_id)
'''


def line_of(symbols, name: str) -> int:
    return next(item.line for item in symbols if item.name == name)


# --- language detection -------------------------------------------------------


def test_language_detection() -> None:
    assert detect_language("app/main.py") == "python"
    assert detect_language("src/App.tsx") == "typescript"
    assert detect_language("index.js") == "javascript"
    assert detect_language("main.go") == "go"
    assert detect_language("mystery.zzz") == "unknown"


# --- Python (exact, via ast) --------------------------------------------------


def test_python_extraction_reports_real_line_numbers() -> None:
    structure = extract_structure("app/main.py", PYTHON_SOURCE)
    lines = PYTHON_SOURCE.splitlines()

    assert structure.language == "python"
    assert structure.parse_error is None

    # Every reported line must actually contain the declaration.
    assert "import os" in lines[line_of(structure.imports, "os") - 1]
    assert "class UserService" in lines[line_of(structure.classes, "UserService") - 1]
    assert "def helper" in lines[line_of(structure.functions, "helper") - 1]
    assert "def get_user" in lines[line_of(structure.methods, "UserService.get_user") - 1]


def test_python_captures_imports_classes_methods_and_functions() -> None:
    structure = extract_structure("app/main.py", PYTHON_SOURCE)

    assert {item.name for item in structure.imports} == {"os", "fastapi"}
    assert [item.name for item in structure.classes] == ["UserService"]
    assert {item.name for item in structure.methods} == {
        "UserService.get_user",
        "UserService.delete_user",
    }
    # Module-level only - methods must not be double-counted as functions.
    assert {item.name for item in structure.functions} == {"helper", "read_user"}


def test_unparseable_python_reports_the_error_rather_than_guessing() -> None:
    structure = extract_structure("broken.py", "def oops(:\n    pass\n")

    assert structure.parse_error is not None
    assert "syntax error" in structure.parse_error
    assert structure.functions == []


# --- routes -------------------------------------------------------------------


def test_fastapi_routes_are_found_with_their_line() -> None:
    structure = extract_structure("app/main.py", PYTHON_SOURCE)

    assert len(structure.routes) == 1
    route = structure.routes[0]
    assert route.detail == "GET /users/{user_id}"
    assert PYTHON_SOURCE.splitlines()[route.line - 1].strip().startswith("@app.get")


def test_express_routes_are_found() -> None:
    source = "const app = express()\napp.get('/health', (req, res) => res.json({}))\napp.post(\"/users\", create)\n"

    structure = extract_structure("server.js", source)

    details = {item.detail for item in structure.routes}
    assert "GET /health" in details
    assert "POST /users" in details


def test_spring_routes_are_found() -> None:
    structure = extract_structure(
        "Controller.java", '@GetMapping("/api/items")\npublic List<Item> list() {}\n'
    )

    assert structure.routes[0].detail == "GET /api/items"


# --- other languages ----------------------------------------------------------


def test_javascript_declarations() -> None:
    source = (
        "import React from 'react'\n"
        "const lodash = require('lodash')\n"
        "export class Store {}\n"
        "export default function App() {}\n"
        "export const handler = async () => {}\n"
    )

    structure = extract_structure("src/app.js", source)

    assert {item.name for item in structure.imports} == {"react", "lodash"}
    assert {item.name for item in structure.classes} == {"Store"}
    assert {item.name for item in structure.functions} == {"App", "handler"}


def test_go_declarations() -> None:
    source = 'package main\n\ntype Server struct {\n}\n\nfunc NewServer() *Server {\n}\n'

    structure = extract_structure("main.go", source)

    assert [item.name for item in structure.classes] == ["Server"]
    assert [item.name for item in structure.functions] == ["NewServer"]


def test_sql_declarations() -> None:
    structure = extract_structure(
        "schema.sql", "CREATE TABLE users (\n  id INT\n);\n"
    )

    assert structure.classes[0].name == "users"


def test_unsupported_language_returns_an_empty_structure() -> None:
    structure = extract_structure("data.zzz", "some content")

    assert structure.language == "unknown"
    assert structure.functions == []
    assert structure.parse_error is None


# --- behavioural signals ------------------------------------------------------


def test_signals_record_real_lines() -> None:
    source = (
        "import requests\n"
        "def fetch():\n"
        "    return requests.get('https://example.com')\n"
        "def run(cmd):\n"
        "    subprocess.run(cmd)\n"
    )

    structure = extract_structure("app/net.py", source)

    assert structure.signals["external_api_call"] == [3]
    assert structure.signals["subprocess"] == [5]


def test_authentication_and_database_signals() -> None:
    source = (
        "token = jwt.encode(payload, key)\n"
        "user = session.query(User).first()\n"
        "if not current_user.is_admin:\n"
        "    raise\n"
    )

    structure = extract_structure("app/auth.py", source)

    assert 1 in structure.signals["authentication"]
    assert 2 in structure.signals["database_query"]
    assert 3 in structure.signals["authorization"]


def test_comment_only_lines_are_not_treated_as_behaviour() -> None:
    """A mention in a comment is discussion, not evidence the code does it."""
    structure = extract_structure("app/x.py", "# we should call subprocess.run here\nx = 1\n")

    assert "subprocess" not in structure.signals


def test_aggregate_signals_produces_citable_references() -> None:
    structures = extract_all(
        {
            "a.py": "requests.get('x')\n",
            "b.py": "import x\nrequests.post('y')\n",
        }
    )

    aggregated = aggregate_signals(structures)

    assert "a.py:1" in aggregated["external_api_call"]
    assert "b.py:2" in aggregated["external_api_call"]


# --- limits -------------------------------------------------------------------


def test_per_kind_cap_is_enforced() -> None:
    source = "\n".join(f"def function_{index}():\n    pass" for index in range(200))

    structure = extract_structure("big.py", source)

    assert len(structure.functions) <= MAX_ITEMS_PER_KIND


def test_enormous_file_is_skipped_rather_than_parsed() -> None:
    structure = extract_structure("huge.py", "x = 1\n" * 200_000)

    assert structure.is_empty
    assert structure.line_count > 0


def test_empty_file_is_safe() -> None:
    structure = extract_structure("empty.py", "")

    assert structure.is_empty
    assert structure.parse_error is None
