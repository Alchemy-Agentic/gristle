"""Git-aware file watcher for incremental graph updates."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from watchfiles import awatch, Change

from gristle.config import settings
from gristle.ingestion.pipeline import IngestionPipeline
from gristle.parsers.registry import ParserRegistry

logger = logging.getLogger(__name__)

# Track active watcher tasks so they can be cancelled
_active_watchers: dict[str, asyncio.Task[None]] = {}


async def watch_repo(
    repo_path: str | Path,
    pipeline: IngestionPipeline,
    registry: ParserRegistry | None = None,
) -> None:
    """Watch a repository for file changes and update the graph incrementally.

    Batches changes within the debounce window, deduplicates per file,
    and processes all changes before moving on.  Runs indefinitely until
    cancelled.
    """
    repo_path = str(Path(repo_path).resolve())
    registry = registry or pipeline.registry
    supported = registry.supported_extensions
    debounce_ms = int(settings.watcher_debounce_seconds * 1000)

    logger.info("Watching %s for changes (debounce=%dms)", repo_path, debounce_ms)

    async for changes in awatch(
        repo_path,
        debounce=debounce_ms,
        recursive=True,
        step=200,
    ):
        # Deduplicate: keep only the latest change per file
        file_changes: dict[str, Change] = {}
        for change_type, abs_path in changes:
            rel_path = str(Path(abs_path).relative_to(repo_path)).replace("\\", "/")

            # Skip excluded directories
            parts = rel_path.split("/")
            if any(p in settings.excluded_dirs for p in parts):
                continue

            # Skip unsupported extensions
            ext = abs_path.rsplit(".", 1)[-1] if "." in abs_path else ""
            if ext not in supported:
                continue

            file_changes[rel_path] = change_type

        if not file_changes:
            continue

        logger.info("Processing %d file change(s)", len(file_changes))
        updated = 0
        deleted = 0

        for rel_path, change_type in file_changes.items():
            try:
                result = pipeline.update_file(repo_path, rel_path)
                if change_type == Change.deleted:
                    deleted += 1
                else:
                    updated += 1
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("Skipping %s: %s", rel_path, e)
            except Exception:
                logger.exception("Unexpected error updating %s", rel_path)

        logger.info(
            "Batch complete: %d updated, %d deleted",
            updated, deleted,
        )


def start_watching(
    repo_id: str,
    repo_path: str | Path,
    pipeline: IngestionPipeline,
) -> bool:
    """Start watching a repo in the background.  Returns True if started."""
    if repo_id in _active_watchers and not _active_watchers[repo_id].done():
        return False  # Already watching

    task = asyncio.get_event_loop().create_task(
        watch_repo(repo_path, pipeline),
        name=f"gristle-watch-{repo_id}",
    )
    _active_watchers[repo_id] = task
    return True


def stop_watching(repo_id: str) -> bool:
    """Stop watching a repo.  Returns True if a watcher was running."""
    task = _active_watchers.pop(repo_id, None)
    if task and not task.done():
        task.cancel()
        return True
    return False


def is_watching(repo_id: str) -> bool:
    """Check if a repo is currently being watched."""
    task = _active_watchers.get(repo_id)
    return task is not None and not task.done()
