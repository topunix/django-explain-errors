"""Deterministic seed data for the fixture app.

Called once per harness run against a freshly migrated (empty) SQLite
database, so the autoincrement ids assumed by evals/fixtures.py URLs
(e.g. author id 2, post id 1) are stable run over run.
"""
from blog.models import Author, Comment, Post, Profile


def seed():
    ada = Author.objects.create(name="Ada Lovelace", email="ada@example.com")
    Profile.objects.create(
        author=ada,
        bio="Mathematician and the first programmer.",
        website="https://example.com/ada",
    )
    Author.objects.create(name="Alex Kim", email="alex@example.com")  # id 2, no profile
    Author.objects.create(name="Grace Hopper", email="grace1@example.com")  # id 3
    Author.objects.create(name="Grace Hopper", email="grace2@example.com")  # id 4, duplicate name

    hello_world = Post.objects.create(
        title="Hello World",
        slug="hello-world",
        author=ada,
        body="My first post.",
    )  # id 1, draft (no published_at)
    Post.objects.create(
        title="Second Post",
        slug="second-post",
        author=ada,
        body="Another one.",
    )  # id 2, draft

    Comment.objects.create(post=hello_world, author_name="Reader", body="Great post!")
