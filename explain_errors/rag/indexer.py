"""File discovery, chunking, and embedding for the RAG source index."""
import ast
import os

from django.conf import settings

from ..sanitize import sanitize_traceback
from .store import VectorStore

CHUNK_EXTENSIONS = (".py", ".html", ".txt")

DEFAULT_EXCLUDE = [
    "migrations",
    "venv",
    ".venv",
    "env",
    "node_modules",
    "static",
    "media",
    ".git",
]

WINDOW_LINES = 80
WINDOW_OVERLAP = 20

EMBED_BATCH_SIZE = 100


def get_include_dirs():
    include = getattr(settings, "EXPLAIN_ERRORS_RAG_INCLUDE", None)
    if include:
        return [str(d) for d in include]
    base_dir = getattr(settings, "BASE_DIR", None)
    if not base_dir:
        raise ValueError(
            "EXPLAIN_ERRORS_RAG_INCLUDE is not set and settings.BASE_DIR is undefined."
        )
    return [str(base_dir)]


def get_exclude_dirs():
    exclude = getattr(settings, "EXPLAIN_ERRORS_RAG_EXCLUDE", None)
    if exclude is None:
        return list(DEFAULT_EXCLUDE)
    return [str(d) for d in exclude]


def get_index_path():
    path = getattr(settings, "EXPLAIN_ERRORS_RAG_INDEX_PATH", None)
    if path:
        return str(path)
    base_dir = getattr(settings, "BASE_DIR", None)
    if not base_dir:
        raise ValueError(
            "EXPLAIN_ERRORS_RAG_INDEX_PATH is not set and settings.BASE_DIR is undefined."
        )
    return os.path.join(str(base_dir), ".explain_errors_index.db")


def get_embed_model():
    return getattr(settings, "EXPLAIN_ERRORS_RAG_EMBED_MODEL", "text-embedding-3-small")


def get_openai_client():
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", getattr(settings, "OPENAI_API_KEY", None))
    if not api_key:
        raise ValueError(
            "OpenAI API key not found. Please set the OPENAI_API_KEY "
            "environment variable."
        )
    return OpenAI(api_key=api_key)


def discover_files(include_dirs, exclude_dirs):
    """Walk include_dirs, pruning excluded directory names, keep chunkable files."""
    exclude_set = set(exclude_dirs)
    files = []
    for include_dir in include_dirs:
        for root, dirs, filenames in os.walk(include_dir):
            dirs[:] = sorted(d for d in dirs if d not in exclude_set)
            for filename in sorted(filenames):
                if filename.endswith(CHUNK_EXTENSIONS):
                    files.append(os.path.join(root, filename))
    return files


def chunk_by_lines(text, window=WINDOW_LINES, overlap=WINDOW_OVERLAP):
    lines = text.splitlines()
    if not lines:
        return []

    step = max(window - overlap, 1)
    chunks = []
    start = 0
    while start < len(lines):
        end = min(start + window, len(lines))
        chunk_text = "\n".join(lines[start:end])
        chunks.append((start + 1, end, chunk_text))
        if end == len(lines):
            break
        start += step
    return chunks


def chunk_python_text(text):
    """Chunk by top-level function/class via ast; fall back to line windows."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return chunk_by_lines(text)

    lines = text.splitlines()
    chunks = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            chunk_text = "\n".join(lines[start - 1:end])
            chunks.append((start, end, chunk_text))

    if not chunks:
        return chunk_by_lines(text)
    return chunks


def chunk_file(path):
    """Return list of (start_line, end_line, chunk_text) for a single file."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return []

    if path.endswith(".py"):
        return chunk_python_text(text)
    return chunk_by_lines(text)


def _embed_texts(client, model, texts):
    embeddings = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i:i + EMBED_BATCH_SIZE]
        response = client.embeddings.create(model=model, input=batch)
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


def build_index():
    """Rebuild the RAG index. Idempotent: writes to a temp file then renames
    it into place atomically, so a failed or concurrent build never leaves a
    partial index.
    """
    include_dirs = get_include_dirs()
    exclude_dirs = get_exclude_dirs()
    index_path = get_index_path()
    embed_model = get_embed_model()

    files = discover_files(include_dirs, exclude_dirs)

    raw_chunks = []  # (file_path, start_line, end_line, text)
    for path in files:
        for start_line, end_line, text in chunk_file(path):
            if not text.strip():
                continue
            raw_chunks.append((path, start_line, end_line, text))

    sanitized_texts = [sanitize_traceback(text) for _, _, _, text in raw_chunks]

    embeddings = []
    if sanitized_texts:
        client = get_openai_client()
        embeddings = _embed_texts(client, embed_model, sanitized_texts)

    tmp_path = f"{index_path}.tmp-{os.getpid()}"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    dimensions = len(embeddings[0]) if embeddings else 1536
    with VectorStore(tmp_path) as store:
        store.create(dimensions)
        store.add(
            (file_path, start_line, end_line, sanitized_text, embedding)
            for (file_path, start_line, end_line, _text), sanitized_text, embedding in zip(
                raw_chunks, sanitized_texts, embeddings
            )
        )

    os.replace(tmp_path, index_path)

    return {
        "files_scanned": len(files),
        "chunks_embedded": len(raw_chunks),
        "index_path": index_path,
    }
