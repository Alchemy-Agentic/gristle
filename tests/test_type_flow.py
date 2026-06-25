"""Tests for type flow analysis — typed parameters, type fields, RETURNS/ACCEPTS edges."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gristle.graph.client import QueryResult
from gristle.models import ParsedTypeField
from gristle.query.engine import QueryEngine


def _qr(records: list[dict]) -> QueryResult:
    return QueryResult(records=records, summary={})


def _empty() -> QueryResult:
    return _qr([])


def _make_engine():
    mock_graph = MagicMock()
    mock_graph.repo_id = "test"
    engine = QueryEngine(mock_graph)
    return engine, mock_graph


# ======================================================================
# TypeScript Parser: Interface/Type Field Extraction
# ======================================================================


class TestTSInterfaceFieldExtraction:
    def test_interface_fields(self):
        from gristle.parsers.typescript import TypeScriptParser

        parser = TypeScriptParser()
        result = parser.parse_file(
            "models.ts",
            """
interface User {
    name: string;
    email?: string;
    age: number;
}
""",
        )
        assert len(result.classes) == 1
        cls = result.classes[0]
        assert cls.kind == "interface"
        assert len(cls.fields) == 3
        # Check field names and types
        field_map = {f.name: f for f in cls.fields}
        assert field_map["name"].type_annotation == "string"
        assert field_map["name"].is_optional is False
        assert field_map["email"].type_annotation == "string"
        assert field_map["email"].is_optional is True
        assert field_map["age"].type_annotation == "number"

    def test_type_alias_object_fields(self):
        from gristle.parsers.typescript import TypeScriptParser

        parser = TypeScriptParser()
        result = parser.parse_file(
            "config.ts",
            """
type Config = {
    debug: boolean;
    port: number;
};
""",
        )
        assert len(result.classes) == 1
        cls = result.classes[0]
        assert cls.kind == "type"
        assert len(cls.fields) == 2
        field_map = {f.name: f for f in cls.fields}
        assert field_map["debug"].type_annotation == "boolean"
        assert field_map["port"].type_annotation == "number"

    def test_class_property_fields(self):
        from gristle.parsers.typescript import TypeScriptParser

        parser = TypeScriptParser()
        result = parser.parse_file(
            "user.ts",
            """
class UserEntity {
    name: string;
    age: number;
    constructor(name: string, age: number) {
        this.name = name;
        this.age = age;
    }
}
""",
        )
        assert len(result.classes) == 1
        cls = result.classes[0]
        # Should have field entries for name and age
        assert len(cls.fields) >= 2
        field_names = {f.name for f in cls.fields}
        assert "name" in field_names
        assert "age" in field_names

    def test_empty_interface_no_fields(self):
        from gristle.parsers.typescript import TypeScriptParser

        parser = TypeScriptParser()
        result = parser.parse_file(
            "empty.ts",
            """
interface Empty {}
""",
        )
        assert len(result.classes) == 1
        assert len(result.classes[0].fields) == 0


# ======================================================================
# TypeScript Parser: Typed Parameter Extraction
# ======================================================================


class TestTSTypedParams:
    def test_function_typed_params(self):
        from gristle.parsers.typescript import TypeScriptParser

        parser = TypeScriptParser()
        result = parser.parse_file(
            "api.ts",
            """
function getUser(id: string, options: RequestOptions): User {
    return db.find(id);
}
""",
        )
        assert len(result.functions) == 1
        func = result.functions[0]
        assert func.typed_parameters == [("id", "string"), ("options", "RequestOptions")]
        assert func.parameters == ["id", "options"]

    def test_arrow_function_typed_params(self):
        from gristle.parsers.typescript import TypeScriptParser

        parser = TypeScriptParser()
        result = parser.parse_file(
            "utils.ts",
            """
