"""File system walker that respects .gitignore and size limits."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pathspec

from gristle.config import settings


@dataclass(slots=True)
class WalkedFile:
    relative_path: str  # Relative to repo root, forward-slash separated
    absolute_path: str
    extension: str


def walk_repo(
    repo_path: str | Path,
    supported_extensions: frozenset[str] | None = None,
) -> list[WalkedFile]:
    """Walk a repository and yield parseable source files.

    Respects ``.gitignore``, skips excluded directories, binary files,
    and files exceeding the configured size limit.
    """
    repo_path = Path(repo_path).resolve()
    gitignore_spec = _load_gitignore(repo_path)
    results: list[WalkedFile] = []

    for dirpath, dirnames, filenames in os.walk(repo_path, topdown=True):
        # Prune excluded directories in-place
        dirnames[:] = [
            d
            for d in dirnames
            if d not in settings.excluded_dirs
            and not _is_gitignored(
                gitignore_spec,
                os.path.relpath(os.path.join(dirpath, d), repo_path) + "/",
            )
        ]

        for fname in filenames:
            abs_path = os.path.join(dirpath, fname)
            try:
                rel_path = os.path.relpath(abs_path, repo_path).replace("\\", "/")
            except ValueError:
                continue  # Skip paths on different drives or special device names

            # Skip gitignored files
            if _is_gitignored(gitignore_spec, rel_path):
                continue

            # Skip files that are too large
            try:
                if os.path.getsize(abs_path) > settings.max_file_size_bytes:
                    continue
            except OSError:
                continue

            # Extract extension
            ext = fname.rsplit(".", 1)[-1] if "." in fname else ""
            if not ext:
                continue

            # Skip unsupported extensions if a whitelist is provided
            if supported_extensions and ext not in supported_extensions:
                continue

            # Skip likely binary files
            if _is_binary(abs_path):
                continue

            results.append(WalkedFile(
                relative_path=rel_path,
                absolute_path=abs_path,
                extension=ext,
            ))

    return results


def _load_gitignore(repo_path: Path) -> pathspec.PathSpec | None:
    gitignore_file = repo_path / ".gitignore"
    if not gitignore_file.exists():
        return None
    try:
        text = gitignore_file.read_text(encoding="utf-8", errors="replace")
        return pathspec.PathSpec.from_lines("gitwildmatch", text.splitlines())
    except Exception:
        return None


def _is_gitignored(spec: pathspec.PathSpec | None, path: str) -> bool:
    if spec is None:
        return False
    return spec.match_file(path)


def _is_binary(path: str, chunk_size: int = 8192) -> bool:
    """Heuristic: if the first chunk contains null bytes, treat as binary."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(chunk_size)
        return b"\x00" in chunk
    except (OSError, PermissionError):
        return True
