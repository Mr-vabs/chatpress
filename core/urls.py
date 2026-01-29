from django.contrib import admin
from django.urls import path
from bot.views import home, tag_view  # <--- Import view tag_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home, name='home'),  # <--- Homepage link
    path('tag/<str:tag_name>/', tag_view, name='tag_view'), # New Route
    # ... static media settings ...
]
