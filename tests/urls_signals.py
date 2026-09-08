from django.urls import path


def boom_view(request):
    raise ValueError("boom")


urlpatterns = [path("boom/", boom_view)]
