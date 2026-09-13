from django.urls import path

from . import views

urlpatterns = [
    path("posts/latest/", views.latest_post, name="latest-post"),
    path("posts/<slug:slug>/comments/", views.post_comments, name="post-comments"),
    path("authors/prolific/", views.prolific_authors, name="prolific-authors"),
    path("authors/<int:author_id>/bio/", views.author_bio, name="author-bio"),
    path("authors/by-name/", views.author_by_name, name="author-by-name"),
    path(
        "posts/<int:post_id>/preview/<int:revision>/",
        views.post_preview,
        name="post-preview",
    ),
    path("posts/page/", views.post_page, name="post-page"),
    path("posts/clone-latest/", views.clone_latest_post, name="clone-latest-post"),
    path("comments/create/", views.create_comment, name="create-comment"),
    path("posts/<slug:slug>/edit/", views.edit_post, name="post-edit"),
    path("posts/<slug:slug>/print/", views.print_post, name="post-detail"),
    path("posts/<slug:slug>/related/", views.related_posts, name="related-posts"),
    path("posts/<slug:slug>/timeago/", views.post_timeago, name="post-timeago"),
    path("posts/export/", views.export_posts_csv, name="export-posts-csv"),
    path("posts/archive/", views.post_archive, name="post-archive"),
]
