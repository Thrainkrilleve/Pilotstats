"""App Configuration"""

# Django
from django.apps import AppConfig

# Pilot Stats
from aa_pilotstats import __version__


class PilotstatsConfig(AppConfig):
    """App Config

    `name` is the importable package (what pip installs as "aa-pilotstats"
    and what goes in INSTALLED_APPS). `label` is the short identifier used in
    permission strings (`pilotstats.basic_access`), the URL namespace
    (`{% url 'pilotstats:...' %}`), table names, and migrations.

    This app was called PvE Tracker (package `aa_pvetracker`, label
    `pvetracker`) up to v0.0.11. Migration 0001 adopts an old install's
    tables, permissions, and migration history; see that migration.
    """

    name = "aa_pilotstats"
    label = "pilotstats"
    verbose_name = f"Pilot Stats v{__version__}"

    def ready(self):
        # Register system checks.
        from . import checks  # noqa: F401
        from . import signals

        signals.connect()