const format = (value: number, locale: string): string => {
    return value.toLocaleString(locale);
};
""",
        )
        assert len(result.functions) == 1
        func = result.functions[0]
        assert func.typed_parameters == [("value", "number"), ("locale", "string")]

    def test_method_typed_params(self):
        from gristle.parsers.typescript import TypeScriptParser

        parser = TypeScriptParser()
        result = parser.parse_file(
            "service.ts",
            """
class UserService {
    async create(data: CreateUserDTO): Promise<User> {
        return this.repo.save(data);
    }
}
""",
        )
        assert len(result.classes) == 1
        method = result.classes[0].methods[0]
        assert method.typed_parameters == [("data", "CreateUserDTO")]
        assert method.return_type == "Promise<User>"

    def test_untyped_params(self):
        from gristle.parsers.typescript import TypeScriptParser

        parser = TypeScriptParser()
        result = parser.parse_file(
            "plain.js",
            """
function add(a, b) {
    return a + b;
}
""",
        )
        assert len(result.functions) == 1
        func = result.functions[0]
        # Untyped JS params should still be captured with None types
        assert func.typed_parameters == [("a", None), ("b", None)]
        assert func.parameters == ["a", "b"]


# ======================================================================
# Python Parser: Typed Parameter Extraction
# ======================================================================


class TestPyTypedParams:
    def test_typed_params(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "service.py",
            """
def create_user(name: str, age: int = 0) -> User:
    return User(name=name, age=age)
""",
        )
        assert len(result.functions) == 1
        func = result.functions[0]
        assert func.typed_parameters == [("name", "str"), ("age", "int")]
        assert func.return_type == "User"

    def test_mixed_typed_untyped(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "mixed.py",
            """
def process(data: dict, verbose=False):
    pass
""",
        )
        func = result.functions[0]
        assert func.typed_parameters == [("data", "dict"), ("verbose", None)]

    def test_self_cls_excluded(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "cls.py",
            """
class Foo:
    def method(self, x: int) -> str:
        pass

    @classmethod
    def from_data(cls, data: dict) -> 'Foo':
        pass
""",
        )
        methods = result.classes[0].methods
        assert len(methods) == 2
        # self/cls should be excluded
        assert methods[0].typed_parameters == [("x", "int")]
        assert methods[1].typed_parameters == [("data", "dict")]


# ======================================================================
# Python Parser: Dataclass/Pydantic Field Extraction
# ======================================================================


class TestPyClassFields:
    def test_dataclass_fields(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "models.py",
            """
from dataclasses import dataclass

@dataclass
class User:
    name: str
    email: str = ""
    age: int = 0
""",
        )
        assert len(result.classes) == 1
        cls = result.classes[0]
        assert len(cls.fields) == 3
        field_map = {f.name: f for f in cls.fields}
        assert field_map["name"].type_annotation == "str"
        assert field_map["name"].default_value is None
        assert field_map["email"].type_annotation == "str"
        assert field_map["email"].default_value == '""'
        assert field_map["age"].type_annotation == "int"
        assert field_map["age"].default_value == "0"

    def test_pydantic_model_fields(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "schema.py",
            """
class Config(BaseModel):
    debug: bool = False
    port: int = 8080
""",
        )
        cls = result.classes[0]
        assert len(cls.fields) == 2
        field_map = {f.name: f for f in cls.fields}
        assert field_map["debug"].type_annotation == "bool"
        assert field_map["port"].type_annotation == "int"

    def test_typeddict_fields(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "types.py",
            """
class UserDict(TypedDict):
    name: str
    age: int
""",
        )
        cls = result.classes[0]
        assert len(cls.fields) == 2

    def test_regular_class_no_fields(self):
        """Non-dataclass/Pydantic classes should not extract fields."""
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "plain.py",
            """
class Calculator:
    x: int = 0
    def add(self, a: int) -> int:
        return self.x + a
""",
        )
        cls = result.classes[0]
        # Regular class without @dataclass or BaseModel -> no fields extracted
        assert len(cls.fields) == 0

    def test_optional_field_detected(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "opt.py",
            """
