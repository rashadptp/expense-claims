from django.conf import settings


def policy(request):
    """Expose branding + currency + policy thresholds to every template."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_TAGLINE": settings.SITE_TAGLINE,
        "CURRENCY": settings.CURRENCY,
        "AUTO_APPROVE_THRESHOLD": settings.AUTO_APPROVE_THRESHOLD,
        "HIGH_VALUE_THRESHOLD": settings.HIGH_VALUE_THRESHOLD,
        "AI_PROVIDER": settings.AI_PROVIDER,
    }
