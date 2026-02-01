"""Test document indexing against pig-knuckle."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from gristle.graph.client import GraphClient
from gristle.ingestion.pipeline import IngestionPipeline

graph = GraphClient(host='localhost', port=6380, repo_id='pig-knuckle')
pipeline = IngestionPipeline(graph)
result = pipeline.ingest_repo(r'd:\projects\pig-knuckle')

print(f"Files processed: {result.files_processed}")
print(f"Files skipped: {result.files_skipped}")
print(f"Docs processed: {result.docs_processed}")
print(f"Nodes created: {result.nodes_created}")
print(f"Relationships created: {result.relationships_created}")
print(f"Doc references total: {result.doc_references_total}")
print(f"Doc references resolved: {result.doc_references_resolved}")
if result.doc_references_total > 0:
    pct = result.doc_references_resolved / result.doc_references_total * 100
    print(f"Resolution rate: {pct:.1f}%")
print(f"Errors: {len(result.errors)}")
if result.errors:
    for e in result.errors[:10]:
        print(f"  - {e}")

# Query some stats
print("\n--- Graph stats ---")
node_stats = graph.execute("MATCH (n) RETURN labels(n)[0] AS type, count(*) AS count")
for r in node_stats.records:
    print(f"  {r['type']}: {r['count']}")

rel_stats = graph.execute("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count")
print("\n--- Relationship stats ---")
for r in rel_stats.records:
    print(f"  {r['type']}: {r['count']}")

# Check some document details
print("\n--- Document types ---")
doc_types = graph.execute("MATCH (d:Document) RETURN d.doc_type AS type, count(*) AS count ORDER BY count DESC")
for r in doc_types.records:
    print(f"  {r['type']}: {r['count']}")

print("\n--- Top referenced entities ---")
top_refs = graph.execute("""
    MATCH (ds)-[:REFERENCES]->(target)
    RETURN target.name AS entity, labels(target)[0] AS type, count(DISTINCT ds) AS refs
    ORDER BY refs DESC LIMIT 10
""")
for r in top_refs.records:
    print(f"  {r['entity']} ({r['type']}): {r['refs']} refs")

print("\n--- Sample REFERENCES edges ---")
sample_refs = graph.execute("""
    MATCH (ds:DocumentSection)-[:REFERENCES]->(target)
    RETURN ds.heading AS section, ds.file_path AS doc, target.name AS target, labels(target)[0] AS target_type
    LIMIT 10
""")
for r in sample_refs.records:
    print(f"  [{r['doc']}] '{r['section']}' -> {r['target']} ({r['target_type']})")
