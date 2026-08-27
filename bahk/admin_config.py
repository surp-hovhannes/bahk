"""Django admin application configuration for the Fast & Pray admin site."""

from django.contrib.admin.apps import AdminConfig


class FastAndPrayAdminConfig(AdminConfig):
    """Install the branded admin as Django's default admin site."""

    default_site = "bahk.admin_site.FastAndPrayAdminSite"
