"""Test enriched graph features against pig-knuckle."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from gristle.graph.client import GraphClient
from gristle.ingestion.pipeline import IngestionPipeline

graph = GraphClient(host='localhost', port=6380, repo_id='pig-knuckle')
pipeline = IngestionPipeline(graph)
result = pipeline.ingest_repo(r'd:\projects\pig-knuckle')

print("=== INGESTION SUMMARY ===")
print(f"Files processed: {result.files_processed}")
print(f"Docs processed: {result.docs_processed}")
print(f"Nodes created: {result.nodes_created}")
print(f"Relationships created: {result.relationships_created}")
print(f"Routes found: {result.routes_found}")
print(f"Components found: {result.components_found}")
print(f"Test files found: {result.test_files_found}")
print(f"TODOs found: {result.todos_found}")
print(f"Doc refs resolved: {result.doc_references_resolved}/{result.doc_references_total}")
print(f"Errors: {len(result.errors)}")
if result.errors:
    for e in result.errors[:5]:
        print(f"  - {e}")

# Node stats
print("\n=== NODE STATS ===")
node_stats = graph.execute("MATCH (n) RETURN labels(n)[0] AS type, count(*) AS count ORDER BY count DESC")
for r in node_stats.records:
    print(f"  {r['type']}: {r['count']}")

# Relationship stats
print("\n=== RELATIONSHIP STATS ===")
rel_stats = graph.execute("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC")
for r in rel_stats.records:
    print(f"  {r['type']}: {r['count']}")

# Components
print("\n=== TOP COMPONENTS (by usage) ===")
comps = graph.execute("""
    MATCH (f:Function)
    WHERE f.is_component = true
    OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
    RETURN f.name AS name, f.file_path AS file, count(DISTINCT caller) AS usages
    ORDER BY usages DESC LIMIT 10
""")
for r in comps.records:
    print(f"  {r['name']} ({r['file']}): {r['usages']} usages")

# Routes
print("\n=== ROUTES ===")
routes = graph.execute("MATCH (r:Route) RETURN r.method AS method, r.path AS path, r.handler_name AS handler, r.file_path AS file LIMIT 15")
for r in routes.records:
    print(f"  {r['method']} {r['path']} -> {r['handler']} ({r['file']})")

route_summary = graph.execute("MATCH (r:Route) RETURN r.method AS method, count(*) AS count ORDER BY count DESC")
print("\nRoute methods:")
for r in route_summary.records:
    print(f"  {r['method']}: {r['count']}")

# Entry points
print("\n=== ENTRY POINTS ===")
entries = graph.execute("""
    MATCH (f:Function)
    WHERE f.is_entry_point = true
    RETURN f.name AS name, f.file_path AS file
    LIMIT 15
""")
for r in entries.records:
    print(f"  {r['name']} ({r['file']})")

# Test files
print("\n=== TEST FILES ===")
tests = graph.execute("MATCH (f:File) WHERE f.is_test_file = true RETURN f.path AS path LIMIT 10")
for r in tests.records:
    print(f"  {r['path']}")
test_count = graph.execute("MATCH (f:File) WHERE f.is_test_file = true RETURN count(*) AS c")
print(f"Total test files: {test_count.records[0]['c']}")

# TODOs
print("\n=== TOP TODO FILES ===")
todo_files = graph.execute("MATCH (f:File) WHERE f.todo_count > 0 RETURN f.path AS path, f.todo_count AS todos ORDER BY todos DESC LIMIT 10")
for r in todo_files.records:
    print(f"  {r['path']}: {r['todos']} TODOs")

total_todos = graph.execute("MATCH (f:File) WHERE f.todo_count > 0 RETURN sum(f.todo_count) AS total")
print(f"Total TODOs: {total_todos.records[0]['total']}")
