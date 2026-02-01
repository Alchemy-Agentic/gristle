> **ARCHIVED — Do not use as reference for current implementation.**
>
> This document was the original design specification written before implementation began.
> The actual implementation diverged significantly — different MCP framework (FastMCP vs custom),
> 3 language parsers instead of 8, different graph schema, no FastAPI layer.
>
> **For current documentation, see:**
> - [README.md](README.md) — Quick start, tools list, configuration
> - [ARCHITECTURE.md](ARCHITECTURE.md) — Graph schema, ingestion pipeline, query engine
> - [MCP_USAGE_GUIDE.md](MCP_USAGE_GUIDE.md) — Tool reference for AI agents

---

# Gristle

## The Connective Tissue of Your Codebase

### Technical Specification for AI-Enhanced Code Understanding

**Version:** 1.0  
**Author:** Paul (Alchemy Agentic)  
**Purpose:** Enable AI agents to navigate and understand complex codebases through graph-based structural queries

---

## 1. Executive Summary

### 1.1 Problem Statement

Current AI code understanding approaches rely primarily on vector search over chunked code, which fundamentally destroys the relational structure that makes code comprehensible. Developers don't understand codebases by reading random snippets—they trace paths, follow dependencies, and understand hierarchies.

### 1.2 Solution

Gristle converts GitHub repositories into a FalkorDB graph database, preserving the structural relationships (calls, imports, inheritance, data flow) that enable genuine code comprehension. This graph can then be queried by AI agents to retrieve precisely the context they need for any code-related task.

### 1.3 Key Value Propositions

- **Structural queries**: "What calls this function?" becomes a trivial graph traversal
- **Impact analysis**: "What breaks if I change this?" via dependency path analysis
- **Data flow tracing**: "How does user input reach the database?" through flow traversal
- **Architecture extraction**: Pull coherent subgraphs representing system components
- **Precise context retrieval**: Give AI agents exactly the code context they need, not keyword-matched chunks

---

## 2. System Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Gristle System                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │   GitHub     │───▶│    Parser    │───▶│    Graph     │───▶│ FalkorDB  │ │
│  │   Ingestion  │    │    Engine    │    │   Builder    │    │  Storage  │ │
│  └──────────────┘    └──────────────┘    └──────────────┘    └───────────┘ │
│         │                   │                   │                   │       │
│         ▼                   ▼                   ▼                   ▼       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │   File       │    │  Tree-sitter │    │   Relation   │    │   Query   │ │
│  │   Walker     │    │     ASTs     │    │   Extractor  │    │   Engine  │ │
│  └──────────────┘    └──────────────┘    └──────────────┘    └───────────┘ │
│                                                                      │       │
│                                                                      ▼       │
│                                                              ┌───────────┐   │
│                                                              │    API    │   │
│                                                              │   Layer   │   │
│                                                              └───────────┘   │
│                                                                      │       │
└──────────────────────────────────────────────────────────────────────│───────┘
                                                                       │
                                                                       ▼
                                                              ┌───────────────┐
                                                              │   AI Agent    │
                                                              │  Integration  │
                                                              └───────────────┘
```

### 2.2 Component Overview

| Component | Responsibility | Technology |
|-----------|----------------|------------|
| GitHub Ingestion | Clone repos, track commits, manage file access | PyGithub, GitPython |
| Parser Engine | Generate ASTs from source files | Tree-sitter (multi-language) |
| Graph Builder | Transform ASTs into graph nodes/edges | Custom Python |
| FalkorDB Storage | Persist and query the code graph | FalkorDB (Redis-based) |
| Query Engine | Execute graph queries, return structured results | Cypher queries |
| API Layer | Expose graph operations to external consumers | FastAPI |
| AI Agent Integration | MCP server + tool definitions for AI agents | MCP Protocol |

### 2.3 Technology Stack

```yaml
Runtime:
  - Python 3.11+
  - FastAPI (async web framework)
  - Uvicorn (ASGI server)

Parsing:
  - tree-sitter (core parsing library)
  - tree-sitter-python
  - tree-sitter-javascript
  - tree-sitter-typescript
  - tree-sitter-go
  - tree-sitter-rust
  - tree-sitter-java
  - tree-sitter-c
  - tree-sitter-cpp

Database:
  - FalkorDB (graph database)
  - Redis (FalkorDB backend)

Git Integration:
  - GitPython
  - PyGithub (API access)

AI Integration:
  - MCP SDK (Model Context Protocol)
  - Optional: OpenAI/Anthropic embeddings for hybrid search
```

---

## 3. Graph Data Model

### 3.1 Node Types

The graph contains the following node types, each representing a distinct code element:

#### 3.1.1 File Node
```
Node Label: File
Properties:
  - id: string (unique identifier, typically file path hash)
  - path: string (relative path from repo root)
  - absolute_path: string (full filesystem path)
  - language: string (detected programming language)
  - extension: string (file extension)
  - size_bytes: integer
  - line_count: integer
  - last_modified: datetime
  - git_hash: string (last commit hash affecting this file)
  - encoding: string (file encoding)
```

#### 3.1.2 Module Node
```
Node Label: Module
Properties:
  - id: string
  - name: string (module/package name)
  - file_id: string (reference to containing file)
  - docstring: string (module-level documentation)
  - is_package: boolean
  - is_init: boolean (__init__.py or index.js)
```

#### 3.1.3 Class Node
```
Node Label: Class
Properties:
  - id: string
  - name: string
  - qualified_name: string (full dotted path)
  - file_id: string
  - start_line: integer
  - end_line: integer
  - docstring: string
  - is_abstract: boolean
  - is_dataclass: boolean (Python)
  - is_interface: boolean (TypeScript/Java)
  - decorators: string[] (list of decorator names)
  - visibility: string (public/private/protected)
  - source_code: string (full class source)
```

#### 3.1.4 Function Node
```
Node Label: Function
Properties:
  - id: string
  - name: string
  - qualified_name: string
  - file_id: string
  - class_id: string (null if module-level)
  - start_line: integer
  - end_line: integer
  - docstring: string
  - is_async: boolean
  - is_generator: boolean
  - is_static: boolean
  - is_classmethod: boolean
  - is_property: boolean
  - decorators: string[]
  - visibility: string
  - parameters: json (structured parameter info)
  - return_type: string (if annotated)
  - complexity: integer (cyclomatic complexity)
  - source_code: string
```

#### 3.1.5 Variable Node
```
Node Label: Variable
Properties:
  - id: string
  - name: string
  - qualified_name: string
  - file_id: string
  - scope_id: string (function/class/module containing this)
  - scope_type: string (function/class/module/global)
  - line: integer
  - type_annotation: string (if present)
  - is_constant: boolean
  - is_class_attribute: boolean
  - is_instance_attribute: boolean
  - initial_value: string (truncated if long)
