import logging

from django.utils.html import escape
from django.views.debug import ExceptionReporter

logger = logging.getLogger(__name__)

BANNER_TEMPLATE = """
<section id="explain-errors" style="border: 1px solid #ccc; background: #fdfdf0; \
font-family: sans-serif; padding: 10px 15px; margin: 10px 0;">
  <h3 style="margin: 0 0 8px 0;">Explanation (django-explain-errors)</h3>
  <div style="white-space: pre-wrap; max-height: 16em; overflow-y: auto;">{explanation}</div>
</section>
"""

ANCHOR = '<table class="meta">'


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
            banner = BANNER_TEMPLATE.format(explanation=escape(explanation))
            return html[:anchor_index] + banner + html[anchor_index:]
        except Exception:
            logger.warning(
                "explain_errors: failed to inject debug page banner", exc_info=True
            )
            return html
