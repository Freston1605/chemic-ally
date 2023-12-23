from django.urls import path
from .views import LandingPage, CalculateMolecularWeightView, BalanceChemicalReaction

urlpatterns = [
    path('', LandingPage.as_view(), name='landing'),
    path('calculate/molecular_weight', CalculateMolecularWeightView.as_view(), name='calculate_molecular_weight'),
    path('calculate/reaction_balancer', BalanceChemicalReaction.as_view(), name='reaction_balancer'),
]
