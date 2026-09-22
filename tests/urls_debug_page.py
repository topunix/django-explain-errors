from django.urls import path


def boom_view(request):
    raise ValueError("boom")


async def boom_view_async(request):
    raise ValueError("async boom")


urlpatterns = [
    path("boom/", boom_view),
    path("boom-async/", boom_view_async),
]