from dataclasses import dataclass
from typing import Optional

@dataclass
class Profile:
    name: str
    bio: Optional[str] = None
    avatar: str | None = None
""",
        )
        cls = result.classes[0]
        field_map = {f.name: f for f in cls.fields}
        assert field_map["name"].is_optional is False
        assert field_map["bio"].is_optional is True
        assert field_map["avatar"].is_optional is True


# ======================================================================
# Pipeline: TypeField Nodes and HAS_FIELD Edges
# ======================================================================


class TestPipelineTypeFields:
    def test_typefield_nodes_created(self):
        """Phase 1 should create TypeField nodes for class fields."""
        from gristle.ingestion.batch import BatchCollector
        from gristle.ingestion.pipeline import IngestionPipeline
        from gristle.models import ParsedClass

        mock_graph = MagicMock()
        mock_graph.repo_id = "test"
        pipeline = IngestionPipeline(mock_graph)

        batch = BatchCollector(mock_graph, batch_size=100)
        cls = ParsedClass(
            name="User",
            qualified_name="models.ts::User",
            file_path="models.ts",
            start_line=1,
            end_line=5,
            signature="interface User",
            kind="interface",
            fields=[
                ParsedTypeField(name="name", type_annotation="string", file_path="models.ts", line=2),
                ParsedTypeField(
                    name="email", type_annotation="string", is_optional=True, file_path="models.ts", line=3
                ),
            ],
        )

        file_id = "file::models.ts"
        pipeline._build_class(file_id, cls, batch)

        # Check TypeField nodes were added to batch
        assert "TypeField" in batch._nodes
        assert len(batch._nodes["TypeField"]) == 2

        # Check HAS_FIELD edges
        has_field_rels = batch._create_rels.get("HAS_FIELD", [])
        assert len(has_field_rels) == 2
        assert has_field_rels[0]["from_id"] == "class::models.ts::User"
        assert has_field_rels[0]["to_id"] == "typefield::models.ts::User.name"


# ======================================================================
# Pipeline: Generic Unwrapping
# ======================================================================


class TestGenericUnwrapping:
    def test_promise(self):
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("Promise<User>") == "User"

    def test_array_angle(self):
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("Array<User>") == "User"

    def test_array_bracket(self):
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("User[]") == "User"

    def test_python_list(self):
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("list[User]") == "User"

    def test_optional(self):
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("Optional[User]") == "User"

    def test_dict_extracts_value(self):
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("dict[str, User]") == "User"

    def test_no_generic(self):
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("User") == "User"

    def test_record_type(self):
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("Record<string, User>") == "User"

    def test_nested_promise_array(self):
        """Promise<UserEntity[]> should peel both layers to UserEntity (real TypeORM shape)."""
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("Promise<UserEntity[]>") == "UserEntity"

    def test_nested_list_dict(self):
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("list[dict[str, User]]") == "User"

    def test_optional_shorthand_union(self):
        """`Article | None` resolves to Article (Optional shorthand)."""
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("Article | None") == "Article"

    def test_ambiguous_union_left_intact(self):
        """A multi-type union is ambiguous and is not reduced to a single type."""
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("User | Comment") == "User | Comment"

    def test_nested_promise_optional(self):
        from gristle.ingestion.pipeline import IngestionPipeline

        assert IngestionPipeline._unwrap_generic("Promise<User | null>") == "User"


# ======================================================================
# Query Engine: get_data_contract
# ======================================================================


class TestGetDataContract:
    def test_returns_contract(self):
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr(
                [
                    {
                        "qualified_name": "api.ts::createUser",
                        "signature": "createUser(data: CreateUserDTO): Promise<User>",
                    }
                ]
            ),
            _qr([{"type_name": "User", "type_qname": "models.ts::User", "kind": "interface"}]),
            _qr(
                [
                    {
                        "param_name": "data",
                        "type_name": "CreateUserDTO",
                        "type_qname": "dto.ts::CreateUserDTO",
                        "kind": "interface",
                    }
                ]
            ),
            # Fields for User
            _qr(
                [
                    {"name": "id", "type_annotation": "string", "is_optional": False},
                    {"name": "email", "type_annotation": "string", "is_optional": False},
                ]
            ),
            # Fields for CreateUserDTO
            _qr(
                [
                    {"name": "email", "type_annotation": "string", "is_optional": False},
                    {"name": "password", "type_annotation": "string", "is_optional": False},
                ]
            ),
        ]
        result = engine.get_data_contract("createUser")
        assert result is not None
        assert result["entity"] == "api.ts::createUser"
        assert result["output"]["type"] == "User"
        assert len(result["output"]["fields"]) == 2
        assert len(result["inputs"]) == 1
        assert result["inputs"][0]["param_name"] == "data"
        assert result["inputs"][0]["type"] == "CreateUserDTO"

    def test_not_found(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        result = engine.get_data_contract("nonexistent")
        assert result is None

    def test_no_type_edges(self):
        """Function exists but has no RETURNS/ACCEPTS edges."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"qualified_name": "utils.ts::add", "signature": "add(a: number, b: number): number"}]),
            _empty(),  # No RETURNS
            _empty(),  # No ACCEPTS
        ]
        result = engine.get_data_contract("add")
        assert result is not None
        assert result["output"] is None
        assert result["inputs"] == []


