import logging
import os
import re
import traceback
from urllib.parse import quote

from django.conf import settings
from django.utils.html import escape
from django.views.debug import ExceptionReporter

from explain_errors.tracebacks import _is_library_frame

logger = logging.getLogger(__name__)

BANNER_TEMPLATE = """
<section id="explain-errors" style="border: 1px solid #ccc; background: #fdfdf0; \
font-family: sans-serif; padding: 10px 15px; margin: 10px 0;">
  <h3 style="margin: 0 0 8px 0;">Explanation (django-explain-errors)</h3>
  <div>{explanation}</div>
</section>
"""

ANCHOR = '<table class="meta">'

_CODE_STYLE = "font-family: monospace; background: #eee;"
_PRE_STYLE = (
    "font-family: monospace; background: #eee; padding: 8px; "
    "overflow-x: auto; white-space: pre-wrap; margin: 4px 0;"
)
_PRE_WITH_BUTTON_STYLE = (
    "font-family: monospace; background: #eee; padding: 8px 60px 8px 8px; "
    "overflow-x: auto; white-space: pre-wrap; margin: 0;"
)
_P_STYLE = "margin: 6px 0;"
_UL_STYLE = "margin: 6px 0; padding-inline-start: 1.5em; list-style: disc;"
_OL_STYLE = "margin: 6px 0; padding-inline-start: 1.5em; list-style: decimal;"
_LI_STYLE = "margin: 2px 0;"
_CODE_WRAPPER_STYLE = "position: relative; margin: 4px 0;"
_COPY_BUTTON_STYLE = (
    "position: absolute; top: 4px; right: 4px; font-family: sans-serif; "
    "font-size: 11px; line-height: 1; padding: 3px 7px; cursor: pointer; "
    "background: #fff; border: 1px solid #999; border-radius: 3px; color: #333;"
)
_COPY_BUTTON_CLASS = "explain-errors-copy-btn"
_CODE_WRAPPER_CLASS = "explain-errors-code-wrapper"

_COPY_SCRIPT = """
<script>
(function () {{
  var container = document.currentScript && document.currentScript.closest("#explain-errors");
  if (!container) {{
    return;
  }}
  container.addEventListener("click", function (event) {{
    var btn = event.target.closest("button[data-copy]");
    if (!btn) {{
      return;
    }}
    var wrapper = btn.closest(".{wrapper_class}");
    var code = wrapper && wrapper.querySelector("code");
    if (!code) {{
      return;
    }}
    var text = code.textContent;
    var showCopied = function () {{
      if (btn._explainErrorsTimer) {{
        clearTimeout(btn._explainErrorsTimer);
      }}
      btn.textContent = "Copied";
      btn._explainErrorsTimer = setTimeout(function () {{
        btn.textContent = "Copy";
        btn._explainErrorsTimer = null;
      }}, 1500);
    }};
    var fallbackCopy = function () {{
      try {{
        var range = document.createRange();
        range.selectNodeContents(code);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        var ok = document.execCommand("copy");
        selection.removeAllRanges();
        if (ok) {{
          showCopied();
        }}
      }} catch (err) {{
        /* both copy paths failed; do nothing */
      }}
    }};
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(showCopied, fallbackCopy);
    }} else {{
      fallbackCopy();
    }}
  }});
}})();
</script>
""".format(
    wrapper_class=_CODE_WRAPPER_CLASS
)

