"""Traceback frame extraction and top-k chunk retrieval at exception time."""
import logging
import os
import sysconfig
import threading
import traceback

from django.conf import settings

from ..sanitize import sanitize_traceback
from ..client import create_embeddings, get_embed_backend, get_embeddings_client
from .indexer import get_embed_model, get_include_dirs, get_index_path
from .store import VectorStore

logger = logging.getLogger(__name__)

CONTEXT_LINES = 5


class EmbeddingIndexMismatchError(Exception):
    """The index was built with a different embedding configuration than the
    one now configured. Querying it would return meaningless results."""

    def __init__(self, index_path, backend, model, index_desc, config_desc):
        self.key = (index_path, backend, model)
        super().__init__(
            f"RAG index at {index_path} was built with {index_desc}, but the "
            f"current configuration is {config_desc}. Rerun `python manage.py "
            "build_error_index` to rebuild it."
        )


_warned_mismatches = set()
_warned_lock = threading.Lock()


def warn_index_mismatch_once(exc):
    """Log an index mismatch once per process per (index path, config)."""
    with _warned_lock:
        if exc.key in _warned_mismatches:
            return
        _warned_mismatches.add(exc.key)
    logger.warning(
        "explain_errors: %s Falling back to traceback-only prompt.", exc
    )


def _describe(backend, model, dimension=None):
    desc = f"backend={backend}, model={model}"
    if dimension is not None:
        desc += f", dimension={dimension}"
    return desc


def _check_index_meta(index_path, meta, backend, model, query_dimension=None):
    """Raise EmbeddingIndexMismatchError if the index and config disagree.
    A legacy index without metadata was necessarily built by the openai
    backend, so it is only accepted under that backend."""
    config_desc = _describe(backend, model, query_dimension)
    if meta is None:
        if backend != "openai":
            raise EmbeddingIndexMismatchError(
                index_path, backend, model,
                "an older format (backend=openai, no recorded model)",
                config_desc,
            )
        return
    index_desc = _describe(meta["backend"], meta["model"], meta["dimension"])
    if (
        meta["backend"] != backend
        or meta["model"] != model
        or (query_dimension is not None and meta["dimension"] != query_dimension)
    ):
        raise EmbeddingIndexMismatchError(
            index_path, backend, model, index_desc, config_desc
        )


def _excluded_dirs():
    dirs = set()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        path = sysconfig.get_path(key)
        if path:
            dirs.add(os.path.normpath(path))
    return dirs


def _is_project_path(filename, include_dirs, excluded_dirs):
    norm = os.path.normpath(filename)

    if "site-packages" in norm.split(os.sep):
        return False

    for excluded in excluded_dirs:
        if norm == excluded or norm.startswith(excluded + os.sep):
            return False

    for include_dir in include_dirs:
        include_norm = os.path.normpath(include_dir)
        if norm == include_norm or norm.startswith(include_norm + os.sep):
            return True

    return False


def extract_project_frames(exception):
    """Return traceback FrameSummary objects for frames inside the project,
    innermost frame last. Skips site-packages and stdlib frames.
    """
    tb = getattr(exception, "__traceback__", None)
    if tb is None:
        return []

    include_dirs = get_include_dirs()
    excluded_dirs = _excluded_dirs()

    return [
        frame
        for frame in traceback.extract_tb(tb)
        if _is_project_path(frame.filename, include_dirs, excluded_dirs)
    ]


def _source_context(frame):
    try:
        with open(frame.filename, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return frame.line or ""

    start = max(frame.lineno - CONTEXT_LINES, 1)
    end = min(frame.lineno + CONTEXT_LINES, len(lines))
    return "".join(lines[start - 1:end])


def build_query_text(exception, frame):
    exc_type = type(exception).__name__
    exc_message = str(exception)
    source = _source_context(frame) if frame is not None else ""
    return f"{exc_type}: {exc_message}\n\n{source}"


def retrieve_chunks(exception, top_k=None):
    """Retrieve the top-k relevant source chunks for `exception`, deduplicated
    by file path. Returns [] if RAG is disabled or the index is missing.
    Raises on any other failure (import, embedding, query) -- callers must
    catch and fall back per the RAG fallback rules.
    """
    if not getattr(settings, "EXPLAIN_ERRORS_RAG_ENABLED", False):
        return []

    index_path = get_index_path()
    if not os.path.exists(index_path):
        logger.warning(
            "explain_errors: RAG index not found at %s; run `manage.py "
            "build_error_index` or disable EXPLAIN_ERRORS_RAG_ENABLED. "
            "Falling back to traceback-only prompt.",
            index_path,
        )
        return []

    frames = extract_project_frames(exception)
    innermost = frames[-1] if frames else None

    query_text = build_query_text(exception, innermost)
    query_text = sanitize_traceback(query_text)

    embed_backend = get_embed_backend()
    embed_model = get_embed_model()
    k = top_k if top_k is not None else getattr(settings, "EXPLAIN_ERRORS_RAG_TOP_K", 4)

    with VectorStore(index_path) as store:
        meta = store.read_meta()
        # Cheap check first, so a mismatch never loads a model or calls an API.
        if meta is not None or embed_backend != "openai":
            _check_index_meta(index_path, meta, embed_backend, embed_model)
        client = get_embeddings_client()
        response = create_embeddings(client, embed_model, [query_text])
        query_embedding = response.data[0].embedding
        _check_index_meta(
            index_path, meta, embed_backend, embed_model, len(query_embedding)
        )
        results = store.query(query_embedding, k)

    deduped = []
    seen_files = set()
    for result in results:
        if result["file_path"] in seen_files:
            continue
        seen_files.add(result["file_path"])
        deduped.append(result)

    return deduped


def format_chunks_for_prompt(chunks, max_chars):
    """Render retrieved chunks under a delimited "Relevant project source:"
    section, truncated to max_chars.
    """
    if not chunks or max_chars <= 0:
        return ""

    parts = ["Relevant project source:"]
    for chunk in chunks:
        header = f"--- {chunk['file_path']}:{chunk['start_line']}-{chunk['end_line']} ---"
        parts.append(f"{header}\n{chunk['chunk_text']}")

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(truncated)..."
    return text
