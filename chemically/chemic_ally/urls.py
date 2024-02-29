from django.urls import path
from .views import LandingPage, CalculateMolecularWeightView, BalanceChemicalReaction, CalculateDilutionView

urlpatterns = [
    path('', LandingPage.as_view(), name='landing'),
    path('calculate/molecular_weight', CalculateMolecularWeightView.as_view(), name='calculate_molecular_weight'),
    path('calculate/reaction_balancer', BalanceChemicalReaction.as_view(), name='reaction_balancer'),
    path('calculate/dilution', CalculateDilutionView.as_view(), name="calculate_dilution"),
]
