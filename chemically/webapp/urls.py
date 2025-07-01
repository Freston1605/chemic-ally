from django.urls import path
from .views import (
    LandingPage,
    CalculateMolecularWeightView,
    BalanceChemicalReaction,
    CalculateDilutionView,
    DashboardView,
)

urlpatterns = [
    path('', LandingPage.as_view(), name='landing'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path(
        'calculate/molecular_weight',
        CalculateMolecularWeightView.as_view(),
        name='molecular_weight',
    ),
    path(
        'calculate/reaction_balancer',
        BalanceChemicalReaction.as_view(),
        name='reaction_balancer',
    ),
    path(
        'calculate/dilution',
        CalculateDilutionView.as_view(),
        name='dilution',
    ),
]
