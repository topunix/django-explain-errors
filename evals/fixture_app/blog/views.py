from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from .models import Author, Comment, Post


def latest_post(request):
    """Show the most recently published post on the homepage."""
    post = Post.objects.filter(published_at__isnull=False).order_by("-published_at").first()
    return render(request, "blog/post_detail.html", {"post": post, "title": post.title})


def post_comments(request, slug):
    """List the comments left on a post, newest first."""
    post = get_object_or_404(Post, slug=slug)
    lines = [str(comment) for comment in post.comments.order_by("-id")]
    return HttpResponse("\n".join(lines) or "No comments yet.")


def prolific_authors(request):
    """List authors who have written more than one post."""
    # Intended: Author.objects.annotate(post_count=Count("posts")).filter(post_count__gt=1)
    authors = Author.objects.filter(post_count__gt=1)
    return render(request, "blog/author_list.html", {"authors": authors})


def author_bio(request, author_id):
    """Show an author's bio, pulled from their profile."""
    author = get_object_or_404(Author, pk=author_id)
    return HttpResponse(author.profile.bio)


def author_by_name(request):
    """Look up an author by their display name."""
    name = request.GET.get("name", "Grace Hopper")
    author = Author.objects.get(name=name)
    return render(request, "blog/author_detail.html", {"author": author})


def post_preview(request, post_id):
    """Render a quick preview of a post, skipping the comment thread."""
    post = get_object_or_404(Post, pk=post_id)
    return render(request, "blog/post_preview.html", {"post": post})


def post_page(request):
    """Paginate the post list, five per page."""
    page = int(request.GET["page"])
    start = (page - 1) * 5
    posts = Post.objects.order_by("id")[start:start + 5]
    return render(request, "blog/post_list.html", {"posts": posts})


def clone_latest_post(request):
    """Duplicate the most recent post as a new draft, ready for editing."""
    latest = Post.objects.order_by("-id").first()
    draft = Post.objects.create(
        title=f"Copy of {latest.title}",
        slug=f"copy-of-{latest.slug}",
        body=latest.body,
    )
    return HttpResponse(f"Created draft #{draft.id}")


def create_comment(request):
    """Handle a comment submitted from the post detail page's form."""
    # .dict() up front so downstream code works with plain dict semantics
    # instead of QueryDict's multi-value behavior.
    form = request.POST.dict()
    post = get_object_or_404(Post, pk=form["post_id"])
    Comment.objects.create(
        post=post,
        author_name=form["name"],
        body=form["body"],
    )
    return HttpResponse(
        "Thanks! We'll email " + form["email"] + " when your comment is approved."
    )


def edit_post(request, slug):
    """Look up a post for the editing form."""
    post = Post.objects.get(slug=slug)
    return render(request, "blog/post_edit.html", {"post": post})


def print_post(request, slug):
    """Render a print-friendly version of a post."""
    post = get_object_or_404(Post, slug=slug)
    return render(request, "blog/post_detial.html", {"post": post})


def related_posts(request, slug):
    """Show a post along with a link to other posts by the same author."""
    post = get_object_or_404(Post, slug=slug)
    return render(request, "blog/related_posts.html", {"post": post})


def post_timeago(request, slug):
    """Show a post's publish time as a friendly relative timestamp."""
    post = get_object_or_404(Post, slug=slug)
    return render(request, "blog/post_timeago.html", {"post": post})


def export_posts_csv(request):
    """Export all posts as CSV for an offline backup."""
    import csvv

    buffer = []
    writer = csvv.writer(buffer)
    for post in Post.objects.order_by("id"):
        writer.writerow([post.id, post.title])
    return HttpResponse("\n".join(buffer), content_type="text/csv")


def post_archive(request):
    """Show every post grouped by whether it has been published."""
    posts = Post.objects.order_by("-id")
    return render(request, "blog/post_archive.html", {"posts": posts})
