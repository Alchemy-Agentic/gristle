"""Query engine with pre-built Cypher templates for code analysis."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gristle.config import settings
from gristle.models import RESOLUTION_RANK, resolution_confidence, weakest_resolution

if TYPE_CHECKING:
    from gristle.graph.client import GraphClient


# External service categories for dependency classification.
_SERVICE_CATEGORIES: dict[str, dict[str, Any]] = {
    "database": {
        "packages": [
            "@supabase/supabase-js",
            "supabase",
            "prisma",
            "@prisma/client",
            "drizzle-orm",
            "mongoose",
            "typeorm",
            "sequelize",
            "knex",
            "pg",
            "mysql2",
            "better-sqlite3",
            "redis",
            "ioredis",
        ],
        "label": "Database & ORM",
    },
    "auth": {
        "packages": [
            "@clerk/nextjs",
            "@clerk/clerk-js",
            "next-auth",
            "@auth/core",
            "passport",
            "lucia",
            "arctic",
            "@supabase/auth-helpers-nextjs",
        ],
        "label": "Authentication",
    },
    "payments": {
        "packages": [
            "stripe",
            "@stripe/stripe-js",
            "@stripe/react-stripe-js",
            "@lemonsqueezy/lemonsqueezy.js",
        ],
        "label": "Payments",
    },
    "email": {
        "packages": [
            "resend",
            "@sendgrid/mail",
            "nodemailer",
            "postmark",
            "@react-email/components",
            "react-email",
        ],
        "label": "Email",
    },
    "ai": {
        "packages": [
            "openai",
            "@anthropic-ai/sdk",
            "ai",
            "@ai-sdk/openai",
            "@ai-sdk/anthropic",
            "langchain",
            "@langchain/core",
            "replicate",
        ],
        "label": "AI & LLM",
    },
    "storage": {
        "packages": [
            "@aws-sdk/client-s3",
            "@supabase/storage-js",
            "cloudinary",
            "uploadthing",
            "@uploadthing/react",
        ],
        "label": "File Storage",
    },
    "analytics": {
        "packages": [
            "posthog-js",
            "@vercel/analytics",
            "@sentry/nextjs",
            "@sentry/node",
            "mixpanel",
            "amplitude-js",
        ],
        "label": "Analytics & Monitoring",
    },
    "ui": {
        "packages": [
            "tailwindcss",
            "class-variance-authority",
            "clsx",
            "tailwind-merge",
            "lucide-react",
            "@heroicons/react",
            "framer-motion",
            "@headlessui/react",
            "cmdk",
        ],
        "label": "UI & Styling",
    },
    "forms": {
        "packages": [
            "react-hook-form",
            "@hookform/resolvers",
            "zod",
            "yup",
            "formik",
        ],
        "label": "Forms & Validation",
    },
    "state": {
        "packages": [
            "zustand",
            "jotai",
            "@tanstack/react-query",
            "swr",
            "@reduxjs/toolkit",
            "react-redux",
            "recoil",
        ],
        "label": "State Management",
    },
}


class QueryEngine:
    """Executes graph queries and enriches results with source code on demand."""

    def __init__(self, graph: GraphClient, repo_path: str | None = None):
        self.graph = graph
        self.repo_path = repo_path

    # ------------------------------------------------------------------
    # 1. Function context
    # ------------------------------------------------------------------

    def get_function_context(
        self,
        name: str,
        include_source: bool = True,
    ) -> dict[str, Any] | None:
        """Get a function with its callers, callees, class, and optionally source.

        `callers_detail` / `callees_detail` carry how reliably each call edge was
        resolved (see :func:`resolution_confidence`); `callers` / `callees` remain
        plain name lists.
        """
        result = self.graph.execute(
            """
            MATCH (f:Function)
            WHERE f.name = $name OR f.qualified_name = $name
            MATCH (f)-[:DEFINED_IN]->(file:File)
            OPTIONAL MATCH (cls:Class)-[:CONTAINS]->(f)
            OPTIONAL MATCH (caller:Function)-[cr:CALLS]->(f)
            OPTIONAL MATCH (f)-[er:CALLS]->(callee:Function)
            RETURN f.qualified_name AS qualified_name,
                   f.name AS name,
                   f.signature AS signature,
                   f.docstring AS docstring,
                   f.start_line AS start_line,
                   f.end_line AS end_line,
                   f.is_async AS is_async,
                   f.complexity AS complexity,
                   f.decorators AS decorators,
                   f.visibility AS visibility,
                   f.return_type AS return_type,
                   file.path AS file_path,
                   cls.name AS class_name,
                   collect(DISTINCT {name: caller.qualified_name,
                                     resolution: cr.resolution}) AS caller_details,
                   collect(DISTINCT {name: callee.qualified_name,
                                     resolution: er.resolution}) AS callee_details
            """,
            {"name": name},
        )
        if not result.records:
            return None

        rec = result.records[0]
        for direction in ("caller", "callee"):
            details = sorted(
                self._resolution_details(rec.pop(f"{direction}_details", None), "name"),
                key=lambda d: d["name"],
            )
            rec[f"{direction}s_detail"] = details
            # Derive the plain list from the details (1:1 — one CALLS edge per
            # pair) so both stay aligned when capped below.
            rec[f"{direction}s"] = [d["name"] for d in details]
            self._cap_list(rec, f"{direction}s_detail")
            self._cap_list(rec, f"{direction}s")
        if include_source and self.repo_path:
            rec["source_code"] = self._load_source(rec["file_path"], rec["start_line"], rec["end_line"])
        return rec

    # ------------------------------------------------------------------
    # 2. Class structure
    # ------------------------------------------------------------------

    def get_class_structure(self, name: str) -> dict[str, Any] | None:
        """Get a class with its methods and inheritance chain."""
        result = self.graph.execute(
            """
            MATCH (c:Class)
            WHERE c.name = $name OR c.qualified_name = $name
            MATCH (c)-[:DEFINED_IN]->(file:File)
            OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
            RETURN c.qualified_name AS qualified_name,
                   c.name AS name,
                   c.signature AS signature,
                   c.docstring AS docstring,
                   c.start_line AS start_line,
                   c.end_line AS end_line,
                   c.bases AS bases,
                   c.is_abstract AS is_abstract,
                   c.decorators AS decorators,
                   file.path AS file_path,
                   collect(DISTINCT {
                       name: m.name,
                       signature: m.signature,
                       visibility: m.visibility,
                       is_async: m.is_async,
                       docstring: m.docstring
                   }) AS methods
            """,
            {"name": name},
        )
        if not result.records:
            return None

        rec = result.records[0]

        # Get inheritance chain
        hierarchy = self.graph.execute(
            """
            MATCH path = (c:Class)-[:INHERITS_FROM*1..10]->(ancestor:Class)
            WHERE c.name = $name OR c.qualified_name = $name
            RETURN [node in nodes(path) | node.name] AS chain
            ORDER BY length(path) DESC
            LIMIT 1
            """,
            {"name": name},
        )
        rec["hierarchy"] = hierarchy.records[0]["chain"] if hierarchy.records else [rec["name"]]
        return rec

    # ------------------------------------------------------------------
    # 3. File overview
    # ------------------------------------------------------------------

    def get_file_overview(self, file_path: str) -> dict[str, Any] | None:
        """Get all classes, functions, imports, routes, and test coverage for a file."""
        result = self.graph.execute(
            """
            MATCH (f:File {path: $path})
            OPTIONAL MATCH (f)-[:CONTAINS]->(c:Class)
            OPTIONAL MATCH (f)-[:CONTAINS]->(fn:Function)
            WHERE fn.id IS NOT NULL
            OPTIONAL MATCH (f)-[:CONTAINS]->(imp:Import)
            RETURN f.path AS path,
                   f.language AS language,
                   f.line_count AS line_count,
                   f.docstring AS docstring,
                   f.is_test_file AS is_test_file,
                   collect(DISTINCT {name: c.name, signature: c.signature, start_line: c.start_line}) AS classes,
                   collect(DISTINCT {name: fn.name, signature: fn.signature, start_line: fn.start_line}) AS functions,
                   collect(DISTINCT {module: imp.module_path, names: imp.imported_names}) AS imports
            """,
            {"path": file_path},
        )
        if not result.records:
            return None

        rec = result.records[0]

        # Routes in this file
        routes = self.graph.execute(
            """
            MATCH (r:Route)
            WHERE r.file_path = $path
            RETURN r.method AS method, r.path AS path,
                   r.handler_name AS handler, r.line AS line
            ORDER BY r.line
            """,
            {"path": file_path},
        )
        if routes.records:
            rec["routes"] = routes.records

        # Test coverage (which test files test this file)
        test_coverage = self.graph.execute(
            """
            MATCH (test:File)-[:TESTS]->(f:File {path: $path})
            RETURN test.path AS test_file
            ORDER BY test.path
            """,
            {"path": file_path},
        )
        if test_coverage.records:
            rec["tested_by"] = [r["test_file"] for r in test_coverage.records]

        # If this is a test file, what does it test?
        if rec.get("is_test_file"):
            tests_targets = self.graph.execute(
                """
                MATCH (f:File {path: $path})-[:TESTS]->(prod:File)
                RETURN prod.path AS production_file
                ORDER BY prod.path
                """,
                {"path": file_path},
            )
            if tests_targets.records:
                rec["tests_files"] = [r["production_file"] for r in tests_targets.records]

        return rec

    # ------------------------------------------------------------------
    # 4. Callers (transitive)
    # ------------------------------------------------------------------

    # Default cap for unbounded list fields in tool outputs. On large repos a hub
    # function can have hundreds of callers; returning them all floods an agent's
    # context (measured: single impact calls >50k tokens). Lists are computed in
    # full — every count/score uses the full data — and truncated only at the
    # public projection, with a sibling `<field>_omitted: N` recording the cut.
    _LIST_CAP = 25
    # get_models: Supabase generated types put ~200 tables in one repo, and the
    # uncapped list view measured 134k tokens on a real app. The list is a map
    # (name/orm/fieldCount per model); per-model field detail is one
    # get_model_detail call away.
    _MODELS_CAP = 50
    _MODEL_INLINE_CAP = 10

    @staticmethod
    def _cap_list(record: dict[str, Any], field: str, cap: int = _LIST_CAP) -> None:
        """Truncate ``record[field]`` to *cap* items, adding ``<field>_omitted``."""
        items = record.get(field)
        if isinstance(items, list) and len(items) > cap:
            record[f"{field}_omitted"] = len(items) - cap
            record[field] = items[:cap]

    @staticmethod
    def _non_null_maps(record: dict[str, Any], field: str, name_key: str) -> None:
        """Drop the all-null map FalkorDB emits when an OPTIONAL MATCH found
        nothing — a map literal survives ``collect()`` where a plain null would
        not, so a model with no relations would report a phantom one."""
        entries = record.get(field)
        if isinstance(entries, list):
            record[field] = [e for e in entries if isinstance(e, dict) and e.get(name_key) is not None]

    @staticmethod
    def _resolution_details(entries: list[dict[str, Any]] | None, name_key: str) -> list[dict[str, Any]]:
        """Turn raw ``collect()``-ed ``{name, resolution}`` maps into rows carrying a
        confidence bucket.

        Drops the all-null entry FalkorDB emits when an OPTIONAL MATCH found nothing:
        a map literal is itself non-null, so (unlike a plain ``collect()``, which drops
        nulls) it survives — and an entity with no callers would report a phantom one.
        """
        return [
            {
                name_key: e["name"],
                "resolution": e.get("resolution"),
                "confidence": resolution_confidence(e.get("resolution")),
            }
            for e in (entries or [])
            if e.get("name")
        ]

    @staticmethod
    def _best_route_confidence(paths: list[list[str | None]]) -> tuple[str | None, str]:
        """Pick the most trustworthy route among *paths* and describe its confidence.

        A call path is only as reliable as its weakest edge, so each path is scored
        by its weakest `resolution`; the best (highest-ranked) of those wins. Returns
        ``(weakest_resolution_label, confidence_bucket)`` for that route.
        """
        weakest_per_path = [weakest_resolution(p) for p in paths if p]
        if not weakest_per_path:
            return None, "unknown"
        best = max(weakest_per_path, key=lambda r: RESOLUTION_RANK.get(r or "", 0))
        return best, resolution_confidence(best)

    def get_callers(self, name: str, max_depth: int = 2) -> list[dict[str, Any]]:
        """Find all functions that call a given function, up to max_depth.

        Each result carries `resolution` / `confidence`: how reliably the call path
        was resolved (weakest edge on the best route). See :func:`resolution_confidence`.
        """
        result = self.graph.execute(
            f"""
            MATCH path = (caller:Function)-[:CALLS*1..{max_depth}]->(target:Function)
            WHERE target.name = $name OR target.qualified_name = $name
            WITH caller, length(path) AS d, [r IN relationships(path) | r.resolution] AS res
            RETURN caller.qualified_name AS caller,
                   caller.file_path AS file_path,
                   caller.start_line AS line,
                   min(d) AS depth,
                   collect(res) AS routes
            ORDER BY depth, caller
            """,
            {"name": name},
        )
        return [self._with_confidence(r, "routes") for r in result.records]

    # ------------------------------------------------------------------
    # 5. Callees (transitive)
    # ------------------------------------------------------------------

    def get_callees(self, name: str, max_depth: int = 2) -> list[dict[str, Any]]:
        """Find all functions called by a given function, up to max_depth.

        Each result carries `resolution` / `confidence` (see :meth:`get_callers`).
        """
        result = self.graph.execute(
            f"""
            MATCH path = (source:Function)-[:CALLS*1..{max_depth}]->(callee:Function)
            WHERE source.name = $name OR source.qualified_name = $name
            WITH callee, length(path) AS d, [r IN relationships(path) | r.resolution] AS res
            RETURN callee.qualified_name AS callee,
                   callee.file_path AS file_path,
                   callee.start_line AS line,
                   min(d) AS depth,
                   collect(res) AS routes
            ORDER BY depth, callee
            """,
            {"name": name},
        )
        return [self._with_confidence(r, "routes") for r in result.records]

    def _with_confidence(self, record: dict[str, Any], routes_key: str) -> dict[str, Any]:
        """Replace a record's raw per-path resolution lists with resolution/confidence."""
        resolution, confidence = self._best_route_confidence(record.pop(routes_key, []) or [])
        record["resolution"] = resolution
        record["confidence"] = confidence
        return record

    # ------------------------------------------------------------------
    # 6. Impact analysis
    # ------------------------------------------------------------------

    # Impact list fields that are unbounded on large repos (a hub function can
    # have hundreds of callers). Public projections cap these; all counts and
    # scores are computed from the full data first.
    _IMPACT_LIST_FIELDS = (
        "direct_callers",
        "direct_callers_detail",
        "low_confidence_callers",
        "transitive_callers",
        "affected_files",
        "total_affected_files",
        "test_files",
        "test_functions",
    )

    def _capped_impact(self, rec: dict[str, Any] | None) -> dict[str, Any] | None:
        if rec is None:
            return None
        rec = dict(rec)
        # Preserve full totals as *_count before capping (setdefault: the scored
        # variant computes its own counts, which win).
        for field in ("direct_callers", "transitive_callers", "affected_files", "total_affected_files"):
            if isinstance(rec.get(field), list):
                rec.setdefault(f"{field}_count", len(rec[field]))
        for field in self._IMPACT_LIST_FIELDS:
            self._cap_list(rec, field)
        return rec

    def impact_analysis(self, name: str) -> dict[str, Any] | None:
        """Analyze what would be affected by changing a function or class.

        List fields are capped at ``_LIST_CAP`` items (``<field>_omitted`` gives
        the cut) — full counts are preserved in the ``*_count`` fields.
        """
        return self._capped_impact(self._impact_analysis_full(name))

    def _impact_analysis_full(self, name: str) -> dict[str, Any] | None:
        """Uncapped impact data — composites build on this so counts stay exact."""
        result = self.graph.execute(
            """
            MATCH (target)
            WHERE (target:Function OR target:Class)
              AND (target.name = $name OR target.qualified_name = $name)
            OPTIONAL MATCH (target)-[:DEFINED_IN]->(file:File)
            OPTIONAL MATCH (caller:Function)-[cr:CALLS]->(target)
            OPTIONAL MATCH (caller)-[:DEFINED_IN]->(caller_file:File)
            RETURN target.qualified_name AS target,
                   labels(target)[0] AS target_type,
                   file.path AS target_file,
                   collect(DISTINCT {name: caller.qualified_name,
                                     resolution: cr.resolution}) AS caller_details,
                   collect(DISTINCT caller_file.path) AS affected_files
            """,
            {"name": name},
        )
        if not result.records:
            return None

        rec = result.records[0]

        details = sorted(
            self._resolution_details(rec.pop("caller_details", None), "caller"),
            key=lambda d: d["caller"],
        )
        rec["direct_callers_detail"] = details
        # Derive the plain list from the details: one CALLS edge per caller pair,
        # so they're 1:1 — deriving keeps the two lists aligned when both are
        # capped at projection time (independent collects order arbitrarily).
        rec["direct_callers"] = [d["caller"] for d in details]
        rec["low_confidence_callers"] = [d["caller"] for d in details if d["confidence"] == "low"]

        # Also get transitive callers (depth 2) for broader impact
        transitive = self.get_callers(name, max_depth=3)
        rec["transitive_callers"] = [r["caller"] for r in transitive]
        # sorted(): set iteration order is nondeterministic — capping an unsorted
        # projection would return a different 25-file subset on every call.
        rec["total_affected_files"] = sorted(
            {r["file_path"] for r in transitive if r.get("file_path")} | set(rec.get("affected_files") or [])
        )

        # Test coverage: which test files cover this entity's file?
        target_file = rec.get("target_file")
        if target_file:
            test_files = self.graph.execute(
                """
                MATCH (test:File)-[:TESTS]->(prod:File {path: $path})
                RETURN test.path AS test_file
                ORDER BY test.path
                """,
                {"path": target_file},
            )
            rec["test_files"] = [r["test_file"] for r in test_files.records]

        # Also find test functions that directly call this entity
        test_funcs = self.get_tests_for_entity(name)
        if test_funcs:
            rec["test_functions"] = test_funcs

        # Routes that handle this function (if it's a route handler)
        routes = self.graph.execute(
            """
            MATCH (r:Route)-[:HANDLES]->(target:Function)
            WHERE target.name = $name OR target.qualified_name = $name
            RETURN r.method AS method, r.path AS path,
                   r.file_path AS file_path, r.line AS line
            """,
            {"name": name},
        )
        if routes.records:
            rec["routes"] = routes.records

        return rec

    def get_impact_analysis(
        self,
        name: str,
        include_source: bool = False,
    ) -> dict[str, Any] | None:
        """Analyze impact of changing a function/class with blast radius scoring.

        Returns impact analysis with scores:
        - direct_impact_score (0-100): Based on direct callers, callbacks, routes
        - transitive_impact_score (0-100): Based on transitive callers, affected files
        - blast_radius_score (0-100): Combined weighted score
        - risk_level: low/medium/high/critical classification

        List fields are capped at ``_LIST_CAP`` items (``<field>_omitted`` gives
        the cut); the ``*_count`` fields always reflect the full data.
        """
        return self._capped_impact(self._get_impact_analysis_full(name, include_source))

    def _get_impact_analysis_full(
        self,
        name: str,
        include_source: bool = False,
    ) -> dict[str, Any] | None:
        """Uncapped scored impact — counts and scores come from the full lists."""
        # Get base impact data
        base = self._impact_analysis_full(name)
        if not base:
            return None

        # Count direct relationships
        direct_callers_count = len(base.get("direct_callers") or [])
        has_route = bool(base.get("routes"))
        is_entry_point = False
        is_exported = False

        # Check if entry point or exported
        entity_check = self.graph.execute(
            """
            MATCH (target)
            WHERE (target:Function OR target:Class)
              AND (target.name = $name OR target.qualified_name = $name)
            RETURN target.is_entry_point AS is_entry_point,
                   target.is_exported AS is_exported
            """,
            {"name": name},
        )
        if entity_check.records:
            is_entry_point = entity_check.records[0].get("is_entry_point") or False
            is_exported = entity_check.records[0].get("is_exported") or False

        # Count PASSED_TO (callback) relationships
        callback_count = self.graph.execute(
            """
            MATCH (passer:Function)-[:PASSED_TO]->(target)
            WHERE (target:Function OR target:Class)
              AND (target.name = $name OR target.qualified_name = $name)
            RETURN count(DISTINCT passer) AS count
            """,
            {"name": name},
        )
        passed_to_count = callback_count.records[0].get("count", 0) if callback_count.records else 0

        # Transitive metrics (scoring weighs the transitive file union)
        transitive_callers = base.get("transitive_callers") or []
        total_affected_files = base.get("total_affected_files") or []
        test_files = base.get("test_files") or []
        has_tests = len(test_files) > 0

        # Calculate Direct Impact Score (0-100)
        direct_score = 0.0
        direct_score += min(direct_callers_count * 5, 40)  # Max 40 points for direct callers
        direct_score += min(passed_to_count * 8, 20)  # Max 20 points for callbacks (higher weight)
        if has_route:
            direct_score += 20  # Route handlers are important
        if is_entry_point:
            direct_score += 15  # Entry points are critical
        if is_exported:
            direct_score += 5  # Exported = part of public API

        direct_score = min(direct_score, 100)

        # Calculate Transitive Impact Score (0-100)
        transitive_score = 0.0
        transitive_score += min(len(transitive_callers) * 2, 50)  # Max 50 for transitive callers
        transitive_score += min(len(total_affected_files) * 5, 30)  # Max 30 for affected files
        if not has_tests:
            transitive_score += 20  # No tests = higher risk

        transitive_score = min(transitive_score, 100)

        # Combined Blast Radius Score (weighted average: 60% direct, 40% transitive)
        blast_radius = direct_score * 0.6 + transitive_score * 0.4

        # Risk classification
        if blast_radius >= 85:
            risk_level = "critical"
        elif blast_radius >= 60:
            risk_level = "high"
        elif blast_radius >= 30:
            risk_level = "medium"
        else:
            risk_level = "low"

        # Build result. Each *_count counts the SAME-named list field (so
        # len(field) + field_omitted == field_count always reconciles); the
        # transitive union gets its own explicitly-named count.
        result = {
            **base,
            "direct_callers_count": direct_callers_count,
            "passed_to_count": passed_to_count,
            "transitive_callers_count": len(transitive_callers),
            "affected_files_count": len(base.get("affected_files") or []),
            "total_affected_files_count": len(total_affected_files),
            "has_route": has_route,
            "is_entry_point": is_entry_point,
            "is_exported": is_exported,
            "has_tests": has_tests,
            "direct_impact_score": round(direct_score, 1),
            "transitive_impact_score": round(transitive_score, 1),
            "blast_radius_score": round(blast_radius, 1),
            "risk_level": risk_level,
        }

        # Optionally include source code
        start_line = base.get("start_line")
        end_line = base.get("end_line")
        if include_source and base.get("target_file") and start_line is not None and end_line is not None:
            source = self._load_source(base["target_file"], int(start_line), int(end_line))
            if source:
                result["source"] = source

        return result

    # Change-impact list fields capped at the public projection (counts stay full).
    _CHANGE_IMPACT_LIST_FIELDS = (
        "direct_callers",
        "direct_callers_detail",
        "low_confidence_callers",
        "affected_files",
        "tests_to_run",
    )

    def get_change_impact(self, name: str) -> dict[str, Any] | None:
        """One-call pre-edit safety check for an agent about to change an entity.

        Composes the scored blast radius (:meth:`get_impact_analysis`) with the
        exact covering tests (:meth:`get_tests_for_entity`), so an agent can answer
        "what breaks, and what must I run?" in a single call instead of chaining
        impact -> tests -> trace. Returns None if the entity isn't found.

        List fields are capped at ``_LIST_CAP`` items (``<field>_omitted`` gives
        the cut); the ``*_count`` fields always reflect the full data.
        """
        full = self._change_impact_full(name)
        if full is None:
            return None
        for field in self._CHANGE_IMPACT_LIST_FIELDS:
            self._cap_list(full, field)
        return full

    def _change_impact_full(self, name: str) -> dict[str, Any] | None:
        """Uncapped change impact — the changeset aggregation builds on this so
        external-caller and test unions see every caller, not the capped view."""
        impact = self._get_impact_analysis_full(name)
        if impact is None:
            return None
        tests = self.get_tests_for_entity(name)
        score = impact["blast_radius_score"]
        risk = impact["risk_level"]
        n_callers = impact["direct_callers_count"]
        # The change-impact payload's affected_files IS the transitive union
        # (a pre-edit check wants the full blast surface), so its count matches.
        affected_files = impact.get("total_affected_files") or []
        n_files = impact["total_affected_files_count"]
        if tests:
            test_advice = f"Run the {len(tests)} covering test(s) below before and after editing."
        else:
            test_advice = "No covering tests found — add tests before changing this."
        low_confidence = impact.get("low_confidence_callers") or []
        confidence_advice = (
            f" {len(low_confidence)} caller edge(s) are low-confidence — verify those call sites by hand."
            if low_confidence
            else ""
        )
        recommendation = (
            f"{risk.upper()} risk (blast radius {score}/100): {n_callers} direct caller(s), "
            f"{n_files} affected file(s). {test_advice}{confidence_advice}"
        )
        # recommendation and scores lead: the actionable summary should be the
        # first thing an agent reads, before the (capped) supporting lists.
        return {
            "recommendation": recommendation,
            "entity": impact.get("target") or name,
            "entity_type": impact.get("target_type"),
            "file": impact.get("target_file"),
            "blast_radius_score": score,
            "risk_level": risk,
            "direct_callers_count": n_callers,
            "affected_files_count": n_files,
            "is_entry_point": impact.get("is_entry_point"),
            "is_exported": impact.get("is_exported"),
            "has_route": impact.get("has_route"),
            "tests_to_run": tests,
            "direct_callers": impact.get("direct_callers") or [],
            "direct_callers_detail": impact.get("direct_callers_detail") or [],
            "low_confidence_callers": low_confidence,
            "affected_files": affected_files,
        }

    # Risk levels ordered worst-last, for taking the max across a changeset.
    _RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

    def get_changeset_impact(self, names: list[str]) -> dict[str, Any]:
        """Combined pre-edit safety check for a *set* of entities changed together.

        Agents edit diffs, not single symbols. This runs :meth:`get_change_impact`
        for each named entity and aggregates the parts an agent needs to vet a whole
        edit in one call:

        - **external_callers** — callers that are *not themselves in the changeset*
          (a co-edited symbol calling another isn't blast radius), i.e. the real
          surface this edit might break.
        - **tests_to_run** — the de-duplicated union of every changed entity's
          covering tests.
        - **affected_files** — files touched by external callers, excluding the
          files being edited.
        - **overall_risk_level / max_blast_radius_score** — worst case across the set.

        Entities that don't resolve are reported in ``not_found`` rather than failing.
        """
        per_entity: list[dict[str, Any]] = []
        not_found: list[str] = []
        for name in names:
            # Full (uncapped) per-entity data: the external-caller and test unions
            # must see every caller, not the capped projection.
            impact = self._change_impact_full(name)
            if impact is None:
                not_found.append(name)
            else:
                per_entity.append(impact)

        if not per_entity:
            return {
                "requested": names,
                "not_found": not_found,
                "entities": [],
                "error": "None of the requested entities were found.",
            }

        # Qualified names + files of the entities being edited together.
        changeset_ids = {ci["entity"] for ci in per_entity}
        changeset_files = {ci["file"] for ci in per_entity if ci.get("file")}

        external_callers: set[str] = set()
        low_confidence_callers: set[str] = set()
        affected_files: set[str] = set()
        tests_by_key: dict[str, dict[str, Any]] = {}
        for ci in per_entity:
            for caller in ci.get("direct_callers") or []:
                if caller and caller not in changeset_ids:
                    external_callers.add(caller)
            for caller in ci.get("low_confidence_callers") or []:
                if caller and caller not in changeset_ids:
                    low_confidence_callers.add(caller)
            for path in ci.get("affected_files") or []:
                if path:
                    affected_files.add(path)
            for test in ci.get("tests_to_run") or []:
                key = test.get("test_qualified_name") or f"file::{test.get('test_file')}"
                tests_by_key.setdefault(key, test)
        affected_files -= changeset_files

        overall_risk = max(
            (ci["risk_level"] for ci in per_entity),
            key=lambda r: self._RISK_ORDER.get(r, 0),
        )
        max_score = max(ci["blast_radius_score"] for ci in per_entity)
        tests_to_run = list(tests_by_key.values())

        n = len(per_entity)
        if tests_to_run:
            test_advice = f"Run the {len(tests_to_run)} covering test(s) below before and after editing."
        else:
            test_advice = "No covering tests found for this changeset — add tests before editing."
        confidence_advice = (
            f" {len(low_confidence_callers)} caller edge(s) are low-confidence — verify those by hand."
            if low_confidence_callers
            else ""
        )
        recommendation = (
            f"{overall_risk.upper()} risk across {n} changed "
            f"{'entity' if n == 1 else 'entities'} (max blast radius {max_score}/100): "
            f"{len(external_callers)} external caller(s), "
            f"{len(affected_files)} other affected file(s). {test_advice}{confidence_advice}"
        )

        result = {
            "recommendation": recommendation,
            "overall_risk_level": overall_risk,
            "max_blast_radius_score": max_score,
            "has_entry_point": any(ci.get("is_entry_point") for ci in per_entity),
            "has_route": any(ci.get("has_route") for ci in per_entity),
            "external_callers_count": len(external_callers),
            "affected_files_count": len(affected_files),
            "tests_to_run_count": len(tests_to_run),
            "requested": names,
            "not_found": not_found,
            "entities": [
                {
                    "entity": ci["entity"],
                    "entity_type": ci.get("entity_type"),
                    "file": ci.get("file"),
                    "blast_radius_score": ci["blast_radius_score"],
                    "risk_level": ci["risk_level"],
                    "direct_callers_count": ci.get("direct_callers_count"),
                    "is_entry_point": ci.get("is_entry_point"),
                    "has_route": ci.get("has_route"),
                }
                for ci in per_entity
            ],
            "external_callers": sorted(external_callers),
            "low_confidence_callers": sorted(low_confidence_callers),
            "affected_files": sorted(affected_files),
            "tests_to_run": tests_to_run,
        }
        # Counts above reflect the full unions; only the list projections are
        # capped — all at the union cap (tests_to_run included: it's the
        # changeset's action list, don't cut it shorter than the other unions).
        for field in ("external_callers", "low_confidence_callers", "affected_files", "tests_to_run"):
            self._cap_list(result, field, cap=50)
        return result

    # ------------------------------------------------------------------
    # 7. Search
    # ------------------------------------------------------------------

    def search(
        self,
        term: str,
        search_type: str = "all",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search for code entities by name or docstring."""
        if search_type == "name":
            result = self.graph.execute(
                """
                MATCH (n)
                WHERE (n:Function OR n:Class OR n:File)
                  AND (n.name CONTAINS $term OR n.qualified_name CONTAINS $term)
                RETURN labels(n)[0] AS type,
                       n.name AS name,
                       n.qualified_name AS qualified_name,
                       n.file_path AS file_path,
                       n.start_line AS start_line
                LIMIT $limit
                """,
                {"term": term, "limit": limit},
            )
        elif search_type == "docstring":
            result = self.graph.execute(
                """
                MATCH (n)
                WHERE (n:Function OR n:Class)
                  AND n.docstring CONTAINS $term
                RETURN labels(n)[0] AS type,
                       n.name AS name,
                       n.qualified_name AS qualified_name,
                       n.file_path AS file_path,
                       n.docstring AS docstring
                LIMIT $limit
                """,
                {"term": term, "limit": limit},
            )
        else:
            result = self.graph.execute(
                """
                MATCH (n)
                WHERE (n:Function OR n:Class OR n:File)
                  AND (n.name CONTAINS $term
                       OR n.qualified_name CONTAINS $term
                       OR n.docstring CONTAINS $term)
                RETURN labels(n)[0] AS type,
                       n.name AS name,
                       n.qualified_name AS qualified_name,
                       n.file_path AS file_path,
                       n.start_line AS start_line
                LIMIT $limit
                """,
                {"term": term, "limit": limit},
            )
        return result.records

    # ------------------------------------------------------------------
    # 8. Repo overview
    # ------------------------------------------------------------------

    def get_repo_overview(self) -> dict[str, Any]:
        """Get high-level statistics and structure of the indexed repo."""
        node_stats = self.graph.execute(
            """
            MATCH (n)
            RETURN labels(n)[0] AS type, count(*) AS count
            """
        )
        rel_stats = self.graph.execute(
            """
            MATCH ()-[r]->()
            RETURN type(r) AS type, count(*) AS count
            """
        )
        files = self.graph.execute(
            """
            MATCH (f:File)
            RETURN f.path AS path, f.language AS language
            ORDER BY f.path
            """
        )
        top_functions = self.graph.execute(
            """
            MATCH (f:Function)
            OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
            RETURN f.qualified_name AS name,
                   f.file_path AS file_path,
                   count(caller) AS caller_count
            ORDER BY caller_count DESC
            LIMIT 10
            """
        )

        overview = {
            "nodes": {r["type"]: r["count"] for r in node_stats.records},
            "relationships": {r["type"]: r["count"] for r in rel_stats.records},
            "files": [r["path"] for r in files.records],
            "languages": list({r["language"] for r in files.records}),
            "most_called_functions": top_functions.records,
        }
        # `nodes.File` carries the true total; listing thousands of paths floods
        # an agent's context without orienting it.
        self._cap_list(overview, "files", cap=100)
        return overview

    # ------------------------------------------------------------------
    # 9. Documentation queries
    # ------------------------------------------------------------------

    def get_docs_for_entity(self, name: str) -> list[dict[str, Any]]:
        """Find documentation that references a given code entity."""
        result = self.graph.execute(
            """
            MATCH (ds)-[:REFERENCES]->(target)
            WHERE target.name = $name OR target.qualified_name = $name
                  OR target.path = $name
            OPTIONAL MATCH (d:Document)-[:HAS_SECTION]->(ds)
            RETURN DISTINCT
                   coalesce(d.path, ds.path) AS doc_path,
                   coalesce(d.title, ds.heading) AS doc_title,
                   ds.heading AS section,
                   ds.start_line AS line,
                   target.name AS references_entity
            ORDER BY doc_path
            """,
            {"name": name},
        )
        return result.records

    def get_doc_staleness(self) -> list[dict[str, Any]]:
        """Find document sections with unresolved code references (potential staleness)."""
        result = self.graph.execute(
            """
            MATCH (d:Document)
            WHERE d.reference_count > 0
            OPTIONAL MATCH (d)-[:HAS_SECTION]->(ds:DocumentSection)-[:REFERENCES]->()
            WITH d, count(DISTINCT ds) AS sections_with_refs
            RETURN d.path AS doc_path,
                   d.title AS title,
                   d.doc_type AS doc_type,
                   d.reference_count AS total_refs,
                   sections_with_refs AS resolved_sections
            ORDER BY d.reference_count DESC
            LIMIT 20
            """
        )
        return result.records

    def get_doc_overview(self) -> dict[str, Any]:
        """Get overview of indexed documentation."""
        stats = self.graph.execute(
            """
            MATCH (d:Document)
            RETURN d.doc_type AS doc_type, count(*) AS count
            """
        )
        total_refs = self.graph.execute(
            """
            MATCH ()-[r:REFERENCES]->()
            RETURN count(r) AS count
            """
        )
        top_referenced = self.graph.execute(
            """
            MATCH (ds)-[:REFERENCES]->(target)
            RETURN coalesce(target.name, target.path) AS entity,
                   labels(target)[0] AS entity_type,
                   count(DISTINCT ds) AS ref_count
            ORDER BY ref_count DESC
            LIMIT 10
            """
        )
        return {
            "doc_types": {r["doc_type"]: r["count"] for r in stats.records},
            "total_references": total_refs.records[0]["count"] if total_refs.records else 0,
            "most_referenced_entities": top_referenced.records,
        }

    # ------------------------------------------------------------------
    # 10. Routes / endpoints
    # ------------------------------------------------------------------

    def get_routes(self, method: str | None = None) -> list[dict[str, Any]]:
        """Get all HTTP routes/endpoints, optionally filtered by method."""
        if method:
            result = self.graph.execute(
                """
                MATCH (r:Route)
                WHERE r.method = $method OR r.method = 'ALL'
                OPTIONAL MATCH (r)-[:HANDLES]->(f:Function)
                RETURN r.method AS method,
                       r.path AS path,
                       r.handler_name AS handler,
                       r.file_path AS file_path,
                       r.line AS line,
                       r.middleware AS middleware,
                       f.signature AS handler_signature
                ORDER BY r.path
                """,
                {"method": method.upper()},
            )
        else:
            result = self.graph.execute(
                """
                MATCH (r:Route)
                OPTIONAL MATCH (r)-[:HANDLES]->(f:Function)
                RETURN r.method AS method,
                       r.path AS path,
                       r.handler_name AS handler,
                       r.file_path AS file_path,
                       r.line AS line,
                       r.middleware AS middleware,
                       f.signature AS handler_signature
                ORDER BY r.path, r.method
                """
            )
        return result.records

    # ------------------------------------------------------------------
    # 11. Components
    # ------------------------------------------------------------------

    def get_components(self, limit: int = 50, exclude_docs: bool = True) -> list[dict[str, Any]]:
        """Get all React/UI components.

        Args:
            limit: Maximum results.
            exclude_docs: If True (default), exclude components in documentation/mockup directories.
        """
        result = self.graph.execute(
            """
            MATCH (f:Function)
            WHERE f.is_component = true
              AND ($exclude_docs = false OR f.is_documentation <> true)
            OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
            RETURN f.name AS name,
                   f.qualified_name AS qualified_name,
                   f.file_path AS file_path,
                   f.start_line AS start_line,
                   f.signature AS signature,
                   f.is_exported AS is_exported,
                   f.is_documentation AS is_documentation,
                   count(DISTINCT caller) AS usage_count
            ORDER BY usage_count DESC
            LIMIT $limit
            """,
            {"limit": limit, "exclude_docs": exclude_docs},
        )
        return result.records

    # ------------------------------------------------------------------
    # 12. Tests
    # ------------------------------------------------------------------

    def get_tests_for_entity(self, name: str) -> list[dict[str, Any]]:
        """Find test functions that call a given entity.

        Uses TESTS_FUNCTION edges (with depth), CALLS edges, and TESTS
        edges (test file -> production file coverage).
        """
        # Test functions linked via TESTS_FUNCTION edges (preferred, has depth)
        tf_edges = self.graph.execute(
            """
            MATCH (test:Function)-[r:TESTS_FUNCTION]->(target)
            WHERE (target.name = $name OR target.qualified_name = $name)
            RETURN DISTINCT test.name AS test_name,
                   test.qualified_name AS test_qualified_name,
                   test.file_path AS test_file,
                   test.start_line AS line,
                   r.depth AS depth,
                   'tests_function' AS via
            ORDER BY r.depth, test_file, test_name
            """,
            {"name": name},
        )

        # Fallback: Test functions that directly call the target (via CALLS chain)
        direct = self.graph.execute(
            """
            MATCH (test:Function)-[:CALLS*1..3]->(target)
            WHERE test.is_test = true
              AND (target.name = $name OR target.qualified_name = $name)
            RETURN DISTINCT test.name AS test_name,
                   test.qualified_name AS test_qualified_name,
                   test.file_path AS test_file,
                   test.start_line AS line,
                   'calls' AS via
            ORDER BY test_file, test_name
            """,
            {"name": name},
        )

        # Test files that cover the entity's file (via TESTS edges)
        file_level = self.graph.execute(
            """
            MATCH (target)
            WHERE (target:Function OR target:Class)
              AND (target.name = $name OR target.qualified_name = $name)
            MATCH (target)-[:DEFINED_IN]->(prod:File)
            MATCH (test_file:File)-[:TESTS]->(prod)
            RETURN DISTINCT test_file.path AS test_file,
                   'file_coverage' AS via
            ORDER BY test_file.path
            """,
            {"name": name},
        )

        # Merge results: TESTS_FUNCTION > CALLS > file_coverage
        seen_tests: set[str] = set()
        seen_files: set[str] = set()
        results = []
        for r in tf_edges.records:
            key = r["test_qualified_name"]
            if key not in seen_tests:
                seen_tests.add(key)
                seen_files.add(r["test_file"])
                results.append(r)
        for r in direct.records:
            key = r["test_qualified_name"]
            if key not in seen_tests:
                seen_tests.add(key)
                seen_files.add(r["test_file"])
                results.append(r)
        for r in file_level.records:
            if r["test_file"] not in seen_files:
                results.append(
                    {
                        "test_name": None,
                        "test_qualified_name": None,
                        "test_file": r["test_file"],
                        "line": None,
                        "via": "file_coverage",
                    }
                )
        return results

    def get_function_coverage(self, name: str) -> dict[str, Any]:
        """Get detailed test coverage for a specific function.

        Returns the function info, its tested_by_count, and which tests
        exercise it at what depth.
        """
        # Get function info + tested_by_count
        func_result = self.graph.execute(
            """
            MATCH (f:Function)
            WHERE f.name = $name OR f.qualified_name = $name
            RETURN f.name AS name,
                   f.qualified_name AS qualified_name,
                   f.file_path AS file_path,
                   f.is_exported AS is_exported,
                   f.complexity AS complexity,
                   f.tested_by_count AS tested_by_count
            LIMIT 1
            """,
            {"name": name},
        )
        if not func_result.records:
            return {"error": f"Function '{name}' not found."}

        func_info = func_result.records[0]

        # Get test functions that exercise it via TESTS_FUNCTION
        tests = self.graph.execute(
            """
            MATCH (test:Function)-[r:TESTS_FUNCTION]->(f:Function)
            WHERE f.name = $name OR f.qualified_name = $name
            RETURN test.name AS test_name,
                   test.qualified_name AS test_qualified_name,
                   test.file_path AS test_file,
                   r.depth AS depth
            ORDER BY r.depth, test_file, test_name
            """,
            {"name": name},
        )

        return {
            "function": func_info,
            "tests": tests.records,
        }

    def get_untested_functions(self, limit: int = 30) -> list[dict[str, Any]]:
        """Find non-test exported functions with no test coverage."""
        result = self.graph.execute(
            """
            MATCH (f:Function)
            WHERE f.is_test = false
              AND f.is_exported = true
              AND f.is_component = false
              AND f.tested_by_count = 0
            RETURN f.name AS name,
                   f.qualified_name AS qualified_name,
                   f.file_path AS file_path,
                   f.complexity AS complexity
            ORDER BY f.complexity DESC
            LIMIT $limit
            """,
            {"limit": limit},
        )
        return result.records

    def get_untested_critical(self, limit: int = 20) -> list[dict[str, Any]]:
        """Find exported functions with callers but zero test coverage.

        These are high-risk: other code depends on them, but no tests verify
        their behavior.
        """
        result = self.graph.execute(
            """
            MATCH (f:Function)
            WHERE f.is_test = false
              AND f.is_exported = true
              AND f.tested_by_count = 0
            MATCH (caller:Function)-[:CALLS]->(f)
            WHERE caller.is_test = false
            WITH f, count(DISTINCT caller) AS caller_count
            WHERE caller_count > 0
            RETURN f.name AS name,
                   f.qualified_name AS qualified_name,
                   f.file_path AS file_path,
                   f.complexity AS complexity,
                   caller_count
            ORDER BY caller_count DESC, f.complexity DESC
            LIMIT $limit
            """,
            {"limit": limit},
        )
        return result.records

    # ------------------------------------------------------------------
    # 13. TODOs
    # ------------------------------------------------------------------

    def get_todos(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get files with TODOs, ordered by count."""
        result = self.graph.execute(
            """
            MATCH (f:File)
            WHERE f.todo_count > 0
            RETURN f.path AS file_path,
                   f.todo_count AS todo_count,
                   f.language AS language
            ORDER BY f.todo_count DESC
            LIMIT $limit
            """,
            {"limit": limit},
        )
        return result.records

    # ------------------------------------------------------------------
    # 14. Conventions inference
    # ------------------------------------------------------------------

    def infer_conventions(self) -> dict[str, Any]:
        """Analyze the graph to infer project conventions and patterns."""
        # File structure patterns
        dir_stats = self.graph.execute(
            """
            MATCH (f:File)
            RETURN f.language AS language,
                   count(*) AS file_count
            """
        )

        # Component patterns
        component_stats = self.graph.execute(
            """
            MATCH (f:Function)
            WHERE f.is_component = true
            RETURN f.file_path AS file_path,
                   f.is_documentation AS is_documentation
            """
        )

        # Test patterns
        test_stats = self.graph.execute(
            """
            MATCH (f:File)
            WHERE f.is_test_file = true
            RETURN f.path AS path
            """
        )

        # Route patterns
        route_stats = self.graph.execute(
            """
            MATCH (r:Route)
            RETURN r.method AS method, count(*) AS count
            """
        )

        # Entry points (deterministic order; the projection below is capped —
        # component-heavy repos have a thousand-plus of these)
        entry_points = self.graph.execute(
            """
            MATCH (f:Function)
            WHERE f.is_entry_point = true
            RETURN f.name AS name,
                   f.file_path AS file_path,
                   f.signature AS signature
            ORDER BY f.file_path, f.name
            """
        )

        # Common import sources (most imported files)
        top_imported = self.graph.execute(
            """
            MATCH ()-[:IMPORTS]->(f:File)
            RETURN f.path AS path, count(*) AS import_count
            ORDER BY import_count DESC
            LIMIT 10
            """
        )

        # Naming patterns
        visibility_stats = self.graph.execute(
            """
            MATCH (f:Function)
            RETURN f.visibility AS visibility, count(*) AS count
            """
        )

        # Detect folder conventions from component/test locations
        component_dirs: dict[str, int] = {}
        production_components = 0
        documentation_components = 0
        for rec in component_stats.records:
            path = rec["file_path"]
            is_doc = rec.get("is_documentation")
            if is_doc:
                documentation_components += 1
            else:
                production_components += 1
            dir_part = path.rsplit("/", 1)[0] if "/" in path else ""
            # Get first 2 directory levels
            top = "/".join(dir_part.split("/")[:2])
            component_dirs[top] = component_dirs.get(top, 0) + 1

        test_dirs: dict[str, int] = {}
        for rec in test_stats.records:
            path = rec["path"]
            dir_part = path.rsplit("/", 1)[0] if "/" in path else ""
            top = "/".join(dir_part.split("/")[:2])
            test_dirs[top] = test_dirs.get(top, 0) + 1

        # Layer violations
        layer_data = self.detect_layer_violations()

        # Framework detection
        frameworks = self._detect_frameworks()

        conventions = {
            "languages": {r["language"]: r["file_count"] for r in dir_stats.records},
            "route_methods": {r["method"]: r["count"] for r in route_stats.records},
            "component_locations": dict(sorted(component_dirs.items(), key=lambda x: -x[1])[:5]),
            "production_components": production_components,
            "documentation_components": documentation_components,
            "test_locations": dict(sorted(test_dirs.items(), key=lambda x: -x[1])[:5]),
            "entry_points": [
                {"name": r["name"], "file": r["file_path"], "signature": r["signature"]} for r in entry_points.records
            ],
            "most_imported_files": [{"path": r["path"], "imports": r["import_count"]} for r in top_imported.records],
            "visibility_distribution": {r["visibility"]: r["count"] for r in visibility_stats.records},
            "layer_violations": layer_data,
            "frameworks": frameworks,
        }
        # A component-heavy repo can have 1000+ entry points (every page/component
        # is one) — the sample communicates the convention; the count carries scale.
        self._cap_list(conventions, "entry_points", cap=20)
        return conventions

    # ------------------------------------------------------------------
    # Framework detection
    # ------------------------------------------------------------------

    # Known frameworks and their npm/PyPI package names
    _FRAMEWORK_PACKAGES: dict[str, list[str]] = {
        "nextjs": ["next"],
        "react": ["react"],
        "express": ["express"],
        "hono": ["hono"],
        "fastify": ["fastify"],
        "supabase": ["@supabase/supabase-js", "supabase"],
        "clerk": ["@clerk/nextjs", "@clerk/clerk-js"],
        "nextauth": ["next-auth"],
        "stripe": ["stripe", "@stripe/stripe-js"],
        "prisma": ["prisma", "@prisma/client"],
        "drizzle": ["drizzle-orm"],
        "trpc": ["@trpc/server", "@trpc/client"],
        "tailwind": ["tailwindcss"],
        "fastapi": ["fastapi"],
        "django": ["django"],
        "flask": ["flask"],
        "vue": ["vue"],
        "angular": ["@angular/core"],
        "svelte": ["svelte"],
        "nuxt": ["nuxt"],
        "remix": ["@remix-run/react"],
        "astro": ["astro"],
    }

    def _detect_frameworks(self) -> dict[str, Any]:
        """Detect frameworks from Dependency nodes and file patterns."""
        # Get all dependency names
        deps_result = self.graph.execute("MATCH (d:Dependency) RETURN d.name AS name")
        dep_names = {r["name"] for r in deps_result.records}

        detected: dict[str, Any] = {}

        for framework, packages in self._FRAMEWORK_PACKAGES.items():
            if any(pkg in dep_names for pkg in packages):
                detected[framework] = True

        # Enrich with framework-specific conventions
        if "nextjs" in detected:
            detected["nextjs"] = self._detect_nextjs_conventions()

        if "react" in detected and "nextjs" not in detected:
            detected["react"] = self._detect_react_conventions(dep_names)
        elif "react" in detected:
            # React is present but Next.js is the primary framework
            react_info = self._detect_react_conventions(dep_names)
            if isinstance(detected.get("nextjs"), dict):
                detected["nextjs"]["react"] = react_info

        # Vibe coder stack enrichment
        if "supabase" in detected:
            detected["supabase"] = self._detect_supabase_conventions(dep_names)

        if "clerk" in detected or "nextauth" in detected:
            detected["auth"] = self._detect_auth_conventions(dep_names)

        if "prisma" in detected or "drizzle" in detected:
            detected["orm"] = self._detect_orm_conventions(dep_names)

        if "react" in detected or "nextjs" in detected:
            detected["ui"] = self._detect_ui_conventions(dep_names)

        if "stripe" in detected:
            detected["payments"] = self._detect_payment_conventions(dep_names)

        return detected

    # File-suffix variants for TS/JS source files (no leading dot).
    _TJS_EXTS = ("ts", "tsx", "js", "jsx")

    @staticmethod
    def _ends_with_any(alias: str, suffixes: tuple[str, ...]) -> str:
        """Build an ``(alias ENDS WITH 'a' OR alias ENDS WITH 'b' ...)`` predicate.

        FalkorDB has no regex match operator, so framework file-pattern checks are
        expressed as a disjunction of ENDS WITH / CONTAINS predicates instead.
        """
        return "(" + " OR ".join(f"{alias} ENDS WITH '{s}'" for s in suffixes) + ")"

    def _detect_nextjs_conventions(self) -> dict[str, Any]:
        """Detect Next.js-specific conventions from the graph."""
        info: dict[str, Any] = {}

        # App router vs Pages router
        app_files = self.graph.execute("MATCH (f:File) WHERE f.path CONTAINS '/app/' RETURN count(f) AS c")
        pages_files = self.graph.execute("MATCH (f:File) WHERE f.path CONTAINS '/pages/' RETURN count(f) AS c")
        app_count = app_files.records[0]["c"] if app_files.records else 0
        pages_count = pages_files.records[0]["c"] if pages_files.records else 0

        if app_count > 0 and pages_count > 0:
            info["router"] = "hybrid"
        elif app_count > 0:
            info["router"] = "app"
        elif pages_count > 0:
            info["router"] = "pages"
        else:
            info["router"] = "unknown"

        # Server vs client components (react_directive)
        directive_result = self.graph.execute(
            """
            MATCH (f:File)
            WHERE f.react_directive <> ''
            RETURN f.react_directive AS directive, count(f) AS c
            """
        )
        directives: dict[str, int] = {}
        for r in directive_result.records:
            directives[r["directive"]] = r["c"]
        info["use_client"] = directives.get("use client", 0)
        info["use_server"] = directives.get("use server", 0)

        # API routes: a `route.{ts,tsx,js,jsx}` file under an api/ (or app/api/) dir.
        route_files = self._ends_with_any("f.path", tuple(f"route.{e}" for e in self._TJS_EXTS))
        api_routes = self.graph.execute(
            f"""
            MATCH (f:File)
            WHERE f.path CONTAINS '/api/' AND {route_files}
            RETURN count(f) AS c
            """
        )
        info["api_routes"] = api_routes.records[0]["c"] if api_routes.records else 0

        # Middleware: a top-level `middleware.{ts,tsx,js,jsx}` file.
        mw_files = self._ends_with_any("f.path", tuple(f"middleware.{e}" for e in self._TJS_EXTS))
        middleware = self.graph.execute(f"MATCH (f:File) WHERE {mw_files} RETURN count(f) AS c")
        info["has_middleware"] = (middleware.records[0]["c"] if middleware.records else 0) > 0

        return info

    def _detect_react_conventions(self, dep_names: set[str]) -> dict[str, Any]:
        """Detect React-specific conventions."""
        info: dict[str, Any] = {}

        # State management
        state_libs = {
            "redux": ["redux", "@reduxjs/toolkit"],
            "zustand": ["zustand"],
            "jotai": ["jotai"],
            "recoil": ["recoil"],
            "mobx": ["mobx"],
            "react-query": ["@tanstack/react-query", "react-query"],
            "swr": ["swr"],
        }
        found_state = []
        for lib_name, packages in state_libs.items():
            if any(pkg in dep_names for pkg in packages):
                found_state.append(lib_name)
        if found_state:
            info["state_management"] = found_state

        # Styling approach
        style_libs = {
            "tailwind": ["tailwindcss"],
            "styled-components": ["styled-components"],
            "emotion": ["@emotion/react", "@emotion/styled"],
            "mui": ["@mui/material"],
            "chakra": ["@chakra-ui/react"],
            "ant-design": ["antd"],
            "css-modules": [],  # detected from file patterns
        }
        found_styles = []
        for lib_name, packages in style_libs.items():
            if packages and any(pkg in dep_names for pkg in packages):
                found_styles.append(lib_name)
        # Check for CSS modules via file pattern
        css_modules = self.graph.execute("MATCH (f:File) WHERE f.path ENDS WITH '.module.css' RETURN count(f) AS c")
        if css_modules.records and css_modules.records[0]["c"] > 0:
            found_styles.append("css-modules")
        if found_styles:
            info["styling"] = found_styles

        # Component style: functional vs class
        func_components = self.graph.execute("MATCH (f:Function) WHERE f.is_component = true RETURN count(f) AS c")
        class_components = self.graph.execute(
            """
            MATCH (c:Class)-[:CONTAINS]->(m:Function)
            WHERE m.name = 'render'
            RETURN count(DISTINCT c) AS c
            """
        )
        func_count = func_components.records[0]["c"] if func_components.records else 0
        class_count = class_components.records[0]["c"] if class_components.records else 0
        if func_count > 0 or class_count > 0:
            info["component_style"] = "functional" if func_count > class_count else "class"
            info["functional_components"] = func_count
            info["class_components"] = class_count

        return info

    # ------------------------------------------------------------------
    # Vibe coder stack detection
    # ------------------------------------------------------------------

    def _detect_supabase_conventions(self, dep_names: set[str]) -> dict[str, Any]:
        """Detect Supabase-specific conventions."""
        info: dict[str, Any] = {"detected": True}

        # Count files that import Supabase client
        client_usage = self.graph.execute(
            "MATCH (i:Import) WHERE i.module_path = '@supabase/supabase-js' RETURN count(DISTINCT i.file_path) AS c"
        )
        info["client_files"] = client_usage.records[0]["c"] if client_usage.records else 0

        info["uses_auth_helpers"] = "@supabase/auth-helpers-nextjs" in dep_names
        info["uses_storage"] = "@supabase/storage-js" in dep_names

        # Edge functions: `supabase/functions/<name>/index.{ts,tsx,js,jsx}`.
        index_files = self._ends_with_any("f.path", tuple(f"index.{e}" for e in self._TJS_EXTS))
        edge_funcs = self.graph.execute(
            f"MATCH (f:File) WHERE f.path CONTAINS '/supabase/functions/' AND {index_files} RETURN count(f) AS c"
        )
        info["edge_function_count"] = edge_funcs.records[0]["c"] if edge_funcs.records else 0

        return info

    def _detect_auth_conventions(self, dep_names: set[str]) -> dict[str, Any]:
        """Detect authentication provider from dependencies."""
        auth_providers = [
            ("clerk", ["@clerk/nextjs", "@clerk/clerk-js"]),
            ("nextauth", ["next-auth"]),
            ("supabase-auth", ["@supabase/auth-helpers-nextjs"]),
            ("lucia", ["lucia"]),
        ]
        for provider, packages in auth_providers:
            for pkg in packages:
                if pkg in dep_names:
                    return {"provider": provider, "package": pkg}
        return {"provider": "unknown", "package": None}

    def _detect_orm_conventions(self, dep_names: set[str]) -> dict[str, Any]:
        """Detect ORM/database client from dependencies."""
        if any(pkg in dep_names for pkg in ("prisma", "@prisma/client")):
            # Check for schema file
            schema = self.graph.execute(
                "MATCH (f:File) WHERE f.path CONTAINS 'prisma/schema.prisma' RETURN count(f) AS c"
            )
            has_schema = (schema.records[0]["c"] if schema.records else 0) > 0
            return {"orm": "prisma", "has_schema": has_schema}

        if "drizzle-orm" in dep_names:
            return {"orm": "drizzle", "has_schema": False}

        return {"orm": "unknown", "has_schema": False}

    def _detect_ui_conventions(self, dep_names: set[str]) -> dict[str, Any]:
        """Detect UI library conventions (shadcn, icons, animations)."""
        info: dict[str, Any] = {}

        # shadcn: detected via import patterns (copies files, no npm dep)
        shadcn_result = self.graph.execute(
            "MATCH (i:Import) WHERE i.module_path STARTS WITH '@/components/ui/' "
            "RETURN count(DISTINCT i.module_path) AS c"
        )
        shadcn_count = shadcn_result.records[0]["c"] if shadcn_result.records else 0
        info["uses_shadcn"] = shadcn_count > 0
        info["shadcn_component_count"] = shadcn_count

        # Icon library
        if "lucide-react" in dep_names:
            info["icon_library"] = "lucide-react"
        elif "@heroicons/react" in dep_names:
            info["icon_library"] = "@heroicons/react"
        else:
            info["icon_library"] = None

        # Animation library
        if "framer-motion" in dep_names:
            info["animation_library"] = "framer-motion"
        else:
            info["animation_library"] = None

        return info

    def _detect_payment_conventions(self, dep_names: set[str]) -> dict[str, Any]:
        """Detect payment provider from dependencies."""
        if any(pkg in dep_names for pkg in ("stripe", "@stripe/stripe-js", "@stripe/react-stripe-js")):
            return {"provider": "stripe", "package": "stripe"}

        if "@lemonsqueezy/lemonsqueezy.js" in dep_names:
            return {"provider": "lemonsqueezy", "package": "@lemonsqueezy/lemonsqueezy.js"}

        return {"provider": "unknown", "package": None}

    # ------------------------------------------------------------------
    # External service mapping
    # ------------------------------------------------------------------

    def get_external_services(self) -> dict[str, Any]:
        """Classify dependencies into service categories.

        Returns categories with matched packages, plus an uncategorized list.
        """
        deps_result = self.graph.execute("MATCH (d:Dependency) RETURN d.name AS name, d.version AS version")

        # Build lookup: package name -> (category, version)
        dep_list = [(r["name"], r.get("version") or "") for r in deps_result.records]

        # Build prefix lookup from _SERVICE_CATEGORIES for scoped packages
        # e.g. "@radix-ui/react-" prefix matches any @radix-ui/react-* package
        categories: dict[str, dict[str, Any]] = {}
        matched_names: set[str] = set()

        for cat_key, cat_info in _SERVICE_CATEGORIES.items():
            matched_packages: list[dict[str, str]] = []
            for dep_name, dep_version in dep_list:
                for pkg_pattern in cat_info["packages"]:
                    if dep_name == pkg_pattern or (pkg_pattern.endswith("*") and dep_name.startswith(pkg_pattern[:-1])):
                        matched_packages.append({"name": dep_name, "version": dep_version})
                        matched_names.add(dep_name)
                        break
            if matched_packages:
                categories[cat_key] = {
                    "label": cat_info["label"],
                    "packages": matched_packages,
                }

        uncategorized = [{"name": name, "version": version} for name, version in dep_list if name not in matched_names]

        return {"categories": categories, "uncategorized": uncategorized}

    # ------------------------------------------------------------------
    # Graph visualization subgraphs (read-only; see docs/graph-visualization-spec.md)
    # ------------------------------------------------------------------

    # Var-length traversal bounds CANNOT be query parameters in FalkorDB, so `depth`
    # is validated as an int and string-interpolated. Clamp it hard. NEVER interpolate
    # `center` or any other user input — those stay query parameters.
    _VIZ_MAX_DEPTH = 4

    # Relationships each node-link view returns (the tail's edge whitelist). The
    # traversal relationships in the heads are fixed per view; this only filters
    # which edges appear in the output. P1/P2 add more views (see the spec).
    _VIZ_VIEW_EDGE_TYPES: dict[str, list[str]] = {
        "call_hierarchy": ["CALLS"],
        "blast_radius": ["CALLS", "TESTS_FUNCTION", "HANDLES"],
        "request_trace": ["HANDLES", "CALLS", "USES_MODEL", "CALLS_RPC"],
    }

    _VIZ_LAYOUT_HINT: dict[str, str] = {
        "call_hierarchy": "dagre-tb",
        "blast_radius": "dagre-tb",
        "request_trace": "dagre-lr",
    }

    # Anchor labels a view exists to show. These are typically LOW-degree (a Route
    # has one HANDLES edge; a Model is a leaf), so plain degree-ranked truncation
    # would drop them first — gutting the view. They're force-kept before the
    # remaining node budget is filled by degree.
    _VIZ_PIN_LABELS: dict[str, set[str]] = {
        "request_trace": {"Route", "Model", "DBFunction"},
        "blast_radius": {"Route"},
        "call_hierarchy": set(),
    }

    # Per-label display-prop allowlist — keeps the payload lean. A node keeps only
    # these property keys (those that exist); unlisted labels keep all properties.
    _VIZ_PROP_ALLOWLIST: dict[str, list[str]] = {
        "Function": [
            "name",
            "qualified_name",
            "file_path",
            "start_line",
            "end_line",
            "signature",
            "complexity",
            "is_async",
            "is_entry_point",
            "entry_point_reason",
            "is_component",
            "is_test",
            "visibility",
            "return_type",
            "tested_by_count",
            "security_finding_count",
            "calls_auth",
            "has_error_handling",
        ],
        "Class": [
            "name",
            "qualified_name",
            "file_path",
            "start_line",
            "end_line",
            "kind",
            "bases",
            "is_abstract",
            "is_exported",
        ],
        "File": [
            "path",
            "language",
            "line_count",
            "is_test_file",
            "is_documentation",
            "config_type",
            "react_directive",
        ],
        "Route": ["method", "path", "handler_name", "has_auth", "middleware", "file_path", "line"],
        "Model": ["name", "orm", "table_name", "is_junction", "is_enum", "field_count", "file_path"],
        "DBFunction": ["name", "schema", "arg_count", "returns", "file_path"],
        "Variable": ["name", "kind", "value_kind", "is_exported", "file_path"],
    }

    # View-specific binding heads. Each binds a DISTINCT node set `ns` (always
    # including the center, when found) and is followed by the shared tail. `{depth}`
    # and `{route_cap}` are int-interpolated; `$center`/`$edge_types` are parameters.
    # NOTE: the center is seeded via `collect(DISTINCT f)` (same node every row ->
    # `[f]`), NOT `[f] + collect(...)`. FalkorDB raises "_AR_EXP_UpdateEntityIdx:
    # Unable to locate a value with alias f" on a bare node in a list literal mixed
    # with aggregations — keep every term an aggregation.
    _VIZ_VIEW_HEADS: dict[str, str] = {
        "call_hierarchy": """
            MATCH (f:Function) WHERE f.id = $center OR f.qualified_name = $center
            OPTIONAL MATCH (caller:Function)-[:CALLS*1..{depth}]->(f)
            OPTIONAL MATCH (f)-[:CALLS*1..{depth}]->(callee:Function)
            WITH collect(DISTINCT f) + collect(DISTINCT caller) + collect(DISTINCT callee) AS seeds
            UNWIND seeds AS n WITH collect(DISTINCT n) AS ns
        """,
        "blast_radius": """
            MATCH (target:Function) WHERE target.id = $center OR target.qualified_name = $center
            OPTIONAL MATCH (caller:Function)-[:CALLS*1..{depth}]->(target)
            OPTIONAL MATCH (tf:Function)-[:TESTS_FUNCTION]->(target)
            OPTIONAL MATCH (rt:Route)-[:HANDLES]->(target)
            WITH collect(DISTINCT target) + collect(DISTINCT caller) + collect(DISTINCT tf) + collect(DISTINCT rt) AS seeds
            UNWIND seeds AS n WITH collect(DISTINCT n) AS ns
        """,
        "request_trace": """
            MATCH (rt:Route)-[:HANDLES]->(h:Function)
            WHERE ($center IS NULL OR rt.path = $center OR rt.id = $center)
            WITH rt, h LIMIT {route_cap}
            OPTIONAL MATCH (h)-[:CALLS*0..{depth}]->(fn:Function)
            OPTIONAL MATCH (fn)-[:USES_MODEL]->(m:Model)
            OPTIONAL MATCH (fn)-[:CALLS_RPC]->(df:DBFunction)
            WITH collect(DISTINCT rt) + collect(DISTINCT h) + collect(DISTINCT fn)
                 + collect(DISTINCT m) + collect(DISTINCT df) AS seeds
            UNWIND seeds AS n WITH collect(DISTINCT n) AS ns
        """,
    }

    # Shared tail: collect edges only between bound nodes (no dangling), label via
    # labels(n)[0] (never id-prefix decoding), id = the business id property.
    _VIZ_TAIL = """
        WITH ns, [x IN ns | x.id] AS ids
        UNWIND ns AS a
        OPTIONAL MATCH (a)-[r]->(b)
        WHERE b.id IN ids AND type(r) IN $edge_types
        WITH ns, collect(DISTINCT {
               source: a.id, target: b.id, type: type(r),
               resolution: r.resolution, access: r.access, context: r.context
             }) AS raw_edges
        RETURN [x IN ns | {id: x.id, label: labels(x)[0], props: properties(x)}] AS nodes,
               [e IN raw_edges WHERE e.target IS NOT NULL] AS edges
    """

    def get_subgraph(
        self,
        view: str,
        center: str | None = None,
        depth: int = 2,
        edge_types: list[str] | None = None,
        limit: int | None = None,
        models_only: bool = False,
    ) -> dict[str, Any]:
        """Return a ``{meta, nodes, edges}`` subgraph for a code-visualization VIEW.

        Read-only over existing node/edge types. ``center`` accepts a business id
        (``func::…``) or a ``qualified_name``/``path``. The payload is capped at
        ``limit`` (default ``GRISTLE_VIZ_MAX_NODES``); over the cap the lowest-degree
        nodes are dropped and ``meta.truncated`` is set.

        ``models_only`` (request_trace only): prune the handler call-closure to just
        the nodes on a path to a data-layer node — a ``Model`` (table/view) or a
        ``DBFunction`` (stored procedure) — the "route → DB" view. Default ``False``
        keeps the full closure (every function the handler calls, data nodes
        highlighted), which stays honest when a ``USES_MODEL``/``CALLS_RPC`` edge is
        unresolved.
        """
        if not isinstance(depth, int) or isinstance(depth, bool):
            return {"error": "depth must be an integer"}
        if view not in self._VIZ_VIEW_HEADS:
            return {"error": f"Unknown or unsupported view '{view}'. Available: {sorted(self._VIZ_VIEW_HEADS)}"}

        node_limit = limit if limit is not None else settings.viz_max_nodes
        depth = max(1, min(depth, self._VIZ_MAX_DEPTH))
        et = edge_types if edge_types is not None else self._VIZ_VIEW_EDGE_TYPES[view]

        head = self._VIZ_VIEW_HEADS[view].format(depth=depth, route_cap=node_limit)
        result = self.graph.execute(head + self._VIZ_TAIL, {"center": center, "edge_types": et})

        if not result.records:
            nodes: list[dict[str, Any]] = []
            edges: list[dict[str, Any]] = []
        else:
            rec = result.records[0]
            nodes = list(rec.get("nodes") or [])
            edges = list(rec.get("edges") or [])

        if models_only and view == "request_trace":
            nodes, edges = self._viz_prune_to_model_paths(nodes, edges)

        return self._viz_finalize(view, center, depth, et, node_limit, nodes, edges, models_only=models_only)

    @staticmethod
    def _viz_prune_to_model_paths(
        nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Keep only nodes on a path to a data endpoint (request_trace ``models_only``).

        A node is kept iff it can reach a ``Model`` or a ``DBFunction`` by following
        edges forward -- i.e. it is reverse-reachable from a data-layer node. Drops
        handler-closure branches (validators, mappers, plumbing) that never touch
        the DB. Returns ``([], [])`` when neither is present (an honest "no resolved
        DB path").
        """
        from collections import deque

        model_ids = {n["id"] for n in nodes if n.get("label") in ("Model", "DBFunction")}
        if not model_ids:
            return [], []
        reverse: dict[str, list[str]] = {}
        for e in edges:
            reverse.setdefault(e["target"], []).append(e["source"])
        keep = set(model_ids)
        queue = deque(model_ids)
        while queue:
            for src in reverse.get(queue.popleft(), []):
                if src not in keep:
                    keep.add(src)
                    queue.append(src)
        nodes = [n for n in nodes if n["id"] in keep]
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
        return nodes, edges

    def _viz_finalize(
        self,
        view: str,
        center: str | None,
        depth: int,
        edge_types: list[str],
        limit: int,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        models_only: bool = False,
    ) -> dict[str, Any]:
        """Trim props to the allowlist, truncate to ``limit`` by degree, build meta."""
        from gristle import __version__

        # Trim each node's props to its label's allowlist (unlisted labels untouched).
        for n in nodes:
            allow = self._VIZ_PROP_ALLOWLIST.get(n.get("label", ""))
            if allow is not None and isinstance(n.get("props"), dict):
                n["props"] = {k: v for k, v in n["props"].items() if k in allow}

        truncated = False
        if len(nodes) > limit:
            truncated = True
            degree: dict[str, int] = {n["id"]: 0 for n in nodes}
            for e in edges:
                if e.get("source") in degree:
                    degree[e["source"]] += 1
                if e.get("target") in degree:
                    degree[e["target"]] += 1
            keep: set[str] = set()
            center_id = self._viz_center_id(center, nodes)
            if center_id is not None:
                keep.add(center_id)
            ranked = sorted(nodes, key=lambda nd: degree.get(nd["id"], 0), reverse=True)
            # Force-keep the view's low-degree anchors (Routes/Models) first, then
            # fill the remaining budget with the highest-degree nodes.
            pin_labels = self._VIZ_PIN_LABELS.get(view, set())
            for n in ranked:
                if len(keep) >= limit:
                    break
                if n.get("label") in pin_labels:
                    keep.add(n["id"])
            for n in ranked:
                if len(keep) >= limit:
                    break
                keep.add(n["id"])
            nodes = [n for n in nodes if n["id"] in keep]
            edges = [e for e in edges if e.get("source") in keep and e.get("target") in keep]

        # Drop null edge-metadata keys so the payload only carries what applies.
        for e in edges:
            for k in ("resolution", "access", "context"):
                if e.get(k) is None:
                    e.pop(k, None)

        return {
            "meta": {
                "view": view,
                "kind": "node_link",
                "repo_id": self.graph.repo_id,
                "center": center,
                "depth": depth,
                "edge_types": edge_types,
                "models_only": models_only,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "truncated": truncated,
                "limit": limit,
                "layout_hint": self._VIZ_LAYOUT_HINT[view],
                "generated_with": f"gristle {__version__}",
            },
            "nodes": nodes,
            "edges": edges,
        }

    @staticmethod
    def _viz_center_id(center: str | None, nodes: list[dict[str, Any]]) -> str | None:
        """Find the node id matching the requested center (by id, qualified_name, or path)."""
        if center is None:
            return None
        for n in nodes:
            props = n.get("props") or {}
            if n.get("id") == center or props.get("qualified_name") == center or props.get("path") == center:
                return str(n["id"])
        return None

    # ------------------------------------------------------------------
    # Layer violation detection
    # ------------------------------------------------------------------

    # Default layer hierarchy: directory name -> (layer_number, layer_name)
    _DEFAULT_LAYERS: dict[str, tuple[int, str]] = {
        "routes": (3, "presentation"),
        "pages": (3, "presentation"),
        "handlers": (3, "presentation"),
        "views": (3, "presentation"),
        "services": (2, "business"),
        "usecases": (2, "business"),
        "logic": (2, "business"),
        "adapters": (1, "data"),
        "repositories": (1, "data"),
        "db": (1, "data"),
        "database": (1, "data"),
        "utils": (0, "cross-cutting"),
        "shared": (0, "cross-cutting"),
        "lib": (0, "cross-cutting"),
        "common": (0, "cross-cutting"),
        "helpers": (0, "cross-cutting"),
    }

    def _classify_layer(
        self, file_path: str, layer_config: dict[str, tuple[int, str]] | None = None
    ) -> tuple[int, str] | None:
        """Classify a file's architectural layer based on its directory path."""
        layers = layer_config or self._DEFAULT_LAYERS
        parts = file_path.replace("\\", "/").split("/")
        # Check each directory component (deepest match wins)
        for part in reversed(parts[:-1]):  # exclude filename
            lower = part.lower()
            if lower in layers:
                return layers[lower]
        return None

    def detect_layer_violations(self, layer_config: dict[str, tuple[int, str]] | None = None) -> dict[str, Any]:
        """Detect architectural layer violations from IMPORTS edges.

        A violation occurs when a file in a higher layer imports from a
        non-adjacent lower layer (skipping layers), e.g. presentation → data.
        Cross-cutting (layer 0) files are exempt from violations.
        """
        # Get all file-to-file imports
        result = self.graph.execute("""
            MATCH (a:File)-[:IMPORTS]->(b:File)
            RETURN a.path AS source, b.path AS target
        """)

        violations: list[dict[str, Any]] = []
        layer_summary: dict[str, int] = {}

        for rec in result.records:
            source_path = rec["source"]
            target_path = rec["target"]

            source_layer = self._classify_layer(source_path, layer_config)
            target_layer = self._classify_layer(target_path, layer_config)

            if source_layer is None or target_layer is None:
                continue

            src_num, src_name = source_layer
            tgt_num, tgt_name = target_layer

            # Cross-cutting (layer 0) is exempt
            if tgt_num == 0 or src_num == 0:
                continue

            # Violation: higher layer imports from non-adjacent lower layer
            if src_num > tgt_num + 1:
                violation_type = f"{src_name}→{tgt_name}"
                violations.append(
                    {
                        "source": source_path,
                        "target": target_path,
                        "source_layer": src_name,
                        "target_layer": tgt_name,
                        "source_level": src_num,
                        "target_level": tgt_num,
                        "violation_type": violation_type,
                    }
                )
                layer_summary[violation_type] = layer_summary.get(violation_type, 0) + 1

        return {
            "violations": violations,
            "total": len(violations),
            "by_type": layer_summary,
        }

    # ------------------------------------------------------------------
    # 15. Dependencies
    # ------------------------------------------------------------------

    def get_dependencies(self, limit: int = 50) -> list[dict[str, Any]]:
        """List external dependencies with usage counts."""
        result = self.graph.execute(
            """
            MATCH (d:Dependency)
            OPTIONAL MATCH (f:File)-[:DEPENDS_ON]->(d)
            OPTIONAL MATCH (fn:Function)-[:USES_DEPENDENCY]->(d)
            RETURN d.name AS name,
                   d.import_count AS file_count,
                   count(DISTINCT fn) AS function_count
            ORDER BY function_count DESC, file_count DESC
            LIMIT $limit
            """,
            {"limit": limit},
        )
        return result.records

    def get_dependency_users(self, dep_name: str) -> dict[str, Any]:
        """Get all functions and files that use a specific dependency."""
        # Files that depend on it
        files = self.graph.execute(
            """
            MATCH (f:File)-[:DEPENDS_ON]->(d:Dependency {name: $name})
            RETURN f.path AS file_path
            ORDER BY f.path
            """,
            {"name": dep_name},
        )
        # Functions that use it
        funcs = self.graph.execute(
            """
            MATCH (fn:Function)-[:USES_DEPENDENCY]->(d:Dependency {name: $name})
            RETURN fn.name AS name,
                   fn.qualified_name AS qualified_name,
                   fn.file_path AS file_path,
                   fn.start_line AS start_line,
                   fn.is_test AS is_test
            ORDER BY fn.file_path, fn.start_line
            """,
            {"name": dep_name},
        )
        # Dependency health info
        dep_info = self.graph.execute(
            """
            MATCH (d:Dependency {name: $name})
            RETURN d.version AS version,
                   d.latest_version AS latest_version,
                   d.is_outdated AS is_outdated,
                   d.vulnerability_count AS vulnerability_count,
                   d.vulnerabilities AS vulnerabilities
            """,
            {"name": dep_name},
        )
        health = dep_info.records[0] if dep_info.records else {}

        return {
            "dependency": dep_name,
            "version": health.get("version", ""),
            "latest_version": health.get("latest_version", ""),
            "is_outdated": health.get("is_outdated", False),
            "vulnerability_count": health.get("vulnerability_count", 0),
            "vulnerabilities": health.get("vulnerabilities", []),
            "files": [r["file_path"] for r in files.records],
            "functions": funcs.records,
            "file_count": len(files.records),
            "function_count": len(funcs.records),
        }

    # ------------------------------------------------------------------
    # Trace path between entities
    # ------------------------------------------------------------------

    def find_path(self, from_name: str, to_name: str, max_hops: int = 5) -> list[dict[str, Any]]:
        """Find call paths between two entities."""
        result = self.graph.execute(
            f"""
            MATCH path = (start:Function)-[:CALLS*1..{max_hops}]->(end:Function)
            WHERE (start.name = $from_name OR start.qualified_name = $from_name)
              AND (end.name = $to_name OR end.qualified_name = $to_name)
            RETURN [node in nodes(path) | node.qualified_name] AS path,
                   length(path) AS hops
            ORDER BY hops
            LIMIT 5
            """,
            {"from_name": from_name, "to_name": to_name},
        )
        return result.records

    # ------------------------------------------------------------------
    # Config & environment queries
    # ------------------------------------------------------------------

    def get_env_vars(self) -> dict[str, Any]:
        """List all environment variables with their sources and usage."""
        # Get all EnvVar nodes with their definitions and usage
        env_result = self.graph.execute("""
            MATCH (e:EnvVar)
            OPTIONAL MATCH (e)-[:DEFINED_IN]->(def_file:File)
            OPTIONAL MATCH (src:File)-[:USES_ENV]->(e)
            RETURN e.name AS name,
                   e.default_value AS default_value,
                   e.required AS required,
                   collect(DISTINCT def_file.path) AS defined_in,
                   collect(DISTINCT src.path) AS used_by
            ORDER BY e.name
        """)
        return {
            "env_vars": env_result.records,
            "total": len(env_result.records),
        }

    def get_config_files(self) -> dict[str, Any]:
        """List all config files with their types and properties."""
        result = self.graph.execute("""
            MATCH (f:File)
            WHERE f.config_type IS NOT NULL
            RETURN f.path AS path,
                   f.config_type AS config_type,
                   f.line_count AS line_count
            ORDER BY f.config_type, f.path
        """)
        return {
            "config_files": result.records,
            "total": len(result.records),
        }

    def get_setup_requirements(self) -> dict[str, Any]:
        """Gather setup requirements: env vars, config files, dependencies."""
        env_data = self.get_env_vars()
        config_data = self.get_config_files()

        # Required env vars (defined in .env templates or marked required)
        required = [e for e in env_data["env_vars"] if e.get("required")]
        optional = [e for e in env_data["env_vars"] if not e.get("required")]

        # Get dependency count
        dep_result = self.graph.execute("MATCH (d:Dependency) RETURN count(d) AS total")
        dep_count = dep_result.records[0]["total"] if dep_result.records else 0

        setup = {
            "required_env_vars": required,
            "optional_env_vars": optional,
            "config_files": config_data["config_files"],
            "dependency_count": dep_count,
        }
        self._cap_list(setup, "required_env_vars", cap=50)
        self._cap_list(setup, "optional_env_vars", cap=50)
        return setup

    # ------------------------------------------------------------------
    # Type flow analysis
    # ------------------------------------------------------------------

    def get_data_contract(self, name: str) -> dict[str, Any] | None:
        """Get the input/output data contract for a function.

        Returns the types a function accepts as parameters and returns,
        including the fields of those types.
        """
        # Get function info
        func_result = self.graph.execute(
            """
            MATCH (f:Function)
            WHERE f.name = $name OR f.qualified_name = $name
            RETURN f.qualified_name AS qualified_name,
                   f.signature AS signature
            """,
            {"name": name},
        )
        if not func_result.records:
            return None

        func_rec = func_result.records[0]

        # Get return types (RETURNS edges)
        returns_result = self.graph.execute(
            """
            MATCH (f:Function)-[:RETURNS]->(t:Class)
            WHERE f.name = $name OR f.qualified_name = $name
            RETURN t.name AS type_name,
                   t.qualified_name AS type_qname,
                   t.kind AS kind
            """,
            {"name": name},
        )

        # Get accepted types (ACCEPTS edges)
        accepts_result = self.graph.execute(
            """
            MATCH (f:Function)-[a:ACCEPTS]->(t:Class)
            WHERE f.name = $name OR f.qualified_name = $name
            RETURN a.param_name AS param_name,
                   t.name AS type_name,
                   t.qualified_name AS type_qname,
                   t.kind AS kind
            """,
            {"name": name},
        )

        # Fetch fields for each type referenced
        type_qnames = set()
        for r in returns_result.records:
            type_qnames.add(r["type_qname"])
        for r in accepts_result.records:
            type_qnames.add(r["type_qname"])

        type_fields: dict[str, list[dict[str, Any]]] = {}
        for tqn in type_qnames:
            fields_result = self.graph.execute(
                """
                MATCH (t:Class {qualified_name: $qn})-[:HAS_FIELD]->(tf:TypeField)
                RETURN tf.name AS name,
                       tf.type_annotation AS type_annotation,
                       tf.is_optional AS is_optional
                ORDER BY tf.name
                """,
                {"qn": tqn},
            )
            type_fields[tqn] = [
                {
                    "name": r["name"],
                    "type": r["type_annotation"],
                    "optional": r["is_optional"],
                }
                for r in fields_result.records
            ]

        # Build inputs
        inputs = []
        for r in accepts_result.records:
            inputs.append(
                {
                    "param_name": r["param_name"],
                    "type": r["type_name"],
                    "qualified_name": r["type_qname"],
                    "kind": r["kind"],
                    "fields": type_fields.get(r["type_qname"], []),
                }
            )

        # Build output
        output = None
        if returns_result.records:
            r = returns_result.records[0]
            output = {
                "type": r["type_name"],
                "qualified_name": r["type_qname"],
                "kind": r["kind"],
                "fields": type_fields.get(r["type_qname"], []),
            }

        return {
            "entity": func_rec["qualified_name"],
            "signature": func_rec["signature"],
            "inputs": inputs,
            "output": output,
        }

    def get_type_usage(self, name: str) -> dict[str, Any] | None:
        """Find all usage of a type across the codebase.

        Shows functions that accept or return this type, and other types
        that reference it in their fields.
        """
        # Get the type itself
        type_result = self.graph.execute(
            """
            MATCH (t:Class)
            WHERE t.name = $name OR t.qualified_name = $name
            RETURN t.name AS name,
                   t.qualified_name AS qualified_name,
                   t.kind AS kind,
                   t.file_path AS file_path
            """,
            {"name": name},
        )
        if not type_result.records:
            return None

        type_rec = type_result.records[0]

        # Get the type's own fields
        fields_result = self.graph.execute(
            """
            MATCH (t:Class)-[:HAS_FIELD]->(tf:TypeField)
            WHERE t.name = $name OR t.qualified_name = $name
            RETURN tf.name AS name,
                   tf.type_annotation AS type_annotation,
                   tf.is_optional AS is_optional
            ORDER BY tf.name
            """,
            {"name": name},
        )

        # Functions that accept this type
        accepted_by = self.graph.execute(
            """
            MATCH (f:Function)-[a:ACCEPTS]->(t:Class)
            WHERE t.name = $name OR t.qualified_name = $name
            RETURN f.qualified_name AS function,
                   f.file_path AS file_path,
                   a.param_name AS param_name
            ORDER BY f.file_path, f.qualified_name
            """,
            {"name": name},
        )

        # Functions that return this type
        returned_by = self.graph.execute(
            """
            MATCH (f:Function)-[:RETURNS]->(t:Class)
            WHERE t.name = $name OR t.qualified_name = $name
            RETURN f.qualified_name AS function,
                   f.file_path AS file_path
            ORDER BY f.file_path, f.qualified_name
            """,
            {"name": name},
        )

        # Types that reference this type in their fields
        referenced_in = self.graph.execute(
            """
            MATCH (parent:Class)-[:HAS_FIELD]->(tf:TypeField)
            WHERE tf.type_annotation CONTAINS $name
              AND parent.name <> $name
              AND parent.qualified_name <> $name
            RETURN parent.qualified_name AS parent_type,
                   tf.name AS field_name,
                   tf.type_annotation AS field_type
            ORDER BY parent_type
            """,
            {"name": name},
        )

        return {
            "type": type_rec["name"],
            "qualified_name": type_rec["qualified_name"],
            "kind": type_rec["kind"],
            "file_path": type_rec["file_path"],
            "fields": [
                {
                    "name": r["name"],
                    "type": r["type_annotation"],
                    "optional": r["is_optional"],
                }
                for r in fields_result.records
            ],
            "accepted_by": [
                {
                    "function": r["function"],
                    "file_path": r["file_path"],
                    "param_name": r["param_name"],
                }
                for r in accepted_by.records
            ],
            "returned_by": [
                {
                    "function": r["function"],
                    "file_path": r["file_path"],
                }
                for r in returned_by.records
            ],
            "referenced_in_fields": [
                {
                    "parent_type": r["parent_type"],
                    "field_name": r["field_name"],
                    "field_type": r["field_type"],
                }
                for r in referenced_in.records
            ],
        }

    # ------------------------------------------------------------------
    # Code quality analysis
    # ------------------------------------------------------------------

    def detect_dead_exports(self) -> dict[str, Any]:
        """Find exported entities that are never imported by other files.

        Excludes entry points (they're meant to be external-facing).
        Useful for identifying unused public API surface.
        """
        # NB: FalkorDB does not support the EXISTS { subquery } form. Use an
        # OPTIONAL MATCH anti-join: an export is dead when no *other* file imports
        # its file (self-imports excluded via the path inequality).
        query = """
        MATCH (file:File)-[:EXPORTS]->(entity)
        WHERE NOT entity.is_entry_point
          AND file.is_documentation <> true
        OPTIONAL MATCH (importer:File)-[:IMPORTS]->(file)
        WHERE importer.path <> file.path
        WITH file, entity, count(importer) AS importers
        WHERE importers = 0
        RETURN entity.qualified_name AS qualified_name,
               entity.name AS name,
               file.path AS file,
               labels(entity)[0] AS type
        ORDER BY file.path, entity.name
        """
        result = self.graph.execute(query)
        exports = [
            {
                "qualified_name": r["qualified_name"],
                "name": r["name"],
                "file": r["file"],
                "type": r["type"],
            }
            for r in result.records
        ]
        report: dict[str, Any] = {"total": len(exports), "dead_exports": exports}
        self._cap_list(report, "dead_exports", cap=50)
        return report

    def detect_import_cycles(self, max_length: int = 10) -> dict[str, Any]:
        """Find all import cycles up to max_length.

        Returns cycles as file path lists, grouped by length.
        Cycles are deduplicated (a→b→a is same as b→a→b).
        """
        # Variable-length bounds CANNOT be query parameters in FalkorDB, so the
        # validated int is string-interpolated (not passed via params).
        max_len = max(1, int(max_length))
        query = f"""
        MATCH path = (a:File)-[:IMPORTS*1..{max_len}]->(a)
        WHERE ALL(r IN relationships(path) WHERE type(r) = 'IMPORTS')
        WITH [n IN nodes(path) | n.path] AS cycle_files,
             length(path) AS cycle_length
        RETURN cycle_files, cycle_length
        ORDER BY cycle_length ASC, cycle_files[0] ASC
        """
        result = self.graph.execute(query)

        cycles = []
        by_length: dict[int, int] = {}
        seen_cycles: set[str] = set()

        for r in result.records:
            files = r["cycle_files"]
            length = r["cycle_length"]

            # Normalize cycle for deduplication (rotate to start with lexicographically smallest)
            min_idx = files.index(min(files[:-1]))  # Exclude last (duplicate of first)
            normalized = tuple(files[min_idx:-1] + files[:min_idx] + [files[min_idx]])
            cycle_key = str(normalized)

            if cycle_key not in seen_cycles:
                seen_cycles.add(cycle_key)
                cycles.append({"files": files, "length": length})
                by_length[length] = by_length.get(length, 0) + 1

        return {
            "total": len(cycles),
            "cycles": cycles,
            "by_length": by_length,
        }

    def get_public_api(self, include_internal: bool = False) -> dict[str, Any]:
        """Return all public API entities (exported, non-test, non-internal).

        Args:
            include_internal: If True, include entities in paths containing 'internal', '__', or '_private'.

        Returns dict with:
            - total: count of public API entities
            - entities: list of {qualified_name, name, file, type, has_docs}
            - by_type: counts grouped by entity type (Function, Class)
            - by_file: counts grouped by file
        """
        internal_filter = ""
        if not include_internal:
            internal_filter = """
            AND NOT file.path CONTAINS '__'
            AND NOT file.path CONTAINS '/internal/'
            AND NOT file.path CONTAINS '/_'
            """

        query = f"""
        MATCH (file:File)-[:EXPORTS]->(entity)
        WHERE NOT file.is_test_file
          AND file.is_documentation <> true
          AND entity.visibility = 'public'
          {internal_filter}
        RETURN entity.qualified_name AS qualified_name,
               entity.name AS name,
               file.path AS file,
               labels(entity)[0] AS type,
               entity.docstring AS docstring
        ORDER BY type, entity.name
        """
        result = self.graph.execute(query)

        entities = []
        by_type: dict[str, int] = {}
        by_file: dict[str, int] = {}
        documented_count = 0

        for r in result.records:
            has_docs = bool(r["docstring"])
            if has_docs:
                documented_count += 1

            entity = {
                "qualified_name": r["qualified_name"],
                "name": r["name"],
                "file": r["file"],
                "type": r["type"],
                "has_docs": has_docs,
            }
            entities.append(entity)
            by_type[r["type"]] = by_type.get(r["type"], 0) + 1
            by_file[r["file"]] = by_file.get(r["file"], 0) + 1

        doc_percentage = int(documented_count / len(entities) * 100) if entities else 0

        return {
            "total": len(entities),
            "entities": entities,
            "by_type": by_type,
            "by_file": by_file,
            "documented_count": documented_count,
            "doc_percentage": doc_percentage,
        }

    # ------------------------------------------------------------------
    # Security analysis
    # ------------------------------------------------------------------

    def detect_security_issues(self) -> dict[str, Any]:
        """Find functions with security findings (unsafe calls, secrets, SQL injection, LLM risks).

        Returns findings grouped by category with file/line details.
        """
        result = self.graph.execute(
            """
            MATCH (fn:Function)-[:DEFINED_IN]->(file:File)
            WHERE fn.security_finding_count > 0
            RETURN fn.qualified_name AS qualified_name,
                   fn.name AS name,
                   file.path AS file,
                   fn.start_line AS line,
                   fn.security_findings AS findings,
                   fn.security_finding_count AS count
            ORDER BY fn.security_finding_count DESC
            """
        )

        all_findings = []
        by_category: dict[str, int] = {}

        for r in result.records:
            all_findings.append(
                {
                    "function": r["qualified_name"],
                    "name": r["name"],
                    "file": r["file"],
                    "line": r["line"],
                    "findings": r["findings"],
                    "count": r["count"],
                }
            )
            for tag in r["findings"]:
                cat = tag.split(":")[0] if ":" in tag else tag
                by_category[cat] = by_category.get(cat, 0) + 1

        return {
            "total": len(all_findings),
            "by_category": by_category,
            "findings": all_findings,
        }

    def detect_unauthenticated_routes(self) -> dict[str, Any]:
        """Find routes whose handlers lack auth decorators or middleware.

        Uses heuristics: checks for common auth-related decorator names
        and middleware presence. Routes without any auth indicator are flagged.
        """
        # A route counts as authenticated if it has an auth decorator/middleware,
        # OR its handler body calls an auth/session helper (fn.calls_auth — e.g.
        # Next.js inline `auth()`), OR a function it calls (<=2 hops) does.
        result = self.graph.execute(
            """
            MATCH (r:Route)-[:HANDLES]->(fn:Function)
            MATCH (fn)-[:DEFINED_IN]->(file:File)
            OPTIONAL MATCH (fn)-[:CALLS*1..2]->(ac:Function)
            WHERE coalesce(ac.calls_auth, false) = true OR toLower(ac.name) CONTAINS 'auth'
            WITH r, fn, file, count(ac) AS auth_callees
            WHERE size(r.middleware) = 0
              AND coalesce(fn.calls_auth, false) = false
              AND auth_callees = 0
              AND NOT ANY(d IN fn.decorators WHERE
                d CONTAINS 'auth' OR d CONTAINS 'login_required' OR
                d CONTAINS 'permission' OR d CONTAINS 'protect' OR
                d CONTAINS 'jwt' OR d CONTAINS 'token' OR
                d CONTAINS 'requires_auth' OR d CONTAINS 'authenticated' OR
                d CONTAINS 'verify')
            RETURN r.method AS method,
                   r.path AS path,
                   fn.name AS handler,
                   fn.qualified_name AS qualified_name,
                   fn.decorators AS decorators,
                   file.path AS file
            ORDER BY file.path, r.path
            """
        )

        routes = [
            {
                "method": r["method"],
                "path": r["path"],
                "handler": r["handler"],
                "qualified_name": r["qualified_name"],
                "decorators": r["decorators"],
                "file": r["file"],
            }
            for r in result.records
        ]

        return {"total": len(routes), "unauthenticated_routes": routes}

    def get_outdated_dependencies(self, severity: str = "all") -> dict[str, Any]:
        """Find outdated dependencies with optional vulnerability filtering.

        severity: "all" (all outdated), "vulnerable" (CVEs only), "safe" (outdated, no CVEs)
        """
        result = self.graph.execute(
            """
            MATCH (d:Dependency)
            WHERE d.is_outdated = true
            OPTIONAL MATCH (f:File)-[:DEPENDS_ON]->(d)
            RETURN d.name AS name,
                   d.version AS declared_version,
                   d.latest_version AS latest_version,
                   d.vulnerability_count AS vulnerability_count,
                   d.vulnerabilities AS vulnerabilities,
                   d.checked_at AS checked_at,
                   count(DISTINCT f) AS file_count
            ORDER BY d.vulnerability_count DESC, file_count DESC
            """,
        )
        outdated = result.records

        if severity == "vulnerable":
            outdated = [r for r in outdated if (r.get("vulnerability_count") or 0) > 0]
        elif severity == "safe":
            outdated = [r for r in outdated if (r.get("vulnerability_count") or 0) == 0]

        vuln_count = sum(1 for r in result.records if (r.get("vulnerability_count") or 0) > 0)

        return {
            "total": len(outdated),
            "outdated": outdated,
            "vulnerable_count": vuln_count,
            "summary": {"total_outdated": len(result.records), "with_vulnerabilities": vuln_count},
        }

    def get_security_overview(self) -> dict[str, Any]:
        """Combined security overview: code findings + unauthenticated routes + vulnerable deps."""
        code_findings = self.detect_security_issues()
        unauth_routes = self.detect_unauthenticated_routes()
        vuln_deps = self.get_outdated_dependencies(severity="vulnerable")

        return {
            "total_issues": code_findings["total"] + unauth_routes["total"] + vuln_deps["total"],
            "code_findings": code_findings,
            "unauthenticated_routes": unauth_routes,
            "vulnerable_dependencies": vuln_deps,
        }

    # ------------------------------------------------------------------
    # Changelog / snapshot queries
    # ------------------------------------------------------------------

    def get_changelog(self) -> dict[str, Any]:
        """Show what changed since last ingestion by diffing Snapshot nodes."""
        # RETURN properties(s) (a map), not the raw node -- FalkorDB Node objects
        # have no .get(), so the metric lookups below would raise on a bare node.
        result = self.graph.execute(
            "MATCH (s:Snapshot) WITH s ORDER BY s.captured_at DESC LIMIT 2 RETURN properties(s) AS s"
        )
        snapshots = result.records
        if not snapshots:
            return {"status": "no_snapshots", "message": "No snapshots found. Run gristle_ingest first."}

        _METRICS = [
            "file_count",
            "function_count",
            "class_count",
            "route_count",
            "test_count",
            "component_count",
            "dependency_count",
            "edge_count",
        ]

        current = snapshots[0]["s"]
        if len(snapshots) < 2:
            return {
                "status": "first_ingestion",
                "current": {m: current.get(m, 0) for m in _METRICS},
                "files": None,
                "captured_at": current.get("captured_at", ""),
            }

        previous = snapshots[1]["s"]
        changes: dict[str, dict[str, int]] = {}
        summary_parts: list[str] = []

        for m in _METRICS:
            before = previous.get(m, 0) or 0
            after = current.get(m, 0) or 0
            delta = after - before
            changes[m] = {"before": before, "after": after, "delta": delta}
            if delta != 0:
                label = m.replace("_count", "").replace("_", " ") + "s"
                verb = "Added" if delta > 0 else "Removed"
                summary_parts.append(f"{verb} {abs(delta)} {label}")

        # File-level diffs (when file_paths_json is available on snapshots)
        files: dict[str, list[str]] | None = None
        prev_paths_json = previous.get("file_paths_json")
        curr_paths_json = current.get("file_paths_json")
        if prev_paths_json and curr_paths_json:
            prev_files = set(json.loads(prev_paths_json))
            curr_files = set(json.loads(curr_paths_json))
            files = {
                "added": sorted(curr_files - prev_files),
                "removed": sorted(prev_files - curr_files),
            }

        return {
            "status": "diff",
            "changes": changes,
            "files": files,
            "summary": ". ".join(summary_parts) + "." if summary_parts else "No changes.",
            "current_captured_at": current.get("captured_at", ""),
            "previous_captured_at": previous.get("captured_at", ""),
        }

    def get_snapshot_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return all snapshots ordered by date."""
        # properties(s), not the raw node -- otherwise the records carry FalkorDB
        # Node objects that don't serialize cleanly to JSON for MCP consumers.
        result = self.graph.execute(
            "MATCH (s:Snapshot) WITH s ORDER BY s.captured_at DESC LIMIT $limit RETURN properties(s) AS s",
            {"limit": limit},
        )
        return [r["s"] for r in result.records]

    # ------------------------------------------------------------------
    # Schema / model queries
    # ------------------------------------------------------------------

    def get_models(self) -> dict:
        """List all database models (excluding enums) with fields and relationships.

        The list view is capped for agent consumption: at most ``_MODELS_CAP``
        models (``models_omitted`` gives the cut, ``count`` stays exact) with at
        most ``_MODEL_INLINE_CAP`` inline fields/relations each (``fieldCount``
        carries the full field count; ``get_model_detail`` has everything).
        """
        result = self.graph.execute(
            """
            MATCH (m:Model)
            WHERE NOT m.is_enum
            OPTIONAL MATCH (m)-[:HAS_MODEL_FIELD]->(f:ModelField)
            WITH m, collect({
                name: f.name,
                fieldType: f.field_type,
                dbType: f.db_type,
                isPrimaryKey: f.is_primary_key,
                isNullable: f.is_nullable,
                isUnique: f.is_unique,
                isForeignKey: f.is_foreign_key,
                referencesModel: f.references_model
            }) AS fields
            OPTIONAL MATCH (m)-[r:RELATED_TO]->(other:Model)
            WITH m, fields, collect({
                targetModel: other.name,
                relationType: r.relation_type,
                foreignKeyField: r.foreign_key_field,
                throughModel: r.through_model
            }) AS relations
            RETURN m.name AS name, m.orm AS orm, m.table_name AS tableName,
                   m.file_path AS filePath, m.field_count AS fieldCount,
                   m.primary_key AS primaryKey, m.is_junction AS isJunction,
                   m.is_enum AS isEnum, fields, relations
            ORDER BY m.name
            """
        )
        for record in result.records:
            self._non_null_maps(record, "fields", "name")
            self._non_null_maps(record, "relations", "targetModel")
            # The list view is a map, not the full shape: drop null/false field
            # attributes here (get_model_detail returns every attribute).
            record["fields"] = [
                {k: v for k, v in f.items() if v is not None and v is not False} for f in record["fields"]
            ]
            # RELATED_TO props were coerced to "" at write time (FalkorDB cannot
            # MERGE nulls) — drop those along with nulls.
            record["relations"] = [{k: v for k, v in r.items() if v not in (None, "")} for r in record["relations"]]
            self._cap_list(record, "fields", self._MODEL_INLINE_CAP)
            self._cap_list(record, "relations", self._MODEL_INLINE_CAP)
        report = {"models": result.records, "count": len(result.records)}
        self._cap_list(report, "models", self._MODELS_CAP)
        return report

    def get_model_detail(self, model_name: str) -> dict:
        """Get detailed information about a specific database model."""
        result = self.graph.execute(
            """
            MATCH (m:Model {name: $name})
            OPTIONAL MATCH (m)-[:HAS_MODEL_FIELD]->(f:ModelField)
            WITH m, collect({
                name: f.name,
                fieldType: f.field_type,
                dbType: f.db_type,
                isPrimaryKey: f.is_primary_key,
                isNullable: f.is_nullable,
                isUnique: f.is_unique,
                isIndexed: f.is_indexed,
                hasDefault: f.has_default,
                defaultValue: f.default_value,
                isForeignKey: f.is_foreign_key,
                referencesModel: f.references_model,
                referencesField: f.references_field,
                line: f.line
            }) AS fields
            OPTIONAL MATCH (m)-[out:RELATED_TO]->(outModel:Model)
            WITH m, fields, collect({
                targetModel: outModel.name,
                relationType: out.relation_type,
                foreignKeyField: out.foreign_key_field,
                throughModel: out.through_model
            }) AS outgoing
            OPTIONAL MATCH (inModel:Model)-[inc:RELATED_TO]->(m)
            WITH m, fields, outgoing, collect({
                sourceModel: inModel.name,
                relationType: inc.relation_type,
                foreignKeyField: inc.foreign_key_field
            }) AS incoming
            RETURN m.name AS name, m.orm AS orm, m.table_name AS tableName,
                   m.file_path AS filePath, m.line_start AS lineStart,
                   m.line_end AS lineEnd, m.primary_key AS primaryKey,
                   m.docstring AS docstring,
                   fields, outgoing AS outgoingRelations, incoming AS incomingRelations
            """,
            {"name": model_name},
        )
        if not result.records:
            return {"error": f"Model '{model_name}' not found."}
        record = result.records[0]
        self._non_null_maps(record, "fields", "name")
        self._non_null_maps(record, "outgoingRelations", "targetModel")
        self._non_null_maps(record, "incomingRelations", "sourceModel")
        return record

    def get_model_relationships(self) -> dict:
        """Get all model-to-model relationships."""
        result = self.graph.execute(
            """
            MATCH (a:Model)-[r:RELATED_TO]->(b:Model)
            RETURN a.name AS sourceModel, b.name AS targetModel,
                   r.relation_type AS relationType,
                   r.foreign_key_field AS foreignKeyField,
                   r.through_model AS throughModel
            ORDER BY a.name, b.name
            """
        )
        return {"relationships": result.records, "count": len(result.records)}

    # Only DB functions actually invoked from code carry an inline caller list; the
    # list is capped for agent consumption (callers_omitted records the cut).
    _DBFUNC_CALLER_CAP = 15

    def get_db_functions(self) -> dict:
        """List database stored functions (Supabase/Postgres RPCs) with their code
        callers. Capped for agent consumption: at most ``_MODELS_CAP`` functions
        (``db_functions_omitted`` gives the cut, ``count`` stays exact), each with
        up to ``_DBFUNC_CALLER_CAP`` inline callers (``callers_omitted``,
        ``caller_count`` is exact). Ordered by caller count so the most-used
        functions (the ones a change is riskiest to touch) come first.
        """
        result = self.graph.execute(
            """
            MATCH (d:DBFunction)
            OPTIONAL MATCH (caller:Function)-[:CALLS_RPC]->(d)
            WITH d, collect(DISTINCT caller.qualified_name) AS callers
            RETURN d.name AS name, d.schema AS schema, d.args AS args,
                   d.arg_count AS argCount, d.returns AS returns,
                   d.file_path AS filePath, callers
            ORDER BY size(callers) DESC, d.name
            """
        )
        for record in result.records:
            callers = record.get("callers") or []
            record["callers"] = callers
            record["caller_count"] = len(callers)
            self._cap_list(record, "callers", self._DBFUNC_CALLER_CAP)
        report = {"db_functions": result.records, "count": len(result.records)}
        self._cap_list(report, "db_functions", self._MODELS_CAP)
        return report

    # ------------------------------------------------------------------
    # Source code loader
    # ------------------------------------------------------------------

    def _load_source(self, file_path: str, start_line: int, end_line: int) -> str | None:
        """Load source code lines from disk."""
        if not self.repo_path:
            return None
        abs_path = os.path.join(self.repo_path, file_path)
        try:
            lines = Path(abs_path).read_text(encoding="utf-8", errors="replace").splitlines()
            # Convert to 0-indexed
            return "\n".join(lines[start_line - 1 : end_line])
        except (OSError, IndexError):
            return None
