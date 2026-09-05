from django.core.management.base import BaseCommand, CommandError

from explain_errors.rag.indexer import build_index


class Command(BaseCommand):
    help = (
        "Build (or rebuild) the local vector index used to ground "
        "explain_errors explanations in project source code."
    )

    def handle(self, *args, **options):
        # Imported lazily to mirror the indexer's optional-dependency pattern.
        from openai import OpenAIError

        try:
            result = build_index()
        except OpenAIError as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            raise CommandError(
                f"OpenAI API request failed ({code}). Check your API key and "
                "billing at platform.openai.com. Index not built."
            ) from exc
        self.stdout.write(
            f"explain_errors: scanned {result['files_scanned']} files, "
            f"embedded {result['chunks_embedded']} chunks.\n"
            f"Index written to {result['index_path']}"
        )
