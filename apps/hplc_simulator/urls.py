from django.urls import path
from . import views

app_name = 'hplc_simulator'

urlpatterns = [
    path('', views.SimulatorIndexView.as_view(), name='index'),
    path(
        'simulator/<slug:slug>/',
        views.SimulatorDetailView.as_view(),
        name='simulator',
    ),
    path('api/levels/', views.LevelListView.as_view(), name='api-levels'),
    path(
        'api/levels/<slug:slug>/',
        views.LevelDetailView.as_view(),
        name='api-level-detail',
    ),
    path('api/simulate/', views.SimulateView.as_view(), name='api-simulate'),
    path('api/scores/', views.ScoreSubmissionView.as_view(), name='api-scores'),
    path(
        'api/scores/history/',
        views.UserScoresView.as_view(),
        name='api-scores-history',
    ),
    path('api/progress/', views.LevelProgressView.as_view(), name='api-progress'),
]
