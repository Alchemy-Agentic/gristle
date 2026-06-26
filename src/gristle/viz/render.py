"""Render a ``get_subgraph`` payload to a single self-contained HTML document.

All JavaScript (Cytoscape + dagre, vendored under ``assets/``) and the graph data
are inlined, so an exported file opens with no network access. Two injection
defenses: the data JSON has ``</`` escaped so a string value containing
``</script>`` cannot break out of the inline ``<script>``, and the template only
ever puts repo-derived strings into the DOM via ``textContent`` (never innerHTML).
"""

from __future__ import annotations

import html
import json
from importlib import resources
from typing import Any


def _read_pkg_file(*parts: str) -> str:
    return resources.files("gristle.viz").joinpath(*parts).read_text(encoding="utf-8")


def _safe_data_json(data: Any) -> str:
    # Escape ``</`` so a value containing ``</script>`` can't terminate the inline
    # <script>. ensure_ascii=False keeps unicode readable; default=str is a guard
    # for any stray non-serializable value.
    return json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")


def render_html(data: dict[str, Any], title: str = "Gristle graph") -> str:
    """Return a single self-contained HTML document visualizing ``data``.

    ``data`` is a ``get_subgraph`` payload (``{meta, nodes, edges}``). The result
    is a complete HTML string with the renderer and data inlined — write it to a
    ``.html`` file and open it; no server or network is needed.
    """
    out = _read_pkg_file("template.html")
    # Assets first, data LAST: a replacement value can only ever contain a token
    # that has not been substituted yet, and the inlined data is substituted last.
    for token, value in (
        ("__GRISTLE_TITLE__", html.escape(title)),
        ("__GRISTLE_CYTOSCAPE_JS__", _read_pkg_file("assets", "cytoscape.min.js")),
        ("__GRISTLE_DAGRE_JS__", _read_pkg_file("assets", "dagre.min.js")),
        ("__GRISTLE_CYTOSCAPE_DAGRE_JS__", _read_pkg_file("assets", "cytoscape-dagre.js")),
        ("__GRISTLE_DATA__", _safe_data_json(data)),
    ):
        out = out.replace(token, value)
    return out
