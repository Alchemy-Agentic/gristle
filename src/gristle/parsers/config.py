"""Config file parser: extracts metadata from JSON, YAML, TOML, Dockerfile, and .env files.

Uses Python stdlib (json, tomllib) plus PyYAML for YAML files.
Does NOT extend LanguageParser — config files aren't source code with functions/classes.
"""

from __future__ import annotations

import json
import logging
import re

from gristle.models import ParsedConfigFile, ParsedEnvVar

logger = logging.getLogger(__name__)

# Extensions handled by the config parser (filename-based dispatch, not extension-based)
CONFIG_FILENAMES: dict[str, str] = {
    "package.json": "package",
    "tsconfig.json": "tsconfig",
    "Dockerfile": "dockerfile",
    "docker-compose.yml": "compose",
    "docker-compose.yaml": "compose",
    "compose.yml": "compose",
    "compose.yaml": "compose",
    "requirements.txt": "package",
    "pyproject.toml": "package",
}

# Patterns for config files matched by path
CONFIG_PATH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.github/workflows/[^/]+\.ya?ml$"), "ci"),
    (re.compile(r"\.env\.example$"), "env_template"),
    (re.compile(r"\.env\.template$"), "env_template"),
    (re.compile(r"\.env\.sample$"), "env_template"),
]


def classify_config_file(file_path: str) -> str | None:
    """Return config_type if this file is a known config file, else None."""
    # Check by exact filename (basename)
    basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    if basename in CONFIG_FILENAMES:
        return CONFIG_FILENAMES[basename]
    # Check by path pattern
    for pattern, config_type in CONFIG_PATH_PATTERNS:
        if pattern.search(file_path):
            return config_type
    return None


def parse_config_file(file_path: str, content: str) -> ParsedConfigFile | None:
    """Parse a config file and return extracted metadata.

    Returns None if the file is not a recognised config file.
    """
    config_type = classify_config_file(file_path)
    if config_type is None:
        return None

    line_count = content.count("\n") + 1
    properties: dict[str, str] = {}
    env_vars: list[ParsedEnvVar] = []

    try:
        if config_type == "package":
            basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
            if basename == "package.json":
                properties, env_vars = _parse_package_json(content, file_path)
            # requirements.txt and pyproject.toml don't have extra config properties
            # (versions are already handled by dependency extraction)
        elif config_type == "tsconfig":
            properties = _parse_tsconfig(content)
        elif config_type == "dockerfile":
            properties, env_vars = _parse_dockerfile(content, file_path)
        elif config_type == "compose":
            properties = _parse_compose(content)
        elif config_type == "ci":
            properties = _parse_ci_workflow(content, file_path)
        elif config_type == "env_template":
            env_vars = _parse_env_template(content, file_path)
    except Exception as e:
        logger.warning("Config parse error %s: %s", file_path, e)

    return ParsedConfigFile(
        path=file_path,
        config_type=config_type,
        properties=properties,
        env_vars=env_vars,
        line_count=line_count,
    )


def _parse_package_json(content: str, file_path: str) -> tuple[dict[str, str], list[ParsedEnvVar]]:
    """Extract scripts, engines from package.json."""
    props: dict[str, str] = {}
    env_vars: list[ParsedEnvVar] = []
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return props, env_vars

    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        props["config_scripts"] = json.dumps(scripts)

    engines = data.get("engines")
    if isinstance(engines, dict):
        props["config_engines"] = json.dumps(engines)

    return props, env_vars


def _parse_tsconfig(content: str) -> dict[str, str]:
    """Extract target, module, paths from tsconfig.json."""
    props: dict[str, str] = {}
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return props

    compiler = data.get("compilerOptions", {})
    if isinstance(compiler, dict):
        if "target" in compiler:
            props["config_target"] = str(compiler["target"])
        if "module" in compiler:
            props["config_module"] = str(compiler["module"])
        paths = compiler.get("paths")
        if isinstance(paths, dict):
            props["config_paths"] = json.dumps(paths)

    return props


_DOCKER_FROM_RE = re.compile(r"^FROM\s+(\S+)", re.MULTILINE)
_DOCKER_EXPOSE_RE = re.compile(r"^EXPOSE\s+(.+)$", re.MULTILINE)
_DOCKER_ENV_RE = re.compile(r"^ENV\s+(\w+)(?:\s*=\s*(.*)|\s+(.*))?$", re.MULTILINE)
_DOCKER_ARG_RE = re.compile(r"^ARG\s+(\w+)(?:\s*=\s*(.*))?$", re.MULTILINE)


def _parse_dockerfile(content: str, file_path: str) -> tuple[dict[str, str], list[ParsedEnvVar]]:
    """Extract base image, exposed ports, ENV directives from Dockerfile."""
    props: dict[str, str] = {}
    env_vars: list[ParsedEnvVar] = []

    # Base image (last FROM wins for multi-stage)
    froms = _DOCKER_FROM_RE.findall(content)
    if froms:
        props["config_base_image"] = froms[-1]

    # Exposed ports
    ports = _DOCKER_EXPOSE_RE.findall(content)
    if ports:
        props["config_exposed_ports"] = ",".join(p.strip() for p in ports)

    # ENV directives
    for m in _DOCKER_ENV_RE.finditer(content):
        name = m.group(1)
        default = (m.group(2) or m.group(3) or "").strip().strip('"').strip("'")
        env_vars.append(
            ParsedEnvVar(
                name=name,
                source_file=file_path,
                default_value=default if default else None,
            )
        )

    return props, env_vars


def _parse_compose(content: str) -> dict[str, str]:
    """Extract service names from docker-compose.yml."""
    props: dict[str, str] = {}
    try:
        import yaml

        data = yaml.safe_load(content)
    except Exception:
        return props

    if isinstance(data, dict):
        services = data.get("services")
        if isinstance(services, dict):
            props["config_services"] = ",".join(sorted(services.keys()))

    return props


def _parse_ci_workflow(content: str, file_path: str) -> dict[str, str]:
    """Extract triggers and job names from GitHub Actions workflow."""
    props: dict[str, str] = {}
    try:
        import yaml

        data = yaml.safe_load(content)
    except Exception:
        return props

    if not isinstance(data, dict):
        return props

    # Triggers (on: push, pull_request, etc.)
    on_key = data.get("on") or data.get(True)  # YAML parses `on:` as True
    if isinstance(on_key, dict):
        props["config_triggers"] = ",".join(sorted(on_key.keys()))
    elif isinstance(on_key, list):
        props["config_triggers"] = ",".join(sorted(on_key))
    elif isinstance(on_key, str):
        props["config_triggers"] = on_key

    # Jobs
    jobs = data.get("jobs")
    if isinstance(jobs, dict):
        props["config_jobs"] = ",".join(sorted(jobs.keys()))

    return props


def _parse_env_template(content: str, file_path: str) -> list[ParsedEnvVar]:
    """Extract env var names from .env.example / .env.template files."""
    env_vars: list[ParsedEnvVar] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # KEY=value or KEY= or just KEY
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key or not re.match(r"^[A-Z_][A-Z0-9_]*$", key):
                continue
            env_vars.append(
                ParsedEnvVar(
                    name=key,
                    source_file=file_path,
                    default_value=value if value else None,
                    required=not bool(value),
                )
            )
        else:
            # Bare key without value
            if re.match(r"^[A-Z_][A-Z0-9_]*$", line):
                env_vars.append(
                    ParsedEnvVar(
                        name=line,
                        source_file=file_path,
                        required=True,
                    )
                )
    return env_vars