_LANG_TAG_RE = re.compile(r"^[A-Za-z0-9_+-]*$")
_INLINE_CODE_RE = re.compile(r"(`[^`\n]*`)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_UL_RE = re.compile(r"^[-*]\s")
_UL_STRIP_RE = re.compile(r"^[-*]\s+")
_OL_RE = re.compile(r"^\d+\.\s")
_OL_STRIP_RE = re.compile(r"^\d+\.\s+")


def render_explanation_html(text):
    """Render a small, safe Markdown subset of a model explanation as HTML.

    Every segment is escaped before any transform runs, and code content
    (fenced or inline) never goes through the bold/list transforms. Falls
    back to escaped plain text (matching the pre-`render_explanation_html`
    behavior) if anything above raises.
    """
    try:
        return _render(text)
    except Exception:
        logger.warning(
            "explain_errors: failed to render explanation markdown", exc_info=True
        )
        return '<div style="white-space: pre-wrap;">{}</div>'.format(escape(text))


def _render(text):
    parts = text.split("```")
    rendered = []
    has_copy_button = False
    for index, part in enumerate(parts):
        if index % 2 == 1:
            block_html, has_button = _render_code_block(part)
            rendered.append(block_html)
            has_copy_button = has_copy_button or has_button
        else:
            rendered.append(_render_prose(escape(part)))
    html = "".join(rendered)
    if has_copy_button:
        html += _COPY_SCRIPT
    return html


def _render_code_block(segment):
    code = segment
    if "\n" in segment:
        first_line, rest = segment.split("\n", 1)
        if _LANG_TAG_RE.match(first_line):
            code = rest
    if code.endswith("\n"):
        code = code[:-1]
    if not code.strip():
        return (
            '<pre dir="ltr" style="{pre_style}"><code dir="ltr">{code}</code></pre>'
        ).format(pre_style=_PRE_STYLE, code=escape(code)), False
    return (
        '<div class="{wrapper_class}" style="{wrapper_style}">'
        '<button type="button" class="{btn_class}" data-copy '
        'aria-label="Copy code" style="{btn_style}">Copy</button>'
        '<pre dir="ltr" style="{pre_style}"><code dir="ltr">{code}</code></pre>'
        "</div>"
    ).format(
        wrapper_class=_CODE_WRAPPER_CLASS,
        wrapper_style=_CODE_WRAPPER_STYLE,
        btn_class=_COPY_BUTTON_CLASS,
        btn_style=_COPY_BUTTON_STYLE,
        pre_style=_PRE_WITH_BUTTON_STYLE,
        code=escape(code),
    ), True


def _render_prose(escaped_text):
    """Render an already-escaped, non-code segment: paragraphs, lists, breaks."""
    blocks = []
    block_type = None
    block_lines = []

    def flush():
        if not block_lines:
            return
        if block_type == "ul":
            items = "".join(
                '<li style="{}">{}</li>'.format(
                    _LI_STYLE, _render_inline(_UL_STRIP_RE.sub("", line, count=1))
                )
                for line in block_lines
            )
            blocks.append('<ul dir="auto" style="{}">{}</ul>'.format(_UL_STYLE, items))
        elif block_type == "ol":
            items = "".join(
                '<li style="{}">{}</li>'.format(
                    _LI_STYLE, _render_inline(_OL_STRIP_RE.sub("", line, count=1))
                )
                for line in block_lines
            )
            blocks.append('<ol dir="auto" style="{}">{}</ol>'.format(_OL_STYLE, items))
        else:
            content = "<br>".join(_render_inline(line) for line in block_lines)
            blocks.append('<p dir="auto" style="{}">{}</p>'.format(_P_STYLE, content))

    for raw_line in escaped_text.split("\n"):
        line = raw_line.strip()
        if line == "":
            flush()
            block_type = None
            block_lines = []
            continue
        if _UL_RE.match(line):
            line_type = "ul"
        elif _OL_RE.match(line):
            line_type = "ol"
        else:
            line_type = "p"
        if line_type != block_type:
            flush()
            block_type = line_type
            block_lines = []
        block_lines.append(line)
    flush()

    return "".join(blocks)


def _render_inline(escaped_line):
    """Apply inline code and bold to an already-escaped line, skipping code spans."""
    pieces = _INLINE_CODE_RE.split(escaped_line)
    rendered = []
    for index, piece in enumerate(pieces):
        if index % 2 == 1:
            rendered.append(
                '<code dir="ltr" style="{}">{}</code>'.format(_CODE_STYLE, piece[1:-1])
            )
        else:
            rendered.append(_BOLD_RE.sub(r"<strong>\1</strong>", piece))
    return "".join(rendered)


_BLOCKED_SCHEMES = ("javascript:", "data:", "vbscript:")
_LINKS_BLOCK_STYLE = "margin: 8px 0 0 0; font-size: 13px;"
_LINKS_TITLE_STYLE = "font-weight: bold; margin: 6px 0 2px 0;"
_LINK_LIST_STYLE = "margin: 2px 0; padding-inline-start: 1.5em; list-style: disc;"


def _editor_url(template, path, line):
    """Render the editor URL template, or None if the scheme is blocked.

    Raises KeyError, IndexError or ValueError for an invalid template.
    """
    url = template.format(path=quote(path, safe="/:\\"), line=int(line))
    if url.strip().lower().startswith(_BLOCKED_SCHEMES):
        logger.debug("explain_errors: editor link with a blocked scheme skipped")
        return None
    return url


def _link_items(template, entries):
    items = []
    for path, line, label in entries:
        url = _editor_url(template, path, line)
        if url is None:
            continue
        items.append(
            '<li style="{}"><a href="{}" rel="noopener noreferrer">{}</a></li>'.format(
                _LI_STYLE, escape(url), escape(label)
            )
        )
    return items


def _build_links_html(tb, cited_files):
    """HTML for the "Open in editor" block, or "" when off, empty or invalid."""
    template = getattr(settings, "EXPLAIN_ERRORS_EDITOR_URL", None)
    if not template or not settings.DEBUG:
        return ""

    frames = []
    for frame in traceback.extract_tb(tb):
        if _is_library_frame(frame.filename):
            continue
        frames.append(
            (
                frame.filename,
                frame.lineno,
                "{}:{} in {}".format(frame.filename, frame.lineno, frame.name),
            )
        )

    cited = []
    seen = set()
    for file_path, start_line in cited_files or ():
        if (file_path, start_line) in seen:
            continue
        seen.add((file_path, start_line))
        abs_path = file_path if os.path.isabs(file_path) else os.path.abspath(file_path)
        cited.append((abs_path, start_line, "{}:{}".format(file_path, start_line)))

    try:
        sections = []
        for title, entries in (
            ("Traceback frames", frames),
            ("Retrieved source", cited),
        ):
            items = _link_items(template, entries)
            if items:
                sections.append(
                    '<div style="{}">{}</div><ul style="{}">{}</ul>'.format(
                        _LINKS_TITLE_STYLE, title, _LINK_LIST_STYLE, "".join(items)
                    )
                )
    except (KeyError, IndexError, ValueError):
        logger.warning(
            "explain_errors: invalid EXPLAIN_ERRORS_EDITOR_URL template, "
            "no editor links rendered",
            exc_info=True,
        )
        return ""

    if not sections:
        return ""
    return '<div style="{}"><div style="{}">Open in editor</div>{}</div>'.format(
        _LINKS_BLOCK_STYLE, _LINKS_TITLE_STYLE, "".join(sections)
    )


class ExplainErrorsExceptionReporter(ExceptionReporter):
    """Injects the explain_errors explanation into the technical 500 page.

    Fails open: any problem producing the banner falls back to the
    unmodified parent HTML, since the debug page must never break because
    of this code.
    """

    def get_traceback_html(self):
        html = super().get_traceback_html()

        try:
            request = self.request
            if request is None:
                return html

            explanation = getattr(request, "_explain_errors_explanation", None)
            if explanation is None:
                return html

            anchor_index = html.index(ANCHOR)
            banner = BANNER_TEMPLATE.format(explanation=render_explanation_html(explanation))
            try:
                links = _build_links_html(
                    self.tb, getattr(request, "_explain_errors_cited_files", None)
                )
            except Exception:
                logger.warning(
                    "explain_errors: failed to build editor links", exc_info=True
                )
                links = ""
            if links:
                end = banner.rindex("</section>")
                banner = banner[:end] + links + "\n" + banner[end:]
            return html[:anchor_index] + banner + html[anchor_index:]
        except Exception:
            logger.warning(
                "explain_errors: failed to inject debug page banner", exc_info=True
            )
            return html
