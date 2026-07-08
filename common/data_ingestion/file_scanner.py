"""
File system scanner for EFM3 data sources.

Discovers files matching include/exclude glob patterns and returns
SourceFileRecord-like dicts with metadata (path, name, ext, size,
mtime, sha256).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .errors import FileScanError

logger = logging.getLogger(__name__)

# Chunk size (bytes) for streaming sha256 computation on large files.
_HASH_CHUNK_SIZE = 64 * 1024  # 64 KB


class FileScanner:
    """
    Scans directories, applies include/exclude glob patterns, and
    produces file metadata dicts with sha256 hashes.
    """

    # ── Public API ─────────────────────────────────────────────────

    def scan_directory(
        self,
        root_path: Path,
        include_patterns: list[str],
        exclude_patterns: list[str] | None = None,
    ) -> list[dict]:
        """
        Walk *root_path* and return metadata dicts for matching files.

        Each returned dict has keys:
            path        – absolute path as string
            name        – file name (stem + ext)
            ext         – lower-case extension with dot (e.g. ".csv")
            size_bytes  – file size in bytes
            mtime       – ISO-format modification timestamp
            sha256      – hex-encoded SHA-256 hash
        """
        if not root_path.exists():
            raise FileScanError(f"Root path does not exist: {root_path}")
        if not root_path.is_dir():
            raise FileScanError(f"Root path is not a directory: {root_path}")

        exclude = set(exclude_patterns) if exclude_patterns else set()
        results: list[dict] = []

        for pattern in include_patterns:
            # Use pathlib glob
            matched = list(root_path.glob(pattern))
            if not matched:
                logger.debug("Pattern '%s' matched no files in %s", pattern, root_path)
                continue

            for file_path in matched:
                if not file_path.is_file():
                    continue
                if self._is_excluded(file_path, root_path, exclude):
                    continue

                try:
                    info = self._file_info(file_path)
                except (OSError, PermissionError) as exc:
                    logger.warning("Skipping %s: %s", file_path, exc)
                    continue

                results.append(info)

        # Deduplicate by absolute path (a file could match multiple patterns)
        seen: set[str] = set()
        deduped: list[dict] = []
        for info in results:
            if info["path"] not in seen:
                seen.add(info["path"])
                deduped.append(info)

        logger.info("Scanned %s — found %d files", root_path, len(deduped))
        return deduped

    # ── Helpers ────────────────────────────────────────────────────

    def _file_info(self, file_path: Path) -> dict:
        stat = file_path.stat()
        sha = self._hash_file(file_path)
        mtime_dt = datetime.fromtimestamp(stat.st_mtime)

        return {
            "path": str(file_path.resolve()),
            "name": file_path.name,
            "ext": file_path.suffix.lower(),
            "size_bytes": stat.st_size,
            "mtime": mtime_dt.isoformat(),
            "sha256": sha,
        }

    @staticmethod
    def _hash_file(file_path: Path) -> str:
        """Compute SHA-256 using streaming read to handle large files."""
        h = hashlib.sha256()
        with open(file_path, "rb") as fh:
            while True:
                chunk = fh.read(_HASH_CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _is_excluded(file_path: Path, root: Path, exclude_patterns: set[str]) -> bool:
        """Check whether *file_path* matches any exclude glob pattern."""
        for pattern in exclude_patterns:
            # Try relative-to-root match first; fall back to absolute
            try:
                rel = file_path.relative_to(root)
                if rel.match(pattern):
                    return True
            except ValueError:
                pass
            try:
                if file_path.match(pattern):
                    return True
            except ValueError:
                pass
        return False
