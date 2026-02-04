"""Security pattern detection for source code.

Detects hardcoded secrets, SQL injection risks, unsafe calls, and
LLM insecure output handling patterns.  Called from language parsers
the same way ``env_vars.py`` is used — pure functions, no AST required.

Sources:
- https://github.com/mazen160/secrets-patterns-db
- https://owasp.org/www-project-top-10-for-large-language-model-applications/
- https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gristle.models import SecurityFinding

# ======================================================================
# Hardcoded secrets detection
# ======================================================================

# Provider-specific patterns (high confidence, low false-positive rate).
# Sourced from secrets-patterns-db and provider documentation.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # (detail_label, compiled_regex, severity)
    ("AWS_ACCESS_KEY", re.compile(r"(?:A3T[A-Z0-9]|AKIA|AGPA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}"), "high"),
    ("GITHUB_TOKEN", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,255}"), "high"),
    ("SLACK_TOKEN", re.compile(r"xox[bposa]-[0-9a-zA-Z\-]{10,}"), "high"),
    ("STRIPE_SECRET_KEY", re.compile(r"sk_(?:live|test)_[0-9a-zA-Z]{24,}"), "high"),
    ("STRIPE_PUBLIC_KEY", re.compile(r"pk_live_[0-9a-zA-Z]{24,}"), "medium"),
    ("GOOGLE_API_KEY", re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "high"),
    ("TWILIO_SID", re.compile(r"\bAC[0-9a-f]{32}\b"), "high"),
    ("SENDGRID_KEY", re.compile(r"SG\.[a-zA-Z0-9_\-]{20,}\.[a-zA-Z0-9_\-]{39,}"), "high"),
    ("NPM_TOKEN", re.compile(r"//registry\.npmjs\.org/:_authToken=[A-Za-z0-9\-]+"), "high"),
    ("PYPI_TOKEN", re.compile(r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9\-_]{50,}"), "high"),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----"), "high"),
    ("OPENAI_KEY", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "high"),
]

# Generic pattern: variable name looks secret-ish + value is a long string.
_GENERIC_SECRET_RE = re.compile(
    r"""(?:api[_-]?key|secret[_-]?key|password|passwd|token|auth[_-]?token|credential|client[_-]?secret)"""
    r"""\s*[:=]\s*["']([^"']{20,})["']""",
    re.IGNORECASE,
)

# Lines to skip (env lookups, comments).
_SECRET_SKIP_RE = re.compile(
    r"(os\.environ|process\.env|getenv|\.env\(|"
    r"""^\s*#|^\s*//|^\s*\*|"""
    r"your[_-].*[_-]here|changeme|xxx+|TODO|REPLACE|example|placeholder|<[A-Z_]+>)",
    re.IGNORECASE,
)


def detect_hardcoded_secrets(content: str, language: str) -> list[SecurityFinding]:
    """Detect hardcoded secrets in source code using known patterns."""
    from gristle.models import SecurityFinding

    findings: list[SecurityFinding] = []
    lines = content.splitlines()

    for line_num_0, line in enumerate(lines):
        if _SECRET_SKIP_RE.search(line):
            continue

        # Provider-specific patterns.
        for detail, pattern, severity in _SECRET_PATTERNS:
            if pattern.search(line):
                # Skip OpenAI pattern if it's actually a Stripe key.
                if detail == "OPENAI_KEY" and ("sk_live_" in line or "sk_test_" in line):
                    continue
                findings.append(
                    SecurityFinding(
                        category="hardcoded_secret",
                        detail=detail,
                        line=line_num_0 + 1,
                        severity=severity,
                    )
                )
                break  # One finding per line is enough.

        # Generic assignment pattern (only if no provider match above).
        if not any(f.line == line_num_0 + 1 for f in findings):
            m = _GENERIC_SECRET_RE.search(line)
            if m:
                value = m.group(1)
                # Skip placeholders and short values.
                if not _SECRET_SKIP_RE.search(value):
                    findings.append(
                        SecurityFinding(
                            category="hardcoded_secret",
                            detail="GENERIC_SECRET",
                            line=line_num_0 + 1,
                            severity="medium",
                        )
                    )

    return findings


# ======================================================================
# SQL injection detection
# ======================================================================

_SQL_KEYWORDS_RE = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|WHERE|JOIN)\b",
    re.IGNORECASE,
)

# Python f-string with SQL + interpolation: f"SELECT ... {var}"
_PY_FSTRING_SQL_RE = re.compile(
    r"""f["'].*\b(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b.*\{""",
    re.IGNORECASE,
)

# Python % formatting: "SELECT ... %s" % var (not tuple param form)
_PY_PERCENT_SQL_RE = re.compile(
    r"""["'].*\b(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b.*["']\s*%\s*(?!\()""",
    re.IGNORECASE,
)

# Python .format(): "SELECT {}".format(var)
_PY_FORMAT_SQL_RE = re.compile(
    r"""["'].*\b(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b.*\{.*\}.*["']\.format\(""",
    re.IGNORECASE,
)

