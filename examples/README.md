# Gristle examples

A tiny FastAPI app you can index in seconds to see Gristle's graph in action.

## Try it

From the repo root, with FalkorDB running (`docker compose up -d falkordb`) and
Gristle installed:

```bash
gristle ingest examples/sample-app --repo-id demo
gristle overview --repo-id demo
gristle explore register --repo-id demo
gristle query "MATCH (f:Function)-[:CALLS]->(g:Function) RETURN f.name, g.name LIMIT 10" --repo-id demo
```

You'll see the route handlers (`register`, `read_user`), the service functions
they call (`create_user`, `get_user`), the `User` model, and the test that
covers the service — all as connected nodes in the graph. `gristle explore
register` shows that the `POST /users` handler calls `create_user`, which is the
kind of structural relationship vector search over code can't give you.

## What's here

- `app/main.py` — FastAPI routes that call into the service layer
- `app/services.py` — business logic (`create_user`, `get_user`)
- `app/models.py` — a Pydantic `User` model
- `tests/test_services.py` — a test that exercises the service
