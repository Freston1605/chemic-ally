from django.urls import path
from .views import LandingPage

urlpatterns = [
    path('', LandingPage.as_view(), name='landing'),
    # other URL patterns...
]
