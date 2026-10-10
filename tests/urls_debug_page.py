from django.http import HttpResponse
from django.urls import path


def boom_view(request):
    raise ValueError("boom")


async def boom_view_async(request):
    raise ValueError("async boom")


async def ok_view_async(request):
    return HttpResponse("ok")


urlpatterns = [
    path("boom/", boom_view),
    path("boom-async/", boom_view_async),
    path("ok-async/", ok_view_async),
]
