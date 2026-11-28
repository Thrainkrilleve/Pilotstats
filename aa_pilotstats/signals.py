"""
Clear the cached leaderboard when the data behind it changes.

Model signals cover ordinary saves and deletes. Bulk writes that skip
signals (``QuerySet.update``, ``bulk_update``, ``F()`` increments) call
``leaderboard.invalidate_cache`` themselves, in ``payouts``.
"""

# Django
from django.db.models.signals import post_delete, post_save


def _clear(sender, **kwargs):
    from .leaderboard import invalidate_cache

    invalidate_cache()


def connect():
    # Alliance Auth
    from allianceauth.authentication.models import CharacterOwnership, UserProfile

    from .models import (
        CombatLogUpload,
        Killmail,
        KillmailParticipant,
        NpcKillDaily,
        PayoutDaily,
        PilotSettings,
    )

    # Sessions, payouts, rat counts and killmails are the numbers; ownership
    # and profiles (the main character) decide whose row they land in;
    # PilotSettings holds the wallet opt-out.
    for model in (
        CombatLogUpload, PayoutDaily, NpcKillDaily, Killmail, KillmailParticipant,
        PilotSettings, CharacterOwnership, UserProfile,
    ):
        name = f"{model._meta.label_lower}"
        post_save.connect(_clear, sender=model, dispatch_uid=f"pilotstats-leaderboard-save-{name}")
        post_delete.connect(_clear, sender=model, dispatch_uid=f"pilotstats-leaderboard-delete-{name}")
