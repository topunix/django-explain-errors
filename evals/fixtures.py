"""The fixture registry: one record per Django failure the eval harness
drives through the fixture app in evals/fixture_app/.

Group A: the cause lives in the app's own source -- a bad queryset lookup,
a missing null check, a broken model method -- something retrieval can
surface and a traceback-only explanation cannot.
Group B: the traceback text itself already names the cause -- a typo'd
template name, a missing tag library, a bad import -- with no extra source
context required to identify it.

Three runs (see evals/README.md's Results section) show RAG-on winning both
groups, and by a wider margin in group B than in group A -- so group B is
not a RAG-neutral control, it's simply a different class of error, and one
where a traceback-only explanation fabricates specifics more often, not
less.

`expected_fix_location` is `file:function`, not a line number, since line
numbers shift as the fixture app changes.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Fixture:
    name: str
    url: str
    group: str  # "A" or "B"
    expected_exception: str
    expected_cause: str
    expected_fix_location: str
    method: str = "GET"
    data: Optional[dict] = field(default=None)

    def __post_init__(self):
        if self.group not in ("A", "B"):
            raise ValueError(f"Fixture {self.name!r}: group must be 'A' or 'B', got {self.group!r}")
        if self.method not in ("GET", "POST"):
            raise ValueError(f"Fixture {self.name!r}: method must be 'GET' or 'POST', got {self.method!r}")


FIXTURES = [
    # --- Group A: cause lives in the app's own source ---
    Fixture(
        name="none_attribute",
        url="/posts/latest/",
        group="A",
        expected_exception="AttributeError",
        expected_cause=(
            "Post.objects.filter(published_at__isnull=False).first() returned None "
            "because no post has been published yet, and the view used the result "
            "(post.title) without checking for None"
        ),
        expected_fix_location="blog/views.py:latest_post",
    ),
    Fixture(
        name="str_recursion",
        url="/posts/hello-world/comments/",
        group="A",
        expected_exception="RecursionError",
        expected_cause=(
            "Comment.__str__ returns self.summary, and the summary property builds "
            "its string with str(self), which calls __str__ again, recursing forever"
        ),
        expected_fix_location="blog/models.py:Comment",
    ),
    Fixture(
        name="bad_lookup",
        url="/authors/prolific/",
        group="A",
        expected_exception="FieldError",
        expected_cause=(
            "The view filters on post_count, a field that does not exist on Author "
            "and was never added via .annotate(post_count=Count('posts'))"
        ),
        expected_fix_location="blog/views.py:prolific_authors",
    ),
    Fixture(
        name="missing_profile",
        url="/authors/2/bio/",
        group="A",
        expected_exception="RelatedObjectDoesNotExist",
        expected_cause=(
            "The view accesses author.profile for an author that has no related "
            "Profile row, and Profile is an optional one-to-one relation"
        ),
        expected_fix_location="blog/views.py:author_bio",
    ),
    Fixture(
        name="non_unique_get",
        url="/authors/by-name/",
        group="A",
        expected_exception="MultipleObjectsReturned",
        expected_cause=(
            "The view calls Author.objects.get(name=name), but two authors share the "
            "default name 'Grace Hopper' and name is not a unique field"
        ),
        expected_fix_location="blog/views.py:author_by_name",
    ),
    Fixture(
        name="unexpected_kwarg",
        url="/posts/1/preview/2/",
        group="A",
        expected_exception="TypeError",
        expected_cause=(
            "The URL pattern for post-preview captures a 'revision' kwarg that the "
            "post_preview view signature does not accept"
        ),
        expected_fix_location="blog/views.py:post_preview",
    ),
    Fixture(
        name="unvalidated_int",
        url="/posts/page/?page=oops",
        group="A",
        expected_exception="ValueError",
        expected_cause=(
            "The view does int(request.GET['page']) with no validation, and the "
            "page query parameter is not a valid integer"
        ),
        expected_fix_location="blog/views.py:post_page",
    ),
    Fixture(
        name="missing_fk",
        url="/posts/clone-latest/",
        group="A",
        expected_exception="IntegrityError",
        expected_cause=(
            "The view creates a new Post to clone the latest one but never sets "
            "author, which is a required (non-nullable) foreign key"
        ),
        expected_fix_location="blog/views.py:clone_latest_post",
    ),
    Fixture(
        name="missing_post_key",
        url="/comments/create/",
        group="A",
        expected_exception="KeyError",
        expected_cause=(
            "The view reads request.POST['email'] to build a confirmation message, "
            "but the comment form does not send an email field"
        ),
        expected_fix_location="blog/views.py:create_comment",
        method="POST",
        data={"post_id": "1", "name": "Reader Two", "body": "Nice!"},
    ),
    Fixture(
        name="get_not_404",
        url="/posts/no-such-post/edit/",
        group="A",
        expected_exception="Post.DoesNotExist",
        expected_cause=(
            "The view calls Post.objects.get(slug=slug) for a slug that does not "
            "exist instead of using get_object_or_404, which would return a 404"
        ),
        expected_fix_location="blog/views.py:edit_post",
    ),
    # --- Group B: the traceback already says everything ---
    Fixture(
        name="template_typo",
        url="/posts/hello-world/print/",
        group="B",
        expected_exception="TemplateDoesNotExist",
        expected_cause=(
            "The view renders 'blog/post_detial.html', a typo of the actual "
            "template file blog/post_detail.html"
        ),
        expected_fix_location="blog/views.py:print_post",
    ),
    Fixture(
        name="url_name_typo",
        url="/posts/hello-world/related/",
        group="B",
        expected_exception="NoReverseMatch",
        expected_cause=(
            "The related_posts.html template does {% url 'post-detial' %}, a typo "
            "of the registered URL name 'post-detail'"
        ),
        expected_fix_location="blog/templates/blog/related_posts.html",
    ),
    Fixture(
        name="missing_tag_library",
        url="/posts/hello-world/timeago/",
        group="B",
        expected_exception="TemplateSyntaxError",
        expected_cause=(
            "post_timeago.html does {% load humanize %} but "
            "django.contrib.humanize is not in INSTALLED_APPS"
        ),
        expected_fix_location="fixture_app/settings.py:INSTALLED_APPS",
    ),
    Fixture(
        name="import_typo",
        url="/posts/export/",
        group="B",
        expected_exception="ModuleNotFoundError",
        expected_cause=(
            "export_posts_csv imports 'csvv', a typo of the standard library "
            "module 'csv'"
        ),
        expected_fix_location="blog/views.py:export_posts_csv",
    ),
    Fixture(
        name="unclosed_tag",
        url="/posts/archive/",
        group="B",
        expected_exception="TemplateSyntaxError",
        expected_cause=(
            "post_archive.html opens an {% if %} inside the posts loop but never "
            "closes it with {% endif %} before {% endfor %}"
        ),
        expected_fix_location="blog/templates/blog/post_archive.html",
    ),
]

FIXTURES_BY_NAME = {fixture.name: fixture for fixture in FIXTURES}
