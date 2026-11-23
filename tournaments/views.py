from django.contrib.auth.hashers import check_password
from django.shortcuts import get_object_or_404
from django.db.models.deletion import ProtectedError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie

from rest_framework import status
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import (
    MatchReferees,
    MatchTeams,
    Matches,
    Players,
    Referees,
    Results,
    Teams,
    TournamentStandings,
    TournamentTeams,
    Tournaments,
    Users,
    Venues,
)

from .serializers import (
    UsersSerializer,
    TournamentsSerializer,
    TeamsSerializer,
    PlayersSerializer,
    VenuesSerializer,
    RefereesSerializer,
    MatchesSerializer,
    ResultsSerializer,
    TournamentStandingsSerializer,
    TournamentTeamsSerializer,
    MatchTeamsSerializer,
    MatchRefereesSerializer,
    SignupSerializer,
)

from .permissions import (
    IsAdmin,
    PublicReadAuthenticatedWrite,
    ROLE_ADMIN,
    ROLE_ORGANIZER,
    ROLE_REFEREE,
    ROLE_TEAM_MANAGER,
    WRITER_ROLES,
    ensure_logged_in,
    ensure_role,
    is_organizer_of,
)
from .services import recompute_standings
from rest_framework.exceptions import PermissionDenied


# =====================================
# BASE LIST AND CREATE API
# =====================================

class ScopedWriteModelAPIView(APIView):
    """
    Shared write-authorisation logic for the list/detail bases.

    - write_roles: roles allowed to write this resource at all.
    - tournament_field: how to resolve the owning tournament
      ("tournament" -> the row IS a tournament; "match" -> resolve via
      the row's match; otherwise the FK field name). None = global
      resource with no tournament ownership.
    - check_write() raises PermissionDenied (403) for insufficient
      role/ownership; 401 comes from the authentication layer.
    """

    serializer_class = None
    write_roles = WRITER_ROLES
    tournament_field = None

    def user_role(self, request):
        _, role = ensure_logged_in(request)
        return role

    def resolve_tournament_id(self, obj, data):
        """
        Return the tournament this write targets, or None for global
        resources. Works for both object rows and payload data.
        """
        if self.tournament_field is None:
            return None

        if obj is not None:
            if self.tournament_field == "tournament":
                return obj.tournament_id

            if self.tournament_field == "match":
                match = getattr(obj, "match", None)

                return match.tournament_id if match is not None else None

            value = getattr(obj, self.tournament_field, None)
            return getattr(value, "team_id", value)

        if not isinstance(data, dict):
            return None

        if self.tournament_field == "match":
            value = data.get("match")

            if value is None:
                return None

            if isinstance(value, Matches):
                return value.tournament_id

            try:
                return Matches.objects.only(
                    "tournament_id"
                ).get(match_id=value).tournament_id
            except Matches.DoesNotExist:
                return None

        value = data.get("tournament")

        if value is None:
            return None

        return getattr(value, "tournament_id", value)

    def check_write(self, request, obj=None, data=None):
        """
        Role + object-level authorisation for create/update/delete.
        Wrapper method used by both base classes.
        """
        return self._check_write(request, obj, data)

    def _check_write(self, request, obj=None, data=None):
        user, role = ensure_role(request, self.write_roles)

        if role == ROLE_ADMIN:
            return

        if role == ROLE_ORGANIZER:
            if self.tournament_field is None:
                # Global resources: organizer may manage (teams, players,
                # venues, referees). The schema has no per-team ownership
                # for these resources.
                return

            # Creating a tournament always creates "own" tournaments.
            tournament_id = self.resolve_tournament_id(obj, data)

            if tournament_id is None:
                return

            if not is_organizer_of(user, tournament_id):
                raise PermissionDenied(
                    "You can only manage tournaments you organize."
                )

            return

        raise PermissionDenied("You do not have permission for this action.")

    def save_kwargs(self, request, data=None):
        """Server-side field overrides applied during save()."""
        return {}

    def after_save(self, request, data):
        """Optional post-save hook (inside the same transaction)."""
        return None

    def after_delete(self, request, obj):
        """Optional post-delete hook (inside the same transaction)."""
        return None