# ======================================================================
# Query Engine: get_type_usage
# ======================================================================


class TestGetTypeUsage:
    def test_returns_usage(self):
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"name": "User", "qualified_name": "models.ts::User", "kind": "interface", "file_path": "models.ts"}]),
            _qr(
                [
                    {"name": "id", "type_annotation": "string", "is_optional": False},
                    {"name": "email", "type_annotation": "string", "is_optional": False},
                ]
            ),
            _qr([{"function": "api.ts::createUser", "file_path": "api.ts", "param_name": "data"}]),
            _qr([{"function": "api.ts::getUser", "file_path": "api.ts"}]),
            _qr([{"parent_type": "dto.ts::Response", "field_name": "user", "field_type": "User"}]),
        ]
        result = engine.get_type_usage("User")
        assert result is not None
        assert result["type"] == "User"
        assert result["kind"] == "interface"
        assert len(result["fields"]) == 2
        assert len(result["accepted_by"]) == 1
        assert len(result["returned_by"]) == 1
        assert len(result["referenced_in_fields"]) == 1

    def test_not_found(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        result = engine.get_type_usage("NonExistent")
        assert result is None


# ======================================================================
# MCP Tools
# ======================================================================


@pytest.fixture(autouse=True)
def _clean_mcp_state():
    import gristle.mcp.server as srv

    orig_engines = srv._engines.copy()
    srv._engines.clear()
    yield
    srv._engines.clear()
    srv._engines.update(orig_engines)


class TestMCPDataContract:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_data_contract

        result = await gristle_data_contract(entity_name="foo")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_contract(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_data_contract

        engine = MagicMock()
        engine.get_data_contract.return_value = {
            "entity": "mod.foo",
            "signature": "foo(x: int): str",
            "inputs": [{"param_name": "x", "type": "int"}],
            "output": {"type": "str"},
        }
        srv._engines["r1"] = engine
        result = await gristle_data_contract(entity_name="foo")
        assert result["entity"] == "mod.foo"
        engine.get_data_contract.assert_called_once_with("foo")


class TestMCPTypeUsage:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_type_usage

        result = await gristle_type_usage(type_name="User")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_usage(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_type_usage

        engine = MagicMock()
        engine.get_type_usage.return_value = {
            "type": "User",
            "kind": "interface",
            "fields": [],
            "accepted_by": [],
            "returned_by": [],
            "referenced_in_fields": [],
        }
        srv._engines["r1"] = engine
        result = await gristle_type_usage(type_name="User")
        assert result["type"] == "User"
        engine.get_type_usage.assert_called_once_with("User")
