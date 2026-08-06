"""Design-token parser — extract CSS custom-property definitions as ParsedTokens.

Standalone extractor helper (like ``parsers/feature_flags.py`` / ``parsers/sql.py`` —
NOT a registry ``LanguageParser``). Parses ``--name: value;`` declarations from a CSS
file, classifies each by category, records whether it sits in a Tailwind v4 ``@theme``
block vs. a plain selector (``:root`` etc.), and captures the tokens it references via
``var(--x)``. Regex-based on purpose: CSS is not in the tree-sitter mandate (Markdown is
the same exception), and custom-property declarations are a flat, unambiguous shape.

String literals, ``url(...)`` bodies, and comments are masked before boundary-scanning
(so a ``;``/``{``/``/*``/``@theme`` living inside a data-URI or string can't truncate a
value, fabricate a token, or mislabel a block); the real value is then sliced from the
original text. Only custom properties (``--x``) are read. The caller (``TokenExtractor``)
dedupes per name across files.
"""

from __future__ import annotations

import bisect
import re

from gristle.models import ParsedToken

# A custom-property declaration: `--name: value;`. Run against MASKED text (see `_mask`);
# the value span is then sliced from the original so the real value is preserved.
_DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;{}]+?)\s*;")
_VAR_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")
_THEME_OPEN_RE = re.compile(r"@theme\b[^{]*\{")

# Color signals: a color function / hex, or the shadcn HSL-triplet convention
# (`215 75% 25%`, optionally with `/ alpha`) stored bare for `hsl(var(--x))` composition.
_COLOR_VAL_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(|\boklab\(|\bcolor\(")
_HSL_TRIPLET_RE = re.compile(r"^-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?%\s+-?\d+(?:\.\d+)?%")
_INT_RE = re.compile(r"^-?\d+$")
_LEN_RE = re.compile(r"\d(?:px|rem|em|vh|vw|vmin|vmax|ch|%)\b")
# A composite that opens with two+ length-ish values then a color = a box-shadow/elevation
# value (e.g. `0 1px 2px rgba(0,0,0,.1)`), even when the name doesn't say "shadow".
_SHADOW_VAL_RE = re.compile(r"^\s*(?:inset\s+)?-?\d[\d.]*(?:px|rem|em)?\s+-?\d")

_COLOR_NAME_KW = (
    "color",
    "bg",
    "background",
    "foreground",
    "border",
    "ring",
    "accent",
    "primary",
    "secondary",
    "muted",
    "destructive",
    "success",
    "warning",
    "info",
    "popover",
    "card",
    "input",
    "chart",
    "gradient",
    "fill",
    "stroke",
    "brand",
    "surface",
)
_TYPO_NAME_KW = ("font", "leading", "tracking", "letter", "weight", "family", "text")
_SPACING_NAME_KW = (
    "spacing",
    "space",
    "gap",
    "size",
    "width",
    "height",
    "inset",
    "margin",
    "padding",
    "breakpoint",
    "container",
)
_ANIM_NAME_KW = ("animate", "animation", "keyframe", "duration", "ease", "transition", "delay")


def _mask(text: str) -> str:
    """Return a same-length copy of *text* with the CONTENTS of comments, quoted strings,
    and ``url(...)`` bodies replaced by inert characters (newlines preserved so byte
    offsets — and line numbers — stay aligned). Left-to-right so whichever delimiter opens
    first wins (a ``/*`` inside a string is not a comment; a ``"`` inside a comment is not
    a string). Declaration boundaries (``;``/``{``/``}``) and real ``@theme`` markers that
    live OUTSIDE strings/comments are left intact for scanning."""
    out = list(text)
    i, n = 0, len(text)

    def blank(start: int, end: int, ch: str) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = ch

    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            end = j + 2 if j != -1 else n
            blank(i, end, " ")
            i = end
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            end = j + 1 if j < n else n
            blank(i, end, "x")
            i = end
        elif text[i : i + 4].lower() == "url(" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "-_")):
            j = text.find(")", i + 4)
            end = j + 1 if j != -1 else n
            blank(i, end, "x")
            i = end
        else:
            i += 1
    return "".join(out)


def _theme_spans(masked: str) -> list[tuple[int, int]]:
    """Byte ranges of Tailwind v4 ``@theme { ... }`` blocks (brace-matched), on MASKED
    text so an ``@theme`` inside a string/comment is ignored."""
    spans: list[tuple[int, int]] = []
    for m in _THEME_OPEN_RE.finditer(masked):
        depth = 0
        i = m.end() - 1  # position of the opening `{`
        while i < len(masked):
            ch = masked[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    spans.append((m.start(), i))
                    break
            i += 1
    return spans


def _is_color(name: str, value: str) -> bool:
    if _COLOR_VAL_RE.search(value) or _HSL_TRIPLET_RE.match(value.strip()):
        return True
    return any(k in name for k in _COLOR_NAME_KW)


def _is_z_index(name: str, value: str) -> bool:
    if not _INT_RE.match(value):
        return False
    return name == "z" or name.startswith("z-") or "z-index" in name or "zindex" in name or "z_index" in name


def _categorize(name: str, value: str) -> str:
    """Best-effort token category. Order matters: a ``-color`` suffix wins over a "shadow"
    name (``--shadow-color`` is a color); a box-shadow-shaped value wins over the generic
    color test (``--elevation-1: 0 1px 2px rgba(...)`` is a shadow, not a color)."""
    n = name.lower()
    v = value.strip()
    if n.endswith("color") or n.endswith("colour"):
        return "color"
    if "radius" in n or "rounded" in n:
        return "radius"
    if "shadow" in n:
        return "shadow"
    if _is_z_index(n, v):
        return "z_index"
    if any(k in n for k in _ANIM_NAME_KW):
        return "animation"
    if _SHADOW_VAL_RE.match(v) and _COLOR_VAL_RE.search(v):
        return "shadow"
    if _is_color(n, value):
        return "color"
    if any(k in n for k in _TYPO_NAME_KW):
        return "typography"
    if any(k in n for k in _SPACING_NAME_KW) or _LEN_RE.search(v):
        return "spacing"
    return "other"


def parse_css_tokens(relative_path: str, content: str) -> list[ParsedToken]:
    """Extract every CSS custom-property definition as a ParsedToken (document order)."""
    masked = _mask(content)
    newline_at = [i for i, ch in enumerate(masked) if ch == "\n"]
    theme = _theme_spans(masked)

    def line_of(pos: int) -> int:
        return bisect.bisect_right(newline_at, pos) + 1

    def in_theme(pos: int) -> bool:
        return any(start <= pos <= end for start, end in theme)

    tokens: list[ParsedToken] = []
    for m in _DECL_RE.finditer(masked):
        name = m.group(1)[2:]  # drop the leading `--`
        vs, ve = m.span(2)
        value = content[vs:ve].strip()  # real value, sliced from the ORIGINAL text
        if not value:
            continue
        pos = m.start()
        tokens.append(
            ParsedToken(
                name=name,
                value=value,
                category=_categorize(name, value),
                source_kind="css_theme" if in_theme(pos) else "css_root",
                references=_VAR_RE.findall(value),
                file_path=relative_path,
                line=line_of(pos),
            )
        )
    return tokens