# TS/JS template literal with SQL: `SELECT ... ${var}`
_TS_TEMPLATE_SQL_RE = re.compile(
    r"""`[^`]*\b(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b[^`]*\$\{""",
    re.IGNORECASE,
)

# String concatenation: "SELECT " + var
_CONCAT_SQL_RE = re.compile(
    r"""["'].*\b(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b.*["']\s*\+""",
    re.IGNORECASE,
)

# Query executor functions (context check).
_SQL_EXECUTORS = frozenset(
    {
        "execute",
        "executemany",
        "query",
        "raw",
        "rawQuery",
        "$queryRaw",
        "$executeRaw",
        "$queryRawUnsafe",
        "$executeRawUnsafe",
        "runQuery",
        "prepare",
        "cursor.execute",
        "connection.execute",
        "db.execute",
        "session.execute",
        "text",
    }
)


def _has_sql_executor(content: str) -> bool:
    """Check if the file contains any SQL executor calls."""
    return any(ex in content for ex in _SQL_EXECUTORS)


def detect_sql_injection(content: str, language: str) -> list[SecurityFinding]:
    """Detect potential SQL injection via string interpolation."""
    from gristle.models import SecurityFinding

    if not _has_sql_executor(content):
        return []

    findings: list[SecurityFinding] = []
    lines = content.splitlines()

    if language == "python":
        patterns = [_PY_FSTRING_SQL_RE, _PY_PERCENT_SQL_RE, _PY_FORMAT_SQL_RE, _CONCAT_SQL_RE]
    else:
        patterns = [_TS_TEMPLATE_SQL_RE, _CONCAT_SQL_RE]

    for line_num_0, line in enumerate(lines):
        # Skip comments.
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        for pat in patterns:
            if pat.search(line):
                findings.append(
                    SecurityFinding(
                        category="sql_injection",
                        detail="dynamic_query",
                        line=line_num_0 + 1,
                        severity="high",
                    )
                )
                break  # One finding per line.

    return findings


# ======================================================================
# Unsafe call detection
# ======================================================================

_UNSAFE_CALLS: frozenset[str] = frozenset(
    {
        # Code execution
        "eval",
        "exec",
        "compile",
        "Function",
        # Deserialization
        "pickle.loads",
        "pickle.load",
        "yaml.unsafe_load",
        "yaml.load",
        "marshal.loads",
        "shelve.open",
        # Shell execution
        "os.system",
        "os.popen",
        "subprocess.call",
        "subprocess.run",
        "subprocess.Popen",
        "child_process.exec",
        "child_process.execSync",
        "child_process.spawn",
    }
)


def detect_unsafe_calls(calls: list[str]) -> list[str]:
    """Check a function's call list for known dangerous functions.

    Returns tags like ``"unsafe_call:eval"`` for each match.
    """
    return [f"unsafe_call:{c}" for c in calls if c in _UNSAFE_CALLS]


# ======================================================================
# LLM insecure output handling (OWASP LLM05)
# ======================================================================

# LLM API calls (sources) — substring matching for flexibility.
_LLM_SOURCE_PATTERNS: list[str] = [
    "completions.create",  # OpenAI / Anthropic
    "messages.create",  # Anthropic
    "chat.completions",  # OpenAI
    "cohere.generate",
    "cohere.chat",
    "chain.invoke",
    "chain.run",
    "agent.invoke",  # LangChain
    "llm.invoke",
    "llm.predict",
    "llm.generate",
    "model.generate_content",  # Google Gemini
    "generate_content",
]

# Dangerous sinks that should not consume raw LLM output.
_LLM_DANGEROUS_SINKS: frozenset[str] = frozenset(
    {
        # Code execution
        "eval",
        "exec",
        "compile",
        "Function",
        # SQL
        "cursor.execute",
        "db.execute",
        "connection.execute",
        "session.execute",
        "execute",
        "executemany",
        # Shell
        "os.system",
        "os.popen",
        "subprocess.call",
        "subprocess.run",
        "child_process.exec",
        "child_process.execSync",
        # XSS (TS/JS)
        "document.write",
        # SSTI
        "render_template_string",
    }
)


def _has_llm_source(calls: list[str]) -> bool:
    """Check if any call matches a known LLM API pattern."""
    for call in calls:
        for pattern in _LLM_SOURCE_PATTERNS:
            if pattern in call:
                return True
    return False


def detect_llm_output_risks(calls: list[str]) -> list[str]:
    """Detect functions that call both an LLM API and a dangerous sink.

    Per OWASP LLM05 (Improper Output Handling), LLM output should never
    flow directly to eval/exec, SQL queries, shell commands, or HTML
    rendering without sanitization.  This is a co-occurrence heuristic —
    the LLM output may not actually flow to the sink, but the function
    is worth reviewing.

    Returns tags like ``"llm_output_risk:eval"``.
    """
    if not _has_llm_source(calls):
        return []

    return [f"llm_output_risk:{c}" for c in calls if c in _LLM_DANGEROUS_SINKS]
