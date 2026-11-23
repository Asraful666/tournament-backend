"""
Role + object-level permission helpers for the custom session auth model.

The auth model is a hand-rolled `Users` table (not django.contrib.auth);
auth state lives in `request.session["user_id"]`. The DRF authentication
class (`tournaments.authentication.SessionUserAuthentication`) loads the
user, enforces CSRF for unsafe methods and sets `request.current_user`.
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

# Canonical role strings as stored in the `users.role` column.
ROLE_ADMIN = "admin"
ROLE_ORGANIZER = "organizer"
ROLE_REFEREE = "referee"
ROLE_TEAM_MANAGER = "team_manager"
ROLE_PLAYER = "player"
ROLE_VIEWER = "viewer"

ALL_ROLES = (
    ROLE_ADMIN,
    ROLE_ORGANIZER,
    ROLE_REFEREE,
    ROLE_TEAM_MANAGER,
    ROLE_PLAYER,
    ROLE_VIEWER,
)

WRITER_ROLES = frozenset([ROLE_ADMIN, ROLE_ORGANIZER])
# Roles that may never write anything, ever (viewer included).
READ_ONLY_ROLES = frozenset(
    [ROLE_REFEREE, ROLE_TEAM_MANAGER, ROLE_PLAYER, ROLE_VIEWER]
)

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def get_current_user(request):
    """Return the session user (or None), cached on request.current_user."""
    if getattr(request, "current_user", None) is not None:
        return request.current_user

    user_id = request.session.get("user_id")

    if not user_id:
        return None

    from .models import Users

    user = Users.objects.filter(user_id=user_id).first()

    if user is None:
        # Stale session (user deleted); drop it so later requests are clean.
        request.session.flush()
        return None

    request.current_user = user
    return user


# Legacy spellings found in existing data (title-case rows written by
# older tooling) -> canonical role strings.
LEGACY_ROLE_MAP = {
    "user": ROLE_VIEWER,
    "tournament_organizer": ROLE_ORGANIZER,
    "teammanager": ROLE_TEAM_MANAGER,
}


def get_role(user):
    """Canonical lowercase role of a user (never None for a saved user)."""
    role = (user.role or "").strip().lower().replace(" ", "_")

    role = LEGACY_ROLE_MAP.get(role, role)

    # Unknown/stale role strings degrade to viewer (read-only) instead of
    # being silently treated as a writer — fail closed.
    if role not in ALL_ROLES:
        role = ROLE_VIEWER

    return role


def ensure_logged_in(request):
    """Raise unless a session user exists; returns (user, role)."""
    user = get_current_user(request)

    if user is None:
        raise PermissionDenied("Authentication required.")

    return user, get_role(user)


def ensure_role(request, roles, message=None):
    """Raise unless the current user has one of `roles`."""
    user, role = ensure_logged_in(request)

    if role not in roles:
        raise PermissionDenied(
            message or "You do not have permission to perform this action."
        )

    return user, role


def is_organizer_of(user, tournament_id):
    from .models import Tournaments

    return (
        user is not None
        and tournament_id is not None
        and Tournaments.objects.filter(
            tournament_id=tournament_id,
            organized_by=user.user_id,
        ).exists()
    )


class IsAdmin(BasePermission):
    """Every method: session user must have the admin role."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS and request.method == "OPTIONS":
            return True

        try:
            user, role = ensure_logged_in(request)
        except PermissionDenied:
            return False

        return role == ROLE_ADMIN


class PublicReadAuthenticatedWrite(BasePermission):
    """
    GET/HEAD/OPTIONS are public (viewer/read-only model).

    Unsafe methods require a logged-in session user. The *role and
    object-level* rules live in `view.write_scope()` (see views.py), which
    runs after validation so it can inspect both the existing object and
    the submitted payload.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True

        try:
            ensure_logged_in(request)
        except PermissionDenied:
            return False

        return True

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)
