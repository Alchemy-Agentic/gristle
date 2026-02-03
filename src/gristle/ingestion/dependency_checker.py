"""Dependency staleness and vulnerability checking via npm/PyPI/OSV APIs."""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DependencyHealth:
    latest_version: str = ""  # "" if fetch failed
    is_outdated: bool = False  # False if can't determine
    vulnerability_ids: list[str] = field(default_factory=list)  # CVE/GHSA IDs
    checked_at: str = ""  # ISO timestamp


# ---------------------------------------------------------------------------
# In-memory TTL cache
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[DependencyHealth, float]] = {}
_CACHE_TTL = 300.0  # 5 minutes


def _get_cached(key: str) -> DependencyHealth | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    result, ts = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return result


def _set_cached(key: str, value: DependencyHealth) -> None:
    _cache[key] = (value, time.monotonic())


def clear_cache() -> None:
    """Clear the in-memory dependency health cache."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Version utilities
# ---------------------------------------------------------------------------

_RANGE_PREFIX_RE = re.compile(r"^[\^~>=<!]+\s*")


def _strip_version_range(version: str) -> str:
    """Strip range prefixes: '^18.2.0' → '18.2.0', '>=1.0.0,<2' → '1.0.0'."""
    # Take only the first segment before comma
    version = version.split(",")[0].strip()
    # Strip ^, ~, >=, >, <=, <, ==, != prefixes
    return _RANGE_PREFIX_RE.sub("", version).strip()


def _is_outdated(declared: str, latest: str, ecosystem: str) -> bool:
    """Compare declared version against latest. Returns False on any parse error."""
    declared = _strip_version_range(declared)
    if not declared or not latest:
        return False

    try:
        if ecosystem == "PyPI":
            return Version(declared) < Version(latest)
        else:
            # npm: simple tuple comparison on dot-split segments
            d_parts = [int(x) for x in declared.split(".")]
            l_parts = [int(x) for x in latest.split(".")]
            return d_parts < l_parts
    except (InvalidVersion, ValueError):
        return False


# ---------------------------------------------------------------------------
# Registry fetchers
# ---------------------------------------------------------------------------


def _fetch_npm_latest(client: httpx.Client, name: str) -> str | None:
    """Fetch latest version from npm registry. Returns None on failure."""
    try:
        # Scoped packages need URL encoding: @scope/pkg → @scope%2fpkg
        url_name = name.replace("/", "%2f")
        resp = client.get(f"https://registry.npmjs.org/{url_name}", params={"fields": "dist-tags"})
        resp.raise_for_status()
        data = resp.json()
        dist_tags = data.get("dist-tags", {})
        return dist_tags.get("latest")
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.debug("npm fetch failed for %s: %s", name, exc)
        return None


def _fetch_pypi_latest(client: httpx.Client, name: str) -> tuple[str | None, list[str]]:
    """Fetch latest version + known vulnerabilities from PyPI.

    Returns (latest_version, [vuln_ids]) or (None, []) on failure.
    """
    try:
        resp = client.get(f"https://pypi.org/pypi/{name}/json")
        resp.raise_for_status()
        data = resp.json()
        version = data.get("info", {}).get("version")
        vulns = [v.get("id", "") for v in data.get("vulnerabilities", []) if v.get("id")]
        return version, vulns
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.debug("PyPI fetch failed for %s: %s", name, exc)
        return None, []


def _fetch_osv_batch(
    client: httpx.Client,
    packages: list[tuple[str, str, str]],  # (name, version, ecosystem)
) -> dict[str, list[str]]:
    """Batch-query OSV.dev for known vulnerabilities.

    Returns {package_name: [vuln_id, ...]}.
    """
    if not packages:
        return {}

    queries = []
    for name, version, ecosystem in packages:
        clean_version = _strip_version_range(version)
        if not clean_version:
            continue
        # OSV ecosystem names: "npm", "PyPI"
        queries.append({"package": {"name": name, "ecosystem": ecosystem}, "version": clean_version})

    if not queries:
        return {}

    try:
        resp = client.post("https://api.osv.dev/v1/querybatch", json={"queries": queries})
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("OSV batch query failed: %s", exc)
        return {}

    results: dict[str, list[str]] = {}
    for i, result in enumerate(data.get("results", [])):
        vulns = result.get("vulns", [])
        if vulns and i < len(packages):
            pkg_name = packages[i][0]
            vuln_ids = [v.get("id", "") for v in vulns if v.get("id")]
            if vuln_ids:
                results[pkg_name] = vuln_ids

    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def check_dependencies(
    deps: list[tuple[str, str, str]],  # (name, declared_version, ecosystem)
    timeout: float = 5.0,
    max_workers: int = 20,
    enabled: bool = True,
) -> dict[str, DependencyHealth]:
    """Fetch latest versions + vulnerabilities for all dependencies.

    Uses ThreadPoolExecutor for parallel npm/PyPI calls, batch POST for OSV.
    Returns {dep_name: DependencyHealth}. Empty dict if disabled or all fail.
    """
    if not enabled or not deps:
        return {}

    now = datetime.now(UTC).isoformat()
    results: dict[str, DependencyHealth] = {}

    # Check cache first, collect uncached
    uncached: list[tuple[str, str, str]] = []
    for name, version, ecosystem in deps:
        cached = _get_cached(f"{name}@{version}")
        if cached is not None:
            results[name] = cached
        else:
            uncached.append((name, version, ecosystem))

    if not uncached:
        return results

    # Fetch latest versions in parallel
    latest_versions: dict[str, str | None] = {}
    pypi_vulns: dict[str, list[str]] = {}

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(uncached))) as executor:
                futures = {}
                for name, _version, ecosystem in uncached:
                    if ecosystem == "npm":
                        futures[executor.submit(_fetch_npm_latest, client, name)] = (name, ecosystem)
                    elif ecosystem == "PyPI":
                        futures[executor.submit(_fetch_pypi_latest, client, name)] = (name, ecosystem)

                for future in as_completed(futures):
                    name, ecosystem = futures[future]
                    try:
                        result = future.result()
                        if ecosystem == "PyPI":
                            latest, vulns = result
                            latest_versions[name] = latest
                            if vulns:
                                pypi_vulns[name] = vulns
                        else:
                            latest_versions[name] = result
                    except Exception:
                        logger.debug("Version fetch failed for %s", name, exc_info=True)

            # Batch OSV query for all packages
            osv_vulns = _fetch_osv_batch(client, uncached)

    except httpx.HTTPError:
        logger.debug("HTTP client error during dependency check", exc_info=True)
        return results

    # Build health results
    for name, version, ecosystem in uncached:
        latest = latest_versions.get(name)
        outdated = _is_outdated(version, latest, ecosystem) if latest else False

        # Merge vulnerability sources
        vuln_ids: list[str] = []
        vuln_ids.extend(pypi_vulns.get(name, []))
        vuln_ids.extend(osv_vulns.get(name, []))
        # Deduplicate preserving order
        seen: set[str] = set()
        unique_vulns: list[str] = []
        for vid in vuln_ids:
            if vid not in seen:
                seen.add(vid)
                unique_vulns.append(vid)

        health = DependencyHealth(
            latest_version=latest or "",
            is_outdated=outdated,
            vulnerability_ids=unique_vulns,
            checked_at=now,
        )
        results[name] = health
        _set_cached(f"{name}@{version}", health)

    return results