```

#### 3.1.6 Import Node
```
Node Label: Import
Properties:
  - id: string
  - file_id: string
  - line: integer
  - module_path: string (what's being imported from)
  - imported_name: string (specific name imported)
  - alias: string (as X)
  - is_relative: boolean
  - is_wildcard: boolean
```

#### 3.1.7 Parameter Node
```
Node Label: Parameter
Properties:
  - id: string
  - name: string
  - function_id: string
  - position: integer
  - type_annotation: string
  - default_value: string
  - is_variadic: boolean (*args)
  - is_keyword_variadic: boolean (**kwargs)
  - is_keyword_only: boolean
  - is_positional_only: boolean
```

#### 3.1.8 Type Node (for typed languages)
```
Node Label: Type
Properties:
  - id: string
  - name: string
  - kind: string (interface/type_alias/enum/generic)
  - file_id: string
  - start_line: integer
  - end_line: integer
  - source_code: string
```

### 3.2 Edge Types (Relationships)

#### 3.2.1 Structural Relationships

```
CONTAINS
  - File -[CONTAINS]-> Module
  - File -[CONTAINS]-> Class
  - File -[CONTAINS]-> Function (module-level)
  - Module -[CONTAINS]-> Class
  - Module -[CONTAINS]-> Function
  - Class -[CONTAINS]-> Function (methods)
  - Class -[CONTAINS]-> Variable (attributes)
  - Function -[CONTAINS]-> Variable (local variables)

DEFINED_IN
  - Class -[DEFINED_IN]-> File
  - Function -[DEFINED_IN]-> File
  - Variable -[DEFINED_IN]-> File
  - Type -[DEFINED_IN]-> File
```

#### 3.2.2 Dependency Relationships

```
IMPORTS
  - File -[IMPORTS]-> File
  Properties:
    - line: integer
    - names: string[] (specific imports)

IMPORTS_FROM
  - File -[IMPORTS_FROM]-> Module
  Properties:
    - names: string[]
    - is_relative: boolean
```

#### 3.2.3 Inheritance & Implementation

```
INHERITS_FROM
  - Class -[INHERITS_FROM]-> Class
  Properties:
    - order: integer (for multiple inheritance)

IMPLEMENTS
  - Class -[IMPLEMENTS]-> Type (interface)
```

#### 3.2.4 Call & Usage Relationships

```
CALLS
  - Function -[CALLS]-> Function
  Properties:
    - line: integer
    - call_count: integer (within the function)
    - is_conditional: boolean
    - is_in_loop: boolean

INSTANTIATES
  - Function -[INSTANTIATES]-> Class
  Properties:
    - line: integer

USES
  - Function -[USES]-> Variable
  - Function -[USES]-> Class
  Properties:
    - line: integer
    - usage_type: string (read/write/both)

REFERENCES
  - Function -[REFERENCES]-> Function (without calling, e.g., passing as callback)
```

#### 3.2.5 Type Relationships

```
HAS_TYPE
  - Variable -[HAS_TYPE]-> Type
  - Parameter -[HAS_TYPE]-> Type
  - Function -[HAS_TYPE]-> Type (return type)

TYPE_PARAMETER
  - Type -[TYPE_PARAMETER]-> Type (generics)
```

#### 3.2.6 Parameter Relationships

```
HAS_PARAMETER
  - Function -[HAS_PARAMETER]-> Parameter
  Properties:
    - position: integer

ACCEPTS
  - Function -[ACCEPTS]-> Type (parameter types)
  Properties:
    - parameter_name: string
    - position: integer

RETURNS
  - Function -[RETURNS]-> Type
```

### 3.3 Graph Schema Visualization

```
                                    ┌──────────┐
                                    │   File   │
                                    └────┬─────┘
                                         │ CONTAINS
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
              ┌──────────┐        ┌──────────┐        ┌──────────┐
              │  Module  │        │   Class  │        │ Function │
              └────┬─────┘        └────┬─────┘        └────┬─────┘
                   │                   │ INHERITS_FROM     │
                   │                   ▼                   │ CALLS
                   │              ┌──────────┐             ▼
                   │              │   Class  │        ┌──────────┐
                   │              └────┬─────┘        │ Function │
                   │                   │ CONTAINS     └──────────┘
                   │                   ▼
                   │              ┌──────────┐
                   │              │  Method  │
                   │              └────┬─────┘
                   │                   │ HAS_PARAMETER
                   │                   ▼
                   │              ┌──────────┐
                   └──────────────│Parameter │
                                  └──────────┘
```

---

## 4. Parser Engine

### 4.1 Tree-sitter Integration

Tree-sitter provides incremental parsing with consistent AST structure across languages. We'll create a unified abstraction layer.

#### 4.1.1 Supported Languages (Phase 1)

| Language | Tree-sitter Grammar | Priority |
|----------|---------------------|----------|
| Python | tree-sitter-python | High |
| JavaScript | tree-sitter-javascript | High |
| TypeScript | tree-sitter-typescript | High |
| Go | tree-sitter-go | Medium |
| Rust | tree-sitter-rust | Medium |
| Java | tree-sitter-java | Medium |
| C/C++ | tree-sitter-c, tree-sitter-cpp | Low |

#### 4.1.2 Parser Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import tree_sitter

@dataclass
class ParsedEntity:
    """Base class for all parsed code entities."""
    id: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    start_col: int
    end_col: int
    source_code: str
    docstring: Optional[str] = None

@dataclass
class ParsedFunction(ParsedEntity):
    parameters: List['ParsedParameter']
    return_type: Optional[str]
    is_async: bool
    is_generator: bool
    is_static: bool
    is_classmethod: bool
    is_property: bool
    decorators: List[str]
    visibility: str
    complexity: int
    calls: List[str]  # function names called
    uses: List[str]   # variables/classes used

@dataclass
class ParsedClass(ParsedEntity):
    bases: List[str]           # parent classes
    interfaces: List[str]      # implemented interfaces
    decorators: List[str]
    is_abstract: bool
    is_dataclass: bool
    methods: List[ParsedFunction]
    attributes: List['ParsedVariable']
    visibility: str

@dataclass
class ParsedVariable(ParsedEntity):
    type_annotation: Optional[str]
    is_constant: bool
    scope_type: str  # module/class/function
    initial_value: Optional[str]

@dataclass
class ParsedParameter:
    name: str
    position: int
    type_annotation: Optional[str]
    default_value: Optional[str]
    is_variadic: bool
    is_keyword_variadic: bool

@dataclass
class ParsedImport:
    line: int
    module_path: str
    imported_names: List[str]
    aliases: Dict[str, str]
    is_relative: bool
    is_wildcard: bool

@dataclass
class ParsedFile:
    """Complete parse result for a file."""
    path: str
    language: str
    imports: List[ParsedImport]
    classes: List[ParsedClass]
    functions: List[ParsedFunction]
    variables: List[ParsedVariable]
    module_docstring: Optional[str]


class LanguageParser(ABC):
    """Abstract base class for language-specific parsers."""
    
    @property
    @abstractmethod
    def language_name(self) -> str:
        pass
    
    @property
    @abstractmethod
    def file_extensions(self) -> List[str]:
        pass
    
    @abstractmethod
    def parse_file(self, file_path: str, content: str) -> ParsedFile:
        """Parse a file and extract all entities."""
        pass
    
    @abstractmethod
    def extract_calls(self, node: tree_sitter.Node) -> List[str]:
        """Extract function calls from a function body."""
        pass
    
    @abstractmethod
    def extract_uses(self, node: tree_sitter.Node) -> List[str]:
        """Extract variable/class usages from a function body."""
        pass


class ParserRegistry:
    """Registry of language parsers."""
    
    def __init__(self):
        self._parsers: Dict[str, LanguageParser] = {}
        self._extension_map: Dict[str, str] = {}
    
    def register(self, parser: LanguageParser):
        self._parsers[parser.language_name] = parser
        for ext in parser.file_extensions:
            self._extension_map[ext] = parser.language_name
    
    def get_parser(self, file_path: str) -> Optional[LanguageParser]:
        ext = file_path.split('.')[-1] if '.' in file_path else ''
        language = self._extension_map.get(ext)
        return self._parsers.get(language) if language else None
    
    def parse_file(self, file_path: str, content: str) -> Optional[ParsedFile]:
        parser = self.get_parser(file_path)
        if parser:
            return parser.parse_file(file_path, content)
        return None
```

#### 4.1.3 Python Parser Implementation (Reference)

```python
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

class PythonParser(LanguageParser):
    
    def __init__(self):
        self._parser = Parser(Language(tspython.language()))
    
    @property
    def language_name(self) -> str:
        return "python"
    
    @property
    def file_extensions(self) -> List[str]:
        return ["py", "pyi"]
    
    def parse_file(self, file_path: str, content: str) -> ParsedFile:
        tree = self._parser.parse(content.encode())
        root = tree.root_node
        
        return ParsedFile(
            path=file_path,
            language="python",
            imports=self._extract_imports(root, content),
            classes=self._extract_classes(root, content, file_path),
            functions=self._extract_functions(root, content, file_path),
            variables=self._extract_module_variables(root, content, file_path),
            module_docstring=self._extract_module_docstring(root, content)
        )
    
    def _extract_imports(self, root: tree_sitter.Node, content: str) -> List[ParsedImport]:
        imports = []
        for node in self._find_nodes(root, ['import_statement', 'import_from_statement']):
            if node.type == 'import_statement':
                # import foo, bar
                imports.append(self._parse_import_statement(node, content))
            else:
                # from foo import bar
                imports.append(self._parse_import_from_statement(node, content))
        return imports
    
    def _extract_classes(self, root: tree_sitter.Node, content: str, file_path: str) -> List[ParsedClass]:
        classes = []
        for node in self._find_nodes(root, ['class_definition']):
            classes.append(self._parse_class(node, content, file_path))
        return classes
    
    def _extract_functions(self, root: tree_sitter.Node, content: str, file_path: str) -> List[ParsedFunction]:
        """Extract module-level functions only."""
        functions = []
        for node in root.children:
            if node.type == 'function_definition':
                functions.append(self._parse_function(node, content, file_path))
            elif node.type == 'decorated_definition':
                inner = self._get_decorated_definition(node)
                if inner and inner.type == 'function_definition':
                    functions.append(self._parse_function(node, content, file_path))
        return functions
    
    def _parse_function(self, node: tree_sitter.Node, content: str, file_path: str) -> ParsedFunction:
        # Handle decorated functions
        decorators = []
        func_node = node
        if node.type == 'decorated_definition':
            decorators = self._extract_decorators(node, content)
            func_node = self._get_decorated_definition(node)
        
        name = self._get_child_text(func_node, 'name', content)
        params_node = self._find_child(func_node, 'parameters')
        body_node = self._find_child(func_node, 'block')
        return_annotation = self._find_child(func_node, 'type')
        
        return ParsedFunction(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,  # Will be updated for methods
            file_path=file_path,
            start_line=func_node.start_point[0] + 1,
            end_line=func_node.end_point[0] + 1,
            start_col=func_node.start_point[1],
            end_col=func_node.end_point[1],
            source_code=self._get_node_text(node, content),
            docstring=self._extract_docstring(body_node, content) if body_node else None,
            parameters=self._parse_parameters(params_node, content) if params_node else [],
            return_type=self._get_node_text(return_annotation, content) if return_annotation else None,
            is_async='async' in self._get_node_text(func_node, content).split('def')[0],
            is_generator=self._is_generator(body_node, content) if body_node else False,
            is_static='staticmethod' in decorators,
            is_classmethod='classmethod' in decorators,
            is_property='property' in decorators,
            decorators=decorators,
            visibility=self._determine_visibility(name),
            complexity=self._calculate_complexity(body_node) if body_node else 1,
            calls=self.extract_calls(body_node) if body_node else [],
            uses=self.extract_uses(body_node) if body_node else []
        )
    
    def extract_calls(self, node: tree_sitter.Node) -> List[str]:
        """Extract all function/method calls from a node."""
        calls = []
        for call_node in self._find_nodes(node, ['call']):
            func_part = self._find_child(call_node, 'function') or call_node.children[0]
            call_name = self._get_call_name(func_part)
            if call_name:
                calls.append(call_name)
        return list(set(calls))
    
    def extract_uses(self, node: tree_sitter.Node) -> List[str]:
        """Extract variable and class references."""
        uses = []
        for id_node in self._find_nodes(node, ['identifier']):
            # Filter out function names, imports, etc.
            parent = id_node.parent
            if parent and parent.type not in ['function_definition', 'class_definition', 
                                               'import_statement', 'import_from_statement']:
                uses.append(id_node.text.decode())
        return list(set(uses))
    
    # ... additional helper methods ...
```

### 4.2 Incremental Parsing Strategy

For large repositories, we need incremental updates rather than full re-parsing:

```python
@dataclass
class FileChange:
    path: str
    change_type: str  # 'added', 'modified', 'deleted'
    old_hash: Optional[str]
    new_hash: Optional[str]

class IncrementalParser:
    """Handles incremental parsing based on git changes."""
    
    def __init__(self, repo_path: str, graph_client: 'GraphClient'):
        self.repo_path = repo_path
        self.graph = graph_client
        self.parser_registry = ParserRegistry()
        self._file_hashes: Dict[str, str] = {}
    
    def detect_changes(self, from_commit: str, to_commit: str = 'HEAD') -> List[FileChange]:
        """Detect file changes between commits."""
        repo = git.Repo(self.repo_path)
        diff = repo.commit(from_commit).diff(to_commit)
        
        changes = []
        for d in diff:
            if d.new_file:
                changes.append(FileChange(d.b_path, 'added', None, d.b_blob.hexsha))
            elif d.deleted_file:
                changes.append(FileChange(d.a_path, 'deleted', d.a_blob.hexsha, None))
            else:
                changes.append(FileChange(d.b_path, 'modified', d.a_blob.hexsha, d.b_blob.hexsha))
        
        return changes
    
    def apply_changes(self, changes: List[FileChange]):
        """Apply incremental changes to the graph."""
        for change in changes:
            if change.change_type == 'deleted':
                self._delete_file_nodes(change.path)
            elif change.change_type == 'added':
                self._add_file_nodes(change.path)
            else:  # modified
                self._delete_file_nodes(change.path)
                self._add_file_nodes(change.path)
    
    def _delete_file_nodes(self, file_path: str):
        """Remove all nodes associated with a file."""
        self.graph.execute("""
            MATCH (n)
            WHERE n.file_path = $file_path OR n.file_id = $file_path
            DETACH DELETE n
        """, {'file_path': file_path})
    
    def _add_file_nodes(self, file_path: str):
        """Parse and add nodes for a file."""
        full_path = os.path.join(self.repo_path, file_path)
        if not os.path.exists(full_path):
            return
        
        with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        parsed = self.parser_registry.parse_file(file_path, content)
        if parsed:
            self._insert_parsed_file(parsed)
```

---

## 5. Graph Builder

### 5.1 Graph Client Interface

```python
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from falkordb import FalkorDB

@dataclass
class QueryResult:
    records: List[Dict[str, Any]]
    summary: Dict[str, Any]

class GraphClient:
    """FalkorDB graph client with query builders."""
    
    def __init__(self, host: str = 'localhost', port: int = 6379, graph_name: str = 'gristle'):
        self.db = FalkorDB(host=host, port=port)
        self.graph = self.db.select_graph(graph_name)
        self._ensure_indexes()
    
    def _ensure_indexes(self):
        """Create indexes for efficient queries."""
        indexes = [
            ("File", "path"),
            ("File", "id"),
            ("Class", "name"),
            ("Class", "qualified_name"),
            ("Function", "name"),
            ("Function", "qualified_name"),
            ("Variable", "name"),
            ("Import", "module_path"),
        ]
        for label, prop in indexes:
            try:
                self.graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            except:
                pass  # Index may already exist
    
    def execute(self, query: str, params: Dict[str, Any] = None) -> QueryResult:
        """Execute a Cypher query."""
        result = self.graph.query(query, params or {})
        return QueryResult(
            records=[dict(zip(result.header, row)) for row in result.result_set],
            summary={
                'nodes_created': result.nodes_created,
                'relationships_created': result.relationships_created,
                'nodes_deleted': result.nodes_deleted,
                'relationships_deleted': result.relationships_deleted,
            }
        )
    
    def create_node(self, label: str, properties: Dict[str, Any]) -> str:
        """Create a node and return its ID."""
        props_str = ', '.join(f'{k}: ${k}' for k in properties.keys())
        query = f"CREATE (n:{label} {{{props_str}}}) RETURN n.id"
        result = self.execute(query, properties)
        return result.records[0]['n.id'] if result.records else None
    
    def create_relationship(self, from_id: str, to_id: str, rel_type: str, 
                           properties: Dict[str, Any] = None):
        """Create a relationship between nodes."""
        props_str = ''
        if properties:
            props_str = ' {' + ', '.join(f'{k}: ${k}' for k in properties.keys()) + '}'
        
        query = f"""
            MATCH (a), (b)
            WHERE a.id = $from_id AND b.id = $to_id
            CREATE (a)-[r:{rel_type}{props_str}]->(b)
        """
        params = {'from_id': from_id, 'to_id': to_id}
        if properties:
            params.update(properties)
        self.execute(query, params)
    
    def clear(self):
        """Clear all nodes and relationships."""
        self.execute("MATCH (n) DETACH DELETE n")


class GraphBuilder:
    """Builds the code graph from parsed files."""
    
    def __init__(self, graph: GraphClient):
        self.graph = graph
        self._id_map: Dict[str, str] = {}  # qualified_name -> id
    
    def build_from_parsed_file(self, parsed: ParsedFile):
        """Build graph nodes and edges from a parsed file."""
        # Create file node
        file_id = self._create_file_node(parsed)
        
        # Create import nodes and relationships
        for imp in parsed.imports:
            self._create_import(file_id, imp)
        
        # Create class nodes
        for cls in parsed.classes:
            self._create_class(file_id, cls)
        
        # Create module-level function nodes
        for func in parsed.functions:
            self._create_function(file_id, None, func)
        
        # Create module-level variable nodes
        for var in parsed.variables:
            self._create_variable(file_id, None, var)
        
        # Second pass: create call relationships
        self._create_call_relationships(parsed)
    
    def _create_file_node(self, parsed: ParsedFile) -> str:
        file_id = f"file::{parsed.path}"
        self.graph.create_node('File', {
            'id': file_id,
            'path': parsed.path,
            'language': parsed.language,
            'docstring': parsed.module_docstring or ''
        })
        return file_id
    
    def _create_class(self, file_id: str, cls: ParsedClass):
        class_id = cls.id
        self._id_map[cls.qualified_name] = class_id
        
        self.graph.create_node('Class', {
            'id': class_id,
            'name': cls.name,
            'qualified_name': cls.qualified_name,
            'file_id': file_id,
            'start_line': cls.start_line,
            'end_line': cls.end_line,
            'docstring': cls.docstring or '',
            'is_abstract': cls.is_abstract,
            'decorators': cls.decorators,
            'visibility': cls.visibility,
            'source_code': cls.source_code
        })
        
        # DEFINED_IN relationship
        self.graph.create_relationship(class_id, file_id, 'DEFINED_IN')
        
        # File CONTAINS Class
        self.graph.create_relationship(file_id, class_id, 'CONTAINS')
        
        # Create methods
        for method in cls.methods:
            method_id = self._create_function(file_id, class_id, method)
            self.graph.create_relationship(class_id, method_id, 'CONTAINS')
        
        # Create attributes
        for attr in cls.attributes:
            attr_id = self._create_variable(file_id, class_id, attr)
            self.graph.create_relationship(class_id, attr_id, 'CONTAINS')
        
        # Inheritance relationships (resolved in second pass)
        for base in cls.bases:
            self._pending_inheritance.append((class_id, base))
    
    def _create_function(self, file_id: str, class_id: Optional[str], func: ParsedFunction) -> str:
        func_id = func.id
        self._id_map[func.qualified_name] = func_id
        
        self.graph.create_node('Function', {
            'id': func_id,
            'name': func.name,
            'qualified_name': func.qualified_name,
            'file_id': file_id,
            'class_id': class_id or '',
            'start_line': func.start_line,
            'end_line': func.end_line,
            'docstring': func.docstring or '',
            'is_async': func.is_async,
            'is_generator': func.is_generator,
            'is_static': func.is_static,
            'is_classmethod': func.is_classmethod,
            'is_property': func.is_property,
            'decorators': func.decorators,
            'visibility': func.visibility,
            'return_type': func.return_type or '',
            'complexity': func.complexity,
            'source_code': func.source_code
        })
        
        self.graph.create_relationship(func_id, file_id, 'DEFINED_IN')
        
        if not class_id:
            self.graph.create_relationship(file_id, func_id, 'CONTAINS')
        
        # Create parameter nodes
        for param in func.parameters:
            self._create_parameter(func_id, param)
        
        # Store calls for second pass
        for call in func.calls:
            self._pending_calls.append((func_id, call))
        
        return func_id
    
    def _create_call_relationships(self, parsed: ParsedFile):
        """Create CALLS relationships after all nodes exist."""
        for caller_id, callee_name in self._pending_calls:
            # Try to resolve callee
            callee_id = self._id_map.get(callee_name)
            if callee_id:
                self.graph.create_relationship(caller_id, callee_id, 'CALLS')
            else:
                # Create external reference node if needed
                pass
```

---

## 6. Query Engine

### 6.1 Pre-built Query Templates

These are the high-value queries that AI agents will use most frequently:

```python
class GristleQueries:
    """Pre-built query templates for common code analysis tasks."""
    
    @staticmethod
    def get_function_callers(function_name: str, max_depth: int = 2) -> str:
        """Find all functions that call a given function."""
        return f"""
            MATCH (caller:Function)-[:CALLS*1..{max_depth}]->(target:Function)
            WHERE target.name = $function_name OR target.qualified_name = $function_name
            RETURN DISTINCT caller.qualified_name AS caller,
                   caller.file_id AS file,
                   caller.start_line AS line,
                   length(path) AS depth
            ORDER BY depth, caller
        """
    
    @staticmethod
    def get_function_callees(function_name: str, max_depth: int = 2) -> str:
        """Find all functions called by a given function."""
        return f"""
            MATCH (source:Function)-[:CALLS*1..{max_depth}]->(callee:Function)
            WHERE source.name = $function_name OR source.qualified_name = $function_name
            RETURN DISTINCT callee.qualified_name AS callee,
                   callee.file_id AS file,
                   callee.start_line AS line,
                   length(path) AS depth
            ORDER BY depth, callee
        """
    
    @staticmethod
    def get_class_hierarchy(class_name: str) -> str:
        """Get the full inheritance hierarchy for a class."""
        return """
            MATCH path = (c:Class)-[:INHERITS_FROM*0..10]->(ancestor:Class)
            WHERE c.name = $class_name OR c.qualified_name = $class_name
            RETURN [node in nodes(path) | node.qualified_name] AS hierarchy,
                   length(path) AS depth
            ORDER BY depth DESC
            LIMIT 1
        """
    
    @staticmethod
    def get_class_descendants(class_name: str) -> str:
        """Find all classes that inherit from a given class."""
        return """
            MATCH (descendant:Class)-[:INHERITS_FROM*1..10]->(c:Class)
            WHERE c.name = $class_name OR c.qualified_name = $class_name
            RETURN descendant.qualified_name AS descendant,
                   descendant.file_id AS file
        """
    
    @staticmethod
    def get_file_dependencies(file_path: str) -> str:
        """Get all files that a given file depends on."""
        return """
            MATCH (f:File)-[:IMPORTS]->(dep:File)
            WHERE f.path = $file_path
            RETURN dep.path AS dependency,
                   dep.language AS language
        """
    
    @staticmethod
    def get_file_dependents(file_path: str) -> str:
        """Get all files that depend on a given file."""
        return """
            MATCH (dependent:File)-[:IMPORTS]->(f:File)
            WHERE f.path = $file_path
            RETURN dependent.path AS dependent,
                   dependent.language AS language
        """
    
    @staticmethod
    def impact_analysis(entity_name: str, entity_type: str = 'Function') -> str:
        """Analyze the impact of changing an entity."""
        return f"""
            MATCH (target:{entity_type})
            WHERE target.name = $entity_name OR target.qualified_name = $entity_name
            
            // Find all callers/users
            OPTIONAL MATCH (caller:Function)-[:CALLS]->(target)
            
            // Find all files affected
            OPTIONAL MATCH (target)-[:DEFINED_IN]->(file:File)
            OPTIONAL MATCH (caller)-[:DEFINED_IN]->(caller_file:File)
            
            RETURN target.qualified_name AS target,
                   file.path AS target_file,
                   collect(DISTINCT caller.qualified_name) AS direct_callers,
                   collect(DISTINCT caller_file.path) AS affected_files
        """
    
    @staticmethod
    def find_data_flow(source_var: str, sink_pattern: str) -> str:
        """Trace data flow from a source variable to functions matching a pattern."""
        return """
            MATCH path = (v:Variable)-[:USED_BY*1..10]->(sink:Function)
            WHERE v.name = $source_var
              AND sink.name =~ $sink_pattern
            RETURN [node in nodes(path) | 
                    CASE 
                        WHEN node:Variable THEN 'var:' + node.name
                        WHEN node:Function THEN 'func:' + node.name
                    END
                   ] AS flow_path,
                   length(path) AS hops
            ORDER BY hops
            LIMIT 10
        """
    
    @staticmethod
    def get_module_structure(file_path: str) -> str:
        """Get the complete structure of a file/module."""
        return """
            MATCH (f:File {path: $file_path})
            OPTIONAL MATCH (f)-[:CONTAINS]->(c:Class)
            OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
            OPTIONAL MATCH (f)-[:CONTAINS]->(func:Function)
            WHERE func.class_id = ''
            
            RETURN f.path AS file,
                   collect(DISTINCT {
                       name: c.name,
                       type: 'class',
                       methods: collect(m.name)
                   }) AS classes,
                   collect(DISTINCT func.name) AS functions
        """
    
    @staticmethod
    def search_by_docstring(search_term: str) -> str:
        """Search for entities by docstring content."""
        return """
            MATCH (n)
            WHERE n.docstring CONTAINS $search_term
            RETURN labels(n)[0] AS type,
                   n.qualified_name AS name,
                   n.file_id AS file,
                   n.docstring AS docstring
            LIMIT 20
        """
    
    @staticmethod
    def get_function_context(function_name: str, context_depth: int = 1) -> str:
        """Get a function with its immediate context (callers, callees, class)."""
        return f"""
            MATCH (f:Function)
            WHERE f.name = $function_name OR f.qualified_name = $function_name
            
            // Get containing class
            OPTIONAL MATCH (c:Class)-[:CONTAINS]->(f)
            
            // Get callers (depth 1)
            OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
            
            // Get callees (depth 1)
            OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
            
            // Get file
            MATCH (f)-[:DEFINED_IN]->(file:File)
            
            RETURN f.qualified_name AS function,
                   f.source_code AS source,
                   f.docstring AS docstring,
                   f.start_line AS line,
                   file.path AS file,
                   c.name AS containing_class,
                   collect(DISTINCT caller.qualified_name) AS callers,
                   collect(DISTINCT callee.qualified_name) AS callees
        """
    
    @staticmethod
    def get_architectural_component(entry_point: str, max_depth: int = 3) -> str:
        """Extract a coherent architectural component starting from an entry point."""
        return f"""
            MATCH path = (entry:Function)-[:CALLS*0..{max_depth}]->(connected:Function)
            WHERE entry.qualified_name = $entry_point
            
            WITH collect(DISTINCT connected) AS funcs
            UNWIND funcs AS f
            
            MATCH (f)-[:DEFINED_IN]->(file:File)
            OPTIONAL MATCH (c:Class)-[:CONTAINS]->(f)
            
            RETURN f.qualified_name AS function,
                   f.source_code AS source,
                   file.path AS file,
                   c.name AS class_name
        """
```

### 6.2 Query Execution Service

```python
from typing import Union, List, Dict, Any
from enum import Enum

class QueryType(Enum):
    CALLERS = "callers"
    CALLEES = "callees"
    HIERARCHY = "hierarchy"
    DESCENDANTS = "descendants"
    DEPENDENCIES = "dependencies"
    DEPENDENTS = "dependents"
    IMPACT = "impact"
    DATA_FLOW = "data_flow"
    STRUCTURE = "structure"
    SEARCH = "search"
    CONTEXT = "context"
    COMPONENT = "component"

class QueryService:
    """Service for executing code graph queries."""
    
    def __init__(self, graph: GraphClient):
        self.graph = graph
        self.queries = GristleQueries()
    
    def execute_query(self, query_type: QueryType, **params) -> List[Dict[str, Any]]:
        """Execute a pre-built query with parameters."""
        query_map = {
            QueryType.CALLERS: (self.queries.get_function_callers, ['function_name']),
            QueryType.CALLEES: (self.queries.get_function_callees, ['function_name']),
            QueryType.HIERARCHY: (self.queries.get_class_hierarchy, ['class_name']),
            QueryType.DESCENDANTS: (self.queries.get_class_descendants, ['class_name']),
            QueryType.DEPENDENCIES: (self.queries.get_file_dependencies, ['file_path']),
            QueryType.DEPENDENTS: (self.queries.get_file_dependents, ['file_path']),
            QueryType.IMPACT: (self.queries.impact_analysis, ['entity_name']),
            QueryType.STRUCTURE: (self.queries.get_module_structure, ['file_path']),
            QueryType.SEARCH: (self.queries.search_by_docstring, ['search_term']),
            QueryType.CONTEXT: (self.queries.get_function_context, ['function_name']),
            QueryType.COMPONENT: (self.queries.get_architectural_component, ['entry_point']),
        }
        
        query_fn, required_params = query_map[query_type]
        
        # Validate required params
        for p in required_params:
            if p not in params:
                raise ValueError(f"Missing required parameter: {p}")
        
        # Build and execute query
        query = query_fn(**{k: v for k, v in params.items() if k in required_params})
        result = self.graph.execute(query, params)
        
        return result.records
    
    def raw_query(self, cypher: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Execute a raw Cypher query."""
        result = self.graph.execute(cypher, params or {})
        return result.records
```

---

## 7. API Layer

### 7.1 FastAPI Application

```python
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from enum import Enum

app = FastAPI(
    title="Gristle API",
    description="Graph-based code analysis and navigation API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
graph_client = GraphClient()
query_service = QueryService(graph_client)
parser_registry = ParserRegistry()

# ============ Models ============

class RepoIngestionRequest(BaseModel):
    repo_url: str = Field(..., description="GitHub repository URL")
    branch: str = Field(default="main", description="Branch to analyze")
    include_patterns: List[str] = Field(default=["*"], description="File patterns to include")
    exclude_patterns: List[str] = Field(default=["node_modules/*", "*.min.js", "dist/*"], 
                                        description="File patterns to exclude")

class QueryRequest(BaseModel):
    query_type: str = Field(..., description="Type of query to execute")
    params: Dict[str, Any] = Field(default={}, description="Query parameters")

class RawQueryRequest(BaseModel):
    cypher: str = Field(..., description="Cypher query to execute")
    params: Dict[str, Any] = Field(default={}, description="Query parameters")

class FunctionContextRequest(BaseModel):
    function_name: str
    include_source: bool = True
    caller_depth: int = 1
    callee_depth: int = 1

class ImpactAnalysisRequest(BaseModel):
    entity_name: str
    entity_type: str = "Function"
    max_depth: int = 3

# ============ Endpoints ============

@app.post("/repos/ingest", tags=["Repository"])
async def ingest_repository(request: RepoIngestionRequest):
    """Ingest a GitHub repository into the code graph."""
    try:
        # Clone repo
        repo_path = clone_repository(request.repo_url, request.branch)
        
        # Parse all files
        parsed_files = []
        for file_path in walk_repository(repo_path, request.include_patterns, request.exclude_patterns):
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            
            relative_path = os.path.relpath(file_path, repo_path)
            parsed = parser_registry.parse_file(relative_path, content)
            if parsed:
                parsed_files.append(parsed)
        
        # Build graph
        builder = GraphBuilder(graph_client)
        for parsed in parsed_files:
            builder.build_from_parsed_file(parsed)
        
        return {
            "status": "success",
            "files_processed": len(parsed_files),
            "repo": request.repo_url
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/repos/update", tags=["Repository"])
async def update_repository(repo_id: str, from_commit: str, to_commit: str = "HEAD"):
    """Incrementally update the graph based on git changes."""
    incremental_parser = IncrementalParser(repo_id, graph_client)
    changes = incremental_parser.detect_changes(from_commit, to_commit)
    incremental_parser.apply_changes(changes)
    
    return {
        "status": "success",
        "changes_applied": len(changes),
        "details": [{"path": c.path, "type": c.change_type} for c in changes]
    }

@app.get("/functions/{function_name}/callers", tags=["Analysis"])
async def get_function_callers(
    function_name: str,
    max_depth: int = Query(default=2, ge=1, le=10)
):
    """Get all functions that call a given function."""
    results = query_service.execute_query(
        QueryType.CALLERS,
        function_name=function_name,
        max_depth=max_depth
    )
    return {"function": function_name, "callers": results}

@app.get("/functions/{function_name}/callees", tags=["Analysis"])
async def get_function_callees(
    function_name: str,
    max_depth: int = Query(default=2, ge=1, le=10)
):
    """Get all functions called by a given function."""
    results = query_service.execute_query(
        QueryType.CALLEES,
        function_name=function_name,
        max_depth=max_depth
    )
    return {"function": function_name, "callees": results}

@app.get("/functions/{function_name}/context", tags=["Analysis"])
async def get_function_context(function_name: str):
    """Get a function with its immediate context."""
    results = query_service.execute_query(
        QueryType.CONTEXT,
        function_name=function_name
    )
    if not results:
        raise HTTPException(status_code=404, detail=f"Function '{function_name}' not found")
    return results[0]

@app.get("/classes/{class_name}/hierarchy", tags=["Analysis"])
async def get_class_hierarchy(class_name: str):
    """Get the inheritance hierarchy for a class."""
    results = query_service.execute_query(
        QueryType.HIERARCHY,
        class_name=class_name
    )
    return {"class": class_name, "hierarchy": results}

@app.get("/classes/{class_name}/descendants", tags=["Analysis"])
async def get_class_descendants(class_name: str):
    """Get all classes that inherit from a given class."""
    results = query_service.execute_query(
        QueryType.DESCENDANTS,
        class_name=class_name
    )
    return {"class": class_name, "descendants": results}

@app.get("/files/{file_path:path}/structure", tags=["Analysis"])
async def get_file_structure(file_path: str):
    """Get the complete structure of a file."""
    results = query_service.execute_query(
        QueryType.STRUCTURE,
        file_path=file_path
    )
    if not results:
        raise HTTPException(status_code=404, detail=f"File '{file_path}' not found")
    return results[0]

@app.get("/files/{file_path:path}/dependencies", tags=["Analysis"])
async def get_file_dependencies(file_path: str):
    """Get all files that a given file depends on."""
    results = query_service.execute_query(
        QueryType.DEPENDENCIES,
        file_path=file_path
    )
    return {"file": file_path, "dependencies": results}

@app.get("/files/{file_path:path}/dependents", tags=["Analysis"])
async def get_file_dependents(file_path: str):
    """Get all files that depend on a given file."""
    results = query_service.execute_query(
        QueryType.DEPENDENTS,
        file_path=file_path
    )
    return {"file": file_path, "dependents": results}

@app.post("/analysis/impact", tags=["Analysis"])
async def analyze_impact(request: ImpactAnalysisRequest):
    """Analyze the impact of changing an entity."""
    results = query_service.execute_query(
        QueryType.IMPACT,
        entity_name=request.entity_name,
        entity_type=request.entity_type
    )
    return {"entity": request.entity_name, "impact": results}

@app.post("/analysis/component", tags=["Analysis"])
async def extract_component(entry_point: str, max_depth: int = 3):
    """Extract a coherent architectural component."""
    results = query_service.execute_query(
        QueryType.COMPONENT,
        entry_point=entry_point,
        max_depth=max_depth
    )
    return {"entry_point": entry_point, "component": results}

@app.get("/search", tags=["Search"])
async def search_code(
    q: str = Query(..., description="Search term"),
    search_type: str = Query(default="docstring", enum=["docstring", "name", "all"])
):
    """Search for code entities."""
    if search_type == "docstring":
        results = query_service.execute_query(QueryType.SEARCH, search_term=q)
    else:
        # Name search
        results = query_service.raw_query("""
            MATCH (n)
            WHERE n.name CONTAINS $term OR n.qualified_name CONTAINS $term
            RETURN labels(n)[0] AS type,
                   n.qualified_name AS name,
                   n.file_id AS file
            LIMIT 20
        """, {"term": q})
    
    return {"query": q, "results": results}

@app.post("/query/raw", tags=["Advanced"])
async def execute_raw_query(request: RawQueryRequest):
    """Execute a raw Cypher query (advanced users)."""
    try:
        results = query_service.raw_query(request.cypher, request.params)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Query error: {str(e)}")

@app.get("/stats", tags=["Info"])
async def get_graph_stats():
    """Get statistics about the code graph."""
    stats = query_service.raw_query("""
        MATCH (n)
        RETURN labels(n)[0] AS type, count(*) AS count
    """)
    
    rel_stats = query_service.raw_query("""
        MATCH ()-[r]->()
        RETURN type(r) AS type, count(*) AS count
    """)
    
    return {
        "nodes": {s['type']: s['count'] for s in stats},
        "relationships": {s['type']: s['count'] for s in rel_stats}
    }

@app.delete("/graph", tags=["Admin"])
async def clear_graph():
    """Clear the entire code graph."""
    graph_client.clear()
    return {"status": "success", "message": "Graph cleared"}
```

---

## 8. AI Agent Integration

### 8.1 MCP Server Implementation

```python
from mcp.server import Server
from mcp.types import Tool, TextContent
import json

# Initialize MCP server
server = Server("gristle")

# Tool definitions for AI agents

@server.tool()
async def get_function_context(
    function_name: str,
    include_callers: bool = True,
    include_callees: bool = True,
    caller_depth: int = 1,
    callee_depth: int = 1
) -> str:
    """
    Get comprehensive context for a function including its source code,
    documentation, callers, and callees.
    
    Use this tool when you need to understand what a function does,
    how it's used, and what it depends on.
    
    Args:
        function_name: Name or qualified name of the function
        include_callers: Whether to include functions that call this function
        include_callees: Whether to include functions this function calls
        caller_depth: How many levels of callers to retrieve (1-3)
        callee_depth: How many levels of callees to retrieve (1-3)
    
    Returns:
        JSON with function source, docstring, callers, and callees
    """
    result = query_service.execute_query(
        QueryType.CONTEXT,
        function_name=function_name
    )
    
    if not result:
        return json.dumps({"error": f"Function '{function_name}' not found"})
    
    context = result[0]
    
    if include_callers and caller_depth > 1:
        callers = query_service.execute_query(
            QueryType.CALLERS,
            function_name=function_name,
            max_depth=caller_depth
        )
        context['callers_extended'] = callers
    
    if include_callees and callee_depth > 1:
        callees = query_service.execute_query(
            QueryType.CALLEES,
            function_name=function_name,
            max_depth=callee_depth
        )
        context['callees_extended'] = callees
    
    return json.dumps(context, indent=2)


@server.tool()
async def analyze_change_impact(
    entity_name: str,
    entity_type: str = "Function"
) -> str:
    """
    Analyze what would be affected if you modify a code entity.
    
    Use this tool BEFORE making changes to understand the blast radius.
    This helps prevent breaking changes and identifies what tests to run.
    
    Args:
        entity_name: Name of the function, class, or variable
        entity_type: Type of entity (Function, Class, Variable)
    
    Returns:
        JSON with direct callers, affected files, and dependency chain
    """
    result = query_service.execute_query(
        QueryType.IMPACT,
        entity_name=entity_name,
        entity_type=entity_type
    )
    
    return json.dumps(result, indent=2)


@server.tool()
async def get_class_structure(
    class_name: str,
    include_hierarchy: bool = True,
    include_methods: bool = True
) -> str:
    """
    Get the complete structure of a class including its hierarchy,
    methods, and attributes.
    
    Use this when you need to understand a class's interface,
    its parent classes, or what methods are available.
    
    Args:
        class_name: Name or qualified name of the class
        include_hierarchy: Include inheritance chain
        include_methods: Include method signatures
    
    Returns:
        JSON with class structure, hierarchy, and methods
    """
    # Get class node
    class_info = query_service.raw_query("""
        MATCH (c:Class)
        WHERE c.name = $name OR c.qualified_name = $name
        OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
        OPTIONAL MATCH (c)-[:CONTAINS]->(a:Variable)
        RETURN c.qualified_name AS name,
               c.docstring AS docstring,
               c.source_code AS source,
               c.file_id AS file,
               collect(DISTINCT {name: m.name, params: m.parameters, returns: m.return_type}) AS methods,
               collect(DISTINCT {name: a.name, type: a.type_annotation}) AS attributes
    """, {"name": class_name})
    
    if not class_info:
        return json.dumps({"error": f"Class '{class_name}' not found"})
    
    result = class_info[0]
    
    if include_hierarchy:
        hierarchy = query_service.execute_query(
            QueryType.HIERARCHY,
            class_name=class_name
        )
        result['hierarchy'] = hierarchy
    
    return json.dumps(result, indent=2)


@server.tool()
async def find_code_path(
    from_entity: str,
    to_entity: str,
    max_hops: int = 5
) -> str:
    """
    Find how two code entities are connected through calls/usage.
    
    Use this to understand data flow, trace how a function eventually
    reaches another, or understand architectural connections.
    
    Args:
        from_entity: Starting function or class name
        to_entity: Target function or class name
        max_hops: Maximum path length to search
    
    Returns:
        JSON with paths connecting the two entities
    """
    result = query_service.raw_query(f"""
        MATCH path = shortestPath(
            (start)-[:CALLS|USES|CONTAINS*1..{max_hops}]->(end)
        )
        WHERE (start.name = $from_name OR start.qualified_name = $from_name)
          AND (end.name = $to_name OR end.qualified_name = $to_name)
        RETURN [node in nodes(path) | node.qualified_name] AS path,
               length(path) AS hops
        LIMIT 5
    """, {"from_name": from_entity, "to_name": to_entity})
    
    return json.dumps(result, indent=2)


@server.tool()
async def get_file_overview(file_path: str) -> str:
    """
    Get a complete overview of a source file including all classes,
    functions, and their relationships.
    
    Use this when you need to understand what's in a file before
    making changes or to get oriented in unfamiliar code.
    
    Args:
        file_path: Relative path to the file from repo root
    
    Returns:
        JSON with file structure, classes, functions, and imports
    """
    result = query_service.execute_query(
        QueryType.STRUCTURE,
        file_path=file_path
    )
    
    if not result:
        return json.dumps({"error": f"File '{file_path}' not found"})
    
    # Also get imports
    imports = query_service.raw_query("""
        MATCH (f:File {path: $path})-[:IMPORTS]->(dep:File)
        RETURN collect(dep.path) AS imports
    """, {"path": file_path})
    
    result[0]['imports'] = imports[0]['imports'] if imports else []
    
    return json.dumps(result[0], indent=2)


@server.tool()
async def search_codebase(
    query: str,
    search_type: str = "all",
    limit: int = 10
) -> str:
    """
    Search the codebase for functions, classes, or variables.
    
    Use this to find relevant code when you're not sure where
    something is defined or to discover related functionality.
    
    Args:
        query: Search term (name, partial name, or docstring content)
        search_type: What to search - 'name', 'docstring', or 'all'
        limit: Maximum results to return
    
    Returns:
        JSON with matching code entities
    """
    if search_type == "docstring":
        results = query_service.execute_query(QueryType.SEARCH, search_term=query)
    elif search_type == "name":
        results = query_service.raw_query("""
            MATCH (n)
            WHERE n.name CONTAINS $term OR n.qualified_name CONTAINS $term
            RETURN labels(n)[0] AS type,
                   n.qualified_name AS name,
                   n.file_id AS file,
                   n.start_line AS line
            LIMIT $limit
        """, {"term": query, "limit": limit})
    else:
        # Search both
        results = query_service.raw_query("""
            MATCH (n)
            WHERE n.name CONTAINS $term 
               OR n.qualified_name CONTAINS $term
               OR n.docstring CONTAINS $term
            RETURN labels(n)[0] AS type,
                   n.qualified_name AS name,
                   n.file_id AS file,
                   n.start_line AS line,
                   CASE WHEN n.docstring CONTAINS $term THEN true ELSE false END AS docstring_match
            LIMIT $limit
        """, {"term": query, "limit": limit})
    
    return json.dumps(results, indent=2)


@server.tool()
async def get_dependencies_tree(
    file_path: str,
    direction: str = "outgoing",
    max_depth: int = 2
) -> str:
    """
    Get the dependency tree for a file.
    
    Use 'outgoing' to see what this file depends on.
    Use 'incoming' to see what depends on this file.
    
    Args:
        file_path: Path to the file
        direction: 'outgoing' (what we import) or 'incoming' (what imports us)
        max_depth: How many levels deep to traverse
    
    Returns:
        JSON dependency tree
    """
    if direction == "outgoing":
        results = query_service.raw_query(f"""
            MATCH path = (f:File)-[:IMPORTS*1..{max_depth}]->(dep:File)
            WHERE f.path = $path
            RETURN dep.path AS dependency,
                   length(path) AS depth
            ORDER BY depth
        """, {"path": file_path})
    else:
        results = query_service.raw_query(f"""
            MATCH path = (dependent:File)-[:IMPORTS*1..{max_depth}]->(f:File)
            WHERE f.path = $path
            RETURN dependent.path AS dependent,
                   length(path) AS depth
            ORDER BY depth
        """, {"path": file_path})
    
    return json.dumps(results, indent=2)


# Run MCP server
if __name__ == "__main__":
    import asyncio
    from mcp.server.stdio import stdio_server
    
    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream)
    
    asyncio.run(main())
```

### 8.2 Tool Usage Guidelines for AI Agents

```markdown
## Gristle Tool Usage Guide for AI Agents

### When to Use Each Tool

**get_function_context**
- ALWAYS use before modifying a function
- Use when you need to understand what a function does
- Use to find the source code of a function
- Use to understand how a function fits into the codebase

**analyze_change_impact**
- ALWAYS use before making changes to understand blast radius
- Use to identify what tests need to run
- Use to find all places that might break

**get_class_structure**
- Use when working with class-based code
- Use to understand inheritance hierarchies
- Use to find available methods on a class

**find_code_path**
- Use for security analysis (how does user input reach the database?)
- Use to understand architectural connections
- Use when debugging data flow issues

**get_file_overview**
- Use when first encountering a file
- Use to understand module organization
- Use before making structural changes

**search_codebase**
- Use when you don't know where something is
- Use to find related functionality
- Use to discover existing implementations before creating new ones

**get_dependencies_tree**
- Use to understand module organization
- Use before refactoring to understand impact
- Use to identify circular dependencies

### Best Practices

1. **Always check context before changes**: Call `get_function_context` and 
   `analyze_change_impact` before modifying any code.

2. **Start broad, then narrow**: Use `search_codebase` to find relevant areas,
   then use specific tools to dive deeper.

3. **Follow the graph**: When debugging, use `find_code_path` to trace
   execution flow rather than guessing.

4. **Understand the hierarchy**: For OOP code, always check `get_class_structure`
   to understand inheritance before making changes.
```

---

## 9. Implementation Phases

### Phase 1: Core Infrastructure (Week 1-2)

**Goals:**
- Basic parsing for Python
- FalkorDB setup and graph schema
- Core node/edge creation
- Basic API endpoints

**Deliverables:**
- [ ] Project structure and dependencies
- [ ] FalkorDB Docker setup
- [ ] Python parser with tree-sitter
- [ ] Graph client implementation
- [ ] Basic node creation (File, Function, Class)
- [ ] Basic edge creation (CONTAINS, CALLS, DEFINED_IN)
- [ ] API: `/repos/ingest` endpoint
- [ ] API: `/functions/{name}/context` endpoint

### Phase 2: Query Engine (Week 3)

**Goals:**
- Complete query template library
- Traversal optimizations
- Search capabilities

**Deliverables:**
- [ ] All query templates implemented
- [ ] Graph indexes for performance
- [ ] Full-text search on docstrings
- [ ] API: All analysis endpoints
- [ ] API: Search endpoint

### Phase 3: Multi-Language Support (Week 4)

**Goals:**
- JavaScript/TypeScript parser
- Go parser
- Unified parsing abstraction

**Deliverables:**
- [ ] JavaScript parser
- [ ] TypeScript parser
- [ ] Go parser
- [ ] Language detection
- [ ] Cross-language import resolution

### Phase 4: AI Agent Integration (Week 5)

**Goals:**
- MCP server implementation
- Tool definitions
- Agent usage documentation

**Deliverables:**
- [ ] MCP server with all tools
- [ ] Tool usage guidelines
- [ ] Integration examples
- [ ] Performance testing with agents

### Phase 5: Incremental Updates & Polish (Week 6)

**Goals:**
- Git-based incremental updates
- Performance optimization
- Production readiness

**Deliverables:**
- [ ] Incremental parser
- [ ] Webhook support for auto-updates
- [ ] Query caching
- [ ] Comprehensive tests
- [ ] Documentation
- [ ] Docker compose for deployment

---

## 10. Project Structure

```
gristle/
├── README.md
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
│
├── src/
│   ├── __init__.py
│   │
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── base.py              # Abstract parser interface
│   │   ├── registry.py          # Parser registry
│   │   ├── python_parser.py     # Python implementation
│   │   ├── javascript_parser.py # JavaScript implementation
│   │   ├── typescript_parser.py # TypeScript implementation
│   │   └── go_parser.py         # Go implementation
│   │
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── client.py            # FalkorDB client
│   │   ├── builder.py           # Graph construction
│   │   ├── schema.py            # Node/edge type definitions
│   │   └── queries.py           # Query templates
│   │
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── github.py            # GitHub repo cloning
│   │   ├── walker.py            # File system walker
│   │   └── incremental.py       # Incremental updates
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── repos.py         # Repository endpoints
│   │   │   ├── analysis.py      # Analysis endpoints
│   │   │   └── search.py        # Search endpoints
│   │   └── models.py            # Pydantic models
│   │
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── server.py            # MCP server
│   │   └── tools.py             # Tool definitions
│   │
│   └── utils/
│       ├── __init__.py
│       ├── hashing.py           # ID generation
│       └── complexity.py        # Code complexity calculation
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_parsers/
│   ├── test_graph/
│   ├── test_api/
│   └── fixtures/
│       └── sample_repos/
│
├── scripts/
│   ├── setup_db.py
│   └── seed_test_data.py
│
└── docs/
    ├── api.md
    ├── queries.md
    └── agent_integration.md
```

---

## 11. Configuration

### 11.1 Environment Variables

```bash
# Database
FALKORDB_HOST=localhost
FALKORDB_PORT=6379
FALKORDB_GRAPH_NAME=gristle

# API
API_HOST=0.0.0.0
API_PORT=8000
API_DEBUG=false

# GitHub (optional, for private repos)
GITHUB_TOKEN=ghp_xxxxx

# Parsing
MAX_FILE_SIZE_MB=10
PARSE_TIMEOUT_SECONDS=30

# Performance
QUERY_CACHE_TTL=300
MAX_CONCURRENT_PARSES=4
```

### 11.2 Docker Compose

```yaml
version: '3.8'

services:
  falkordb:
    image: falkordb/falkordb:latest
    ports:
      - "6379:6379"
    volumes:
      - falkordb_data:/data
    command: --loadmodule /usr/lib/redis/modules/falkordb.so

  gristle-api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - FALKORDB_HOST=falkordb
      - FALKORDB_PORT=6379
    depends_on:
      - falkordb
    volumes:
      - ./repos:/app/repos  # For cloned repositories

volumes:
  falkordb_data:
```

---

## 12. Testing Strategy

### 12.1 Test Categories

```python
# tests/conftest.py
import pytest
from src.graph.client import GraphClient

@pytest.fixture
def graph_client():
    """Provide a test graph client with isolated graph."""
    client = GraphClient(graph_name="gristle_test")
    yield client
    client.clear()

@pytest.fixture
def sample_python_code():
    return '''
class UserService:
    """Service for user operations."""
    
    def __init__(self, db: Database):
        self.db = db
    
    def get_user(self, user_id: int) -> User:
        """Fetch a user by ID."""
        return self.db.query(User).get(user_id)
    
    def create_user(self, name: str, email: str) -> User:
        """Create a new user."""
        user = User(name=name, email=email)
        self.db.save(user)
        return user

def validate_email(email: str) -> bool:
    """Validate email format."""
    return "@" in email
'''

# tests/test_parsers/test_python_parser.py
def test_extracts_classes(sample_python_code):
    parser = PythonParser()
    result = parser.parse_file("test.py", sample_python_code)
    
    assert len(result.classes) == 1
    assert result.classes[0].name == "UserService"
    assert len(result.classes[0].methods) == 3

def test_extracts_module_functions(sample_python_code):
    parser = PythonParser()
    result = parser.parse_file("test.py", sample_python_code)
    
    assert len(result.functions) == 1
    assert result.functions[0].name == "validate_email"

def test_extracts_docstrings(sample_python_code):
    parser = PythonParser()
    result = parser.parse_file("test.py", sample_python_code)
    
    assert result.classes[0].docstring == "Service for user operations."
    assert result.functions[0].docstring == "Validate email format."

# tests/test_graph/test_queries.py
def test_function_callers_query(graph_client):
    # Setup: Create nodes and relationships
    graph_client.create_node("Function", {"id": "f1", "name": "caller", "qualified_name": "caller"})
    graph_client.create_node("Function", {"id": "f2", "name": "target", "qualified_name": "target"})
    graph_client.create_relationship("f1", "f2", "CALLS")
    
    # Execute query
    service = QueryService(graph_client)
    results = service.execute_query(QueryType.CALLERS, function_name="target")
    
    assert len(results) == 1
    assert results[0]["caller"] == "caller"
```

---

## 13. Future Enhancements

### 13.1 Hybrid Search (Graph + Embeddings)

Combine structural graph queries with semantic similarity:

```python
class HybridSearch:
    """Combine graph queries with vector similarity."""
    
    def __init__(self, graph: GraphClient, embedding_model):
        self.graph = graph
        self.embeddings = embedding_model
    
    def search(self, query: str, top_k: int = 10):
        # Get semantic matches
        query_embedding = self.embeddings.encode(query)
        semantic_matches = self.vector_search(query_embedding, top_k * 2)
        
        # Enrich with graph context
        enriched = []
        for match in semantic_matches:
            context = self.graph.execute("""
                MATCH (n {id: $id})
                OPTIONAL MATCH (n)-[:DEFINED_IN]->(f:File)
                OPTIONAL MATCH (caller:Function)-[:CALLS]->(n)
                RETURN n, f.path AS file, collect(caller.name) AS callers
            """, {"id": match["id"]})
            enriched.append({**match, **context.records[0]})
        
        return enriched[:top_k]
```

### 13.2 Real-time Sync with IDE

WebSocket-based updates as developers make changes:

```python
@app.websocket("/ws/sync/{repo_id}")
async def sync_changes(websocket: WebSocket, repo_id: str):
    await websocket.accept()
    
    while True:
        data = await websocket.receive_json()
        
        if data["type"] == "file_changed":
            # Reparse single file
            parsed = parser_registry.parse_file(data["path"], data["content"])
            builder.update_file(parsed)
            
            # Notify about affected queries
            affected = query_service.execute_query(
                QueryType.IMPACT,
                entity_name=data["path"]
            )
            await websocket.send_json({
                "type": "graph_updated",
                "affected": affected
            })
```

### 13.3 Architecture Visualization

Generate visual diagrams from graph queries:

```python
def generate_component_diagram(entry_point: str, max_depth: int = 3) -> str:
    """Generate a Mermaid diagram of a component."""
    nodes = query_service.execute_query(
        QueryType.COMPONENT,
        entry_point=entry_point,
        max_depth=max_depth
    )
    
    mermaid = ["graph TD"]
    seen_edges = set()
    
    for node in nodes:
        # Add node
        node_id = node["function"].replace(".", "_")
        mermaid.append(f'    {node_id}["{node["function"]}"]')
        
        # Add edges from callees
        for callee in node.get("callees", []):
            callee_id = callee.replace(".", "_")
            edge = (node_id, callee_id)
            if edge not in seen_edges:
                mermaid.append(f"    {node_id} --> {callee_id}")
                seen_edges.add(edge)
    
    return "\n".join(mermaid)
```

---

## 14. Success Metrics

### 14.1 Performance Targets

| Metric | Target |
|--------|--------|
| Parse time (1000 file repo) | < 60 seconds |
| Query response (simple) | < 100ms |
| Query response (traversal, depth 3) | < 500ms |
| Incremental update (single file) | < 2 seconds |
| Memory usage (10k node graph) | < 500MB |

### 14.2 Quality Metrics

| Metric | Target |
|--------|--------|
| Parse accuracy (Python) | > 98% |
| Relationship accuracy | > 95% |
| Test coverage | > 80% |
| API uptime | > 99.5% |

---

## 15. Appendix

### 15.1 Cypher Query Reference

```cypher
-- Find circular dependencies
MATCH path = (f:File)-[:IMPORTS*2..10]->(f)
RETURN [node in nodes(path) | node.path] AS cycle

-- Most connected functions (potential refactoring targets)
MATCH (f:Function)
OPTIONAL MATCH (f)-[:CALLS]->(callee)
OPTIONAL MATCH (caller)-[:CALLS]->(f)
RETURN f.qualified_name,
       count(DISTINCT callee) AS outgoing,
       count(DISTINCT caller) AS incoming,
       count(DISTINCT callee) + count(DISTINCT caller) AS total
ORDER BY total DESC
LIMIT 20

-- Orphan functions (never called)
MATCH (f:Function)
WHERE NOT ()-[:CALLS]->(f)
  AND NOT f.name STARTS WITH '_'
  AND NOT f.name = '__init__'
RETURN f.qualified_name, f.file_id

-- Complex functions (high cyclomatic complexity)
MATCH (f:Function)
WHERE f.complexity > 10
RETURN f.qualified_name, f.complexity, f.file_id
ORDER BY f.complexity DESC

-- Type usage frequency
MATCH (f:Function)-[:RETURNS]->(t:Type)
RETURN t.name, count(f) AS usage_count
ORDER BY usage_count DESC
```

### 15.2 Troubleshooting

**Problem: Parsing fails for certain files**
- Check file encoding (must be UTF-8 compatible)
- Check for syntax errors in source file
- Verify tree-sitter grammar is installed

**Problem: Missing relationships**
- Check if both nodes exist before creating relationship
- Verify qualified names are resolved correctly
- Check for dynamic imports/calls that can't be statically analyzed

**Problem: Slow queries**
- Ensure indexes exist for queried properties
- Limit traversal depth
- Use more specific starting nodes

---

*Document Version: 1.0*  
*Last Updated: January 2025*  
*For questions or feedback, contact: paul@alchemyagentic.ai*
