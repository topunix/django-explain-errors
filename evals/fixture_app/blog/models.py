from django.db import models


class Author(models.Model):
    """A blog contributor."""

    name = models.CharField(max_length=100)
    email = models.EmailField()

    def __str__(self):
        return self.name


class Profile(models.Model):
    """Optional extra details about an author. Not every author has one."""

    author = models.OneToOneField(
        Author, on_delete=models.CASCADE, related_name="profile"
    )
    bio = models.TextField(blank=True)
    website = models.URLField(blank=True)

    def __str__(self):
        return f"Profile of {self.author.name}"


class Post(models.Model):
    """A blog post. Drafts have no published_at."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="posts")
    body = models.TextField()
    published_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title


class Comment(models.Model):
    """A comment left on a post."""

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    author_name = models.CharField(max_length=100)
    body = models.TextField()

    def __str__(self):
        return self.summary

    @property
    def summary(self):
        # Meant to trim the body for display, but calls back into __str__
        # instead of formatting self.body directly -- infinite recursion.
        return f"{self.author_name}: {str(self)}"
