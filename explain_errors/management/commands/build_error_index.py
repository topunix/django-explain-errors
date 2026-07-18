from django.core.management.base import BaseCommand

from explain_errors.rag.indexer import build_index


class Command(BaseCommand):
    help = (
        "Build (or rebuild) the local vector index used to ground "
        "explain_errors explanations in project source code."
    )

    def handle(self, *args, **options):
        result = build_index()
        self.stdout.write(
            f"explain_errors: scanned {result['files_scanned']} files, "
            f"embedded {result['chunks_embedded']} chunks.\n"
            f"Index written to {result['index_path']}"
        )