# =====================================
# BASE LIST AND CREATE API
# =====================================

class BaseListCreateAPIView(ScopedWriteModelAPIView):
    permission_classes = [PublicReadAuthenticatedWrite]

    def get(self, request):
        model = self.serializer_class.Meta.model

        objects = model.objects.order_by(
            model._meta.pk.name
        )

        serializer = self.serializer_class(
            objects,
            many=True
        )

        return Response(serializer.data)

    def post(self, request):
        serializer = self.serializer_class(
            data=request.data
        )

        serializer.is_valid(raise_exception=True)

        try:
            self.check_write(request, data=serializer.validated_data)
        except PermissionDenied:
            raise

        model = self.serializer_class.Meta.model

        field_names = {
            field.name
            for field in model._meta.fields
        }

        save_values = {}

        for field_name in ("created_at", "registered_at"):
            if field_name in field_names:
                save_values[field_name] = timezone.now()

        save_values.update(self.save_kwargs(request, serializer.validated_data))

        try:
            with transaction.atomic():
                serializer.save(**save_values)
                self.after_save(request, serializer.data)

        except IntegrityError:
            return Response(
                {
                    "error": (
                        "Data conflicts with a database constraint."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
        )


# =====================================
# BASE DETAIL API
# =====================================

class BaseDetailAPIView(ScopedWriteModelAPIView):
    permission_classes = [PublicReadAuthenticatedWrite]

    def get_object(self, pk):
        model = self.serializer_class.Meta.model

        return get_object_or_404(
            model,
            pk=pk
        )

    def get(self, request, pk):
        obj = self.get_object(pk)

        serializer = self.serializer_class(obj)

        return Response(serializer.data)

    def update_object(self, request, pk, partial):
        obj = self.get_object(pk)

        self.check_write(request, obj=obj)

        serializer = self.serializer_class(
            obj,
            data=request.data,
            partial=partial,
        )

        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                serializer.save(**self.save_kwargs(request, serializer.validated_data))
                self.after_save(request, serializer.data)

        except IntegrityError:
            return Response(
                {
                    "error": (
                        "Data conflicts with a database constraint."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(serializer.data)

    def put(self, request, pk):
        return self.update_object(
            request,
            pk,
            partial=False
        )

    def patch(self, request, pk):
        return self.update_object(
            request,
            pk,
            partial=True
        )

    def delete(self, request, pk):
        obj = self.get_object(pk)

        self.check_write(request, obj=obj)

        try:
            with transaction.atomic():
                obj.delete()
                # Recompute/resync hooks must observe the post-delete
                # state (e.g. standings after a result row is removed).
                self.after_delete(request, obj)

        except (ProtectedError, IntegrityError):
            return Response(
                {
                    "error": (
                        "Cannot delete this record because "
                        "other records depend on it."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            status=status.HTTP_204_NO_CONTENT
        )


# =====================================
# USER APIs - ADMIN ONLY
# =====================================

class UsersListCreateAPIView(BaseListCreateAPIView):
    serializer_class = UsersSerializer
    permission_classes = [IsAdmin]


class UsersDetailAPIView(BaseDetailAPIView):
    serializer_class = UsersSerializer
    permission_classes = [IsAdmin]


# =====================================
# AUTH APIs
# =====================================

@method_decorator(ensure_csrf_cookie, name="dispatch")
class AuthCsrfAPIView(APIView):
    """Sets the csrftoken cookie used by the SPA for unsafe requests."""

    def get(self, request):
        return Response({"detail": "CSRF cookie set."})


class SignupAPIView(APIView):
    # Scoped brute-force protection (rates in settings).
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "signup"

    def post(self, request):
        serializer = SignupSerializer(
            data=request.data
        )

        serializer.is_valid(raise_exception=True)

        user = serializer.save()

        return Response(
            {
                "message": "Account created successfully.",
                "user": {
                    "user_id": user.user_id,
                    "name": user.name,
                    "email": user.email,
                    "role": user.role,
                }
            },
            status=status.HTTP_201_CREATED
        )


class AuthLoginAPIView(APIView):
    # Scoped brute-force protection (rates in settings).
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        email = (
            request.data.get("email") or ""
        ).strip().lower()

        password = request.data.get("password") or ""

        user = Users.objects.filter(
            email__iexact=email
        ).first()

        if user is None or not check_password(
            password,
            user.password
        ):
            return Response(
                {
                    "error": "Invalid email or password."
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Rotate the session key on credential change (session-fixation
        # defense); keep the CSRF cookie working afterwards.
        request.session.cycle_key()
        request.session["user_id"] = user.user_id

        return Response(
            {
                "user_id": user.user_id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "created_at": user.created_at,
            }
        )



class AuthMeAPIView(APIView):

    def get_authenticated_user(self, request):
        """
        Always identify the user from the Django session.
        Never trust a user ID supplied by the frontend.
        """
        user_id = request.session.get("user_id")

        if not user_id:
            return None

        user = Users.objects.filter(
            user_id=user_id
        ).first()

        if user is None:
            request.session.flush()
            return None

        return user

    def get_profile_data(self, user):
        """
        Return only safe profile information.
        Never expose passwords or password hashes.
        """
        return {
            "user_id": user.user_id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "team": user.team_id,
            "player": user.player_id,
            "referee": user.referee_id,
            "created_at": user.created_at,
        }

    def get(self, request):

        user = self.get_authenticated_user(request)

        if user is None:
            return Response(
                {"error": "Not authenticated."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(
            self.get_profile_data(user),
            status=status.HTTP_200_OK,
        )

    def patch(self, request):

        user = self.get_authenticated_user(request)

        if user is None:
            return Response(
                {"error": "Not authenticated."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Only Full Name can be updated.
        # Email, Role, User ID and Permissions are protected.

        name = request.data.get("name")

        if not isinstance(name, str):
            return Response(
                {"name": ["A valid name is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        name = name.strip()

        if not name:
            return Response(
                {"name": ["Full name cannot be empty."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(name) > 100:
            return Response(
                {"name": ["Name cannot exceed 100 characters."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update only the currently authenticated user's record.
        Users.objects.filter(
            user_id=user.user_id
        ).update(name=name)

        user.refresh_from_db()

        return Response(
            {
                "message": "Profile updated successfully.",
                **self.get_profile_data(user),
            },
            status=status.HTTP_200_OK,
        )


    def get_authenticated_user(self, request):
        """
        Always identify the user from the Django session.
        Never trust a user ID supplied by the frontend.
        """
        user_id = request.session.get("user_id")

        if not user_id:
            return None

        user = Users.objects.filter(
            user_id=user_id
        ).first()

        if user is None:
            request.session.flush()
            return None

        return user

    def get_profile_data(self, user):
        """
        Return only safe profile information.
        Never expose passwords or password hashes.
        """
        return {
            "user_id": user.user_id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "team": user.team_id,
            "player": user.player_id,
            "referee": user.referee_id,
            "created_at": user.created_at,
        }

    def get(self, request):

        user = self.get_authenticated_user(request)

        if user is None:
            return Response(
                {"error": "Not authenticated."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(
            self.get_profile_data(user),
            status=status.HTTP_200_OK,
        )

    def patch(self, request):

        user = self.get_authenticated_user(request)

        if user is None:
            return Response(
                {"error": "Not authenticated."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Only Full Name can be updated.
        # Email, Role, User ID and Permissions are protected.

        name = request.data.get("name")

        if not isinstance(name, str):
            return Response(
                {"name": ["A valid name is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        name = name.strip()

        if not name:
            return Response(
                {"name": ["Full name cannot be empty."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(name) > 100:
            return Response(
                {"name": ["Name cannot exceed 100 characters."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update only the currently authenticated user's record.
        Users.objects.filter(
            user_id=user.user_id
        ).update(name=name)

        user.refresh_from_db()

        return Response(
            {
                "message": "Profile updated successfully.",
                **self.get_profile_data(user),
            },
            status=status.HTTP_200_OK,
        )



class AuthLogoutAPIView(APIView):

    def post(self, request):
        request.session.flush()

        return Response(
            {
                "message": "Logged out successfully."
            },
            status=status.HTTP_200_OK
        )


# =====================================
# TOURNAMENT APIs
# =====================================

class TournamentsListCreateAPIView(BaseListCreateAPIView):
    serializer_class = TournamentsSerializer
    tournament_field = "tournament"

    def resolve_tournament_id(self, obj, data):
        # The tournament row itself.
        if obj is not None:
            return obj.tournament_id

        return None

    def check_write(self, request, obj=None, data=None):
        user, role = ensure_role(request, self.write_roles)

        if role == ROLE_ADMIN:
            return

        if role == ROLE_ORGANIZER:
            # organized_by_id is the FK column int; organized_by is the
            # related Users object and must never be compared to an int.
            if obj is not None and obj.organized_by_id != user.user_id:
                raise PermissionDenied(
                    "You can only manage tournaments you organize."
                )

            return

        raise PermissionDenied("You do not have permission for this action.")

    def save_kwargs(self, request, data=None):
        user, role = ensure_logged_in(request)

        if role == ROLE_ORGANIZER:
            # organized_by is always the logged-in organizer.
            return {"organized_by": user}

        if data is not None and data.get("organized_by") is None:
            # Admin did not pick a specific organizer -> self.
            return {"organized_by": user}

        return {}


class TournamentsDetailAPIView(TournamentsListCreateAPIView, BaseDetailAPIView):
    def save_kwargs(self, request, data=None):
        user, role = ensure_logged_in(request)

        if role == ROLE_ORGANIZER:
            # organized_by is always the logged-in organizer.
            return {"organized_by": user}

        # Admins keep the tournament's organizer; ownership is what
        # check_write already verified.
        return {}


# =====================================
# TEAM APIs
# =====================================

class TeamsListCreateAPIView(BaseListCreateAPIView):
    serializer_class = TeamsSerializer

    def check_write(self, request, obj=None, data=None):
        user, role = ensure_logged_in(request)

        if role == ROLE_ADMIN or role == ROLE_ORGANIZER:
            return

        if role == ROLE_TEAM_MANAGER:
            # Team managers may update their own team only; they cannot
            # create or delete teams.
            if request.method not in ("PUT", "PATCH"):
                raise PermissionDenied(
                    "Team managers may only update their own team."
                )

            team_id = obj.team_id if obj is not None else None

            if team_id is None or team_id != user.team_id:
                raise PermissionDenied(
                    "You can only manage your own team."
                )

            return

        raise PermissionDenied("You do not have permission for this action.")


class TeamsDetailAPIView(TeamsListCreateAPIView, BaseDetailAPIView):
    pass


# =====================================
# PLAYER APIs
# =====================================

class PlayersListCreateAPIView(BaseListCreateAPIView):
    serializer_class = PlayersSerializer

    def check_write(self, request, obj=None, data=None):
        user, role = ensure_logged_in(request)

        if role == ROLE_ADMIN or role == ROLE_ORGANIZER:
            return

        if role == ROLE_TEAM_MANAGER:
            # Team managers may only manage players of their own team.
            player_team = None

            if obj is not None:
                player_team = obj.team_id
            elif isinstance(data, dict):
                player_team = getattr(data.get("team"), "team_id", None)

            if player_team is None or player_team != user.team_id:
                raise PermissionDenied(
                    "You can only manage players of your own team."
                )

            return

        raise PermissionDenied("You do not have permission for this action.")


class PlayersDetailAPIView(PlayersListCreateAPIView, BaseDetailAPIView):
    pass


# =====================================
# VENUE APIs
# =====================================

class VenuesListCreateAPIView(BaseListCreateAPIView):
    serializer_class = VenuesSerializer


class VenuesDetailAPIView(VenuesListCreateAPIView, BaseDetailAPIView):
    pass


# =====================================
# REFEREE APIs
# =====================================

class RefereesListCreateAPIView(BaseListCreateAPIView):
    serializer_class = RefereesSerializer


class RefereesDetailAPIView(RefereesListCreateAPIView, BaseDetailAPIView):
    pass


# =====================================
# MATCH APIs
# =====================================

class MatchesListCreateAPIView(BaseListCreateAPIView):
    serializer_class = MatchesSerializer
    tournament_field = "tournament"


class MatchesDetailAPIView(MatchesListCreateAPIView, BaseDetailAPIView):
    pass


# =====================================
# RESULT APIs
# =====================================

class ResultsListCreateAPIView(BaseListCreateAPIView):
    serializer_class = ResultsSerializer
    tournament_field = "match"

    def check_write(self, request, obj=None, data=None):
        user, role = ensure_logged_in(request)

        if role == ROLE_ADMIN:
            return

        if role == ROLE_REFEREE:
            # Referees may submit/edit results only for matches they
            # are assigned to.
            match_id = (
                obj.match_id
                if obj is not None
                else (
                    data.get("match", data.get("match_id"))
                    if isinstance(data, dict)
                    else None
                )
            )

            if isinstance(match_id, Matches):
                match_id = match_id.match_id

            assigned = (
                match_id is not None
                and user.referee_id is not None
                and MatchReferees.objects.filter(
                    match__match_id=match_id,
                    referee_id=user.referee_id,
                ).exists()
            )

            if request.method in ("PUT", "PATCH", "POST") and assigned:
                return

            raise PermissionDenied(
                "You can only submit results for matches you are assigned to."
            )

        if role == ROLE_ORGANIZER:
            tournament_id = self.resolve_tournament_id(obj, data)

            if tournament_id is None:
                raise PermissionDenied(
                    "You can only manage results of your tournaments."
                )

            if not is_organizer_of(user, tournament_id):
                raise PermissionDenied(
                    "You can only manage results of your tournaments."
                )

            return

        raise PermissionDenied("You do not have permission for this action.")

    def after_save(self, request, data):
        # Consistent standings after every result change.
        match_id = data.get("match") if isinstance(data, dict) else None

        if match_id is not None:
            tournament_id = Matches.objects.filter(
                match_id=match_id,
            ).values_list("tournament_id", flat=True).first()

            if tournament_id is not None:
                recompute_standings(tournament_id)

    def after_delete(self, request, obj):
        recompute_standings(obj.match.tournament_id)


class ResultsDetailAPIView(ResultsListCreateAPIView, BaseDetailAPIView):
    pass


# =====================================
# TOURNAMENT STANDINGS APIs
# =====================================

class TournamentStandingsListCreateAPIView(BaseListCreateAPIView):
    serializer_class = TournamentStandingsSerializer
    tournament_field = "tournament"


class TournamentStandingsDetailAPIView(
    TournamentStandingsListCreateAPIView,
    BaseDetailAPIView,
):
    pass


# =====================================
# TOURNAMENT TEAMS APIs
# =====================================

class TournamentTeamsListCreateAPIView(BaseListCreateAPIView):
    serializer_class = TournamentTeamsSerializer
    tournament_field = "tournament"


class TournamentTeamsDetailAPIView(
    TournamentTeamsListCreateAPIView,
    BaseDetailAPIView,
):
    pass


# =====================================
# MATCH TEAMS APIs
# =====================================

class MatchTeamsListCreateAPIView(BaseListCreateAPIView):
    serializer_class = MatchTeamsSerializer
    tournament_field = "match"


class MatchTeamsDetailAPIView(MatchTeamsListCreateAPIView, BaseDetailAPIView):
    pass


# =====================================
# MATCH REFEREES APIs
# =====================================

class MatchRefereesListCreateAPIView(BaseListCreateAPIView):
    serializer_class = MatchRefereesSerializer
    tournament_field = "match"


class MatchRefereesDetailAPIView(
    MatchRefereesListCreateAPIView,
    BaseDetailAPIView,
):
    pass
