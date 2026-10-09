"""Frame-aware traceback formatting for the explanation prompt.

Keeps the application's own frames over library internals when trimming to
a character budget, instead of slicing the last N characters of the raw
traceback.
"""
import functools
import hashlib
import os
import sysconfig
import traceback

import django

_LIBRARY_DIR_MARKERS = ("site-packages", "dist-packages")

_OMITTED_LINE = "  ... {count} library frames omitted ...\n"


@functools.lru_cache(maxsize=1)
def _library_dirs():
    dirs = []
    for key in ("stdlib", "platstdlib"):
        path = sysconfig.get_paths().get(key)
        if path:
            dirs.append(os.path.normpath(path))
    dirs.append(os.path.normpath(os.path.dirname(django.__file__)))
    return tuple(dirs)


def _is_library_frame(filename):
    norm = os.path.normpath(filename)
    if any(marker in norm.split(os.sep) for marker in _LIBRARY_DIR_MARKERS):
        return True
    for library_dir in _library_dirs():
        if norm == library_dir or norm.startswith(library_dir + os.sep):
            return True
    return False


def _tail_slice_fallback(exc, max_chars):
    # Built from the exception object, not traceback.format_exc(): the async
    # path runs process_exception in a sync_to_async worker thread, where
    # sys.exc_info() (which format_exc() reads) is empty, and format_exc()
    # would silently produce "NoneType: None". format_exception() takes the
    # exception explicitly and also handles exc.__traceback__ being None.
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if len(tb) > max_chars:
        tb = "...(truncated)...\n" + tb[-max_chars:]
    return tb


def format_traceback(exc: BaseException, max_chars: int) -> str:
    """Render `exc`'s traceback in standard format, keeping every application
    frame and spending the remaining `max_chars` budget on library frames
    nearest the raise point.
    """
    tb = exc.__traceback__
    if tb is None:
        return _tail_slice_fallback(exc, max_chars)

    frame_summaries = traceback.extract_tb(tb)
    header_text = "".join(traceback.format_exception_only(type(exc), exc))
    base_prefix = "Traceback (most recent call last):\n"
    is_library = [_is_library_frame(f.filename) for f in frame_summaries]
    frame_texts = ["".join(traceback.format_list([f])) for f in frame_summaries]
    app_indices = {i for i, library in enumerate(is_library) if not library}

    def render(kept):
        parts = [base_prefix]
        i = 0
        n = len(frame_summaries)
        while i < n:
            if i in kept:
                parts.append(frame_texts[i])
                i += 1
                continue
            start = i
            while i < n and i not in kept:
                i += 1
            parts.append(_OMITTED_LINE.format(count=i - start))
        parts.append(header_text)
        return "".join(parts)

    baseline = render(app_indices)
    if len(baseline) > max_chars:
        # Application frames alone blow the budget; the tail-slice fallback
        # at least guarantees the result stays under max_chars.
        return _tail_slice_fallback(exc, max_chars)

    kept = set(app_indices)
    best = baseline
    # Nearest the raise point (end of the list) first, since those library
    # frames are the most relevant internals.
    for i in range(len(frame_summaries) - 1, -1, -1):
        if not is_library[i]:
            continue
        trial_kept = kept | {i}
        trial = render(trial_kept)
        if len(trial) <= max_chars:
            kept = trial_kept
            best = trial
        else:
            break

    return best


def exception_dedup_key(exc: BaseException):
    """Return a stable hash identifying "the same error" for replay purposes,
    or None when the exception carries no traceback to key on.

    The key covers the exception type's qualified name, the exception
    message, and the innermost project frame's filename, line number and
    stripped source text. The message is included, so "id 41 not found" and
    "id 42 not found" at one site are different errors. With no project frames the
    innermost frame is used. traceback.extract_tb refreshes linecache, so
    editing the offending line yields a new key. Built from the raw exception,
    not the sanitized traceback.
    """
    tb = exc.__traceback__
    if tb is None:
        return None
    frames = traceback.extract_tb(tb)
    if not frames:
        return None
    frame = frames[-1]
    for candidate in reversed(frames):
        if not _is_library_frame(candidate.filename):
            frame = candidate
            break
    exc_type = type(exc)
    parts = (
        f"{exc_type.__module__}.{exc_type.__qualname__}",
        str(exc),
        frame.filename,
        str(frame.lineno),
        (frame.line or "").strip(),
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8", "replace")).hexdigest()
