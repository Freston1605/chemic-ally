from django.views.generic import TemplateView

class LandingPage(TemplateView):
    """Landing Page

    Args:
        TemplateView (Class): Class Based Django View
    """
    template_name = 'landing.html'