import types

from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import serializers

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
from .permissions import (
    ALL_ROLES,
    LEGACY_ROLE_MAP,
    ROLE_VIEWER,
    get_role,
)


def validate_password_strength(password, name, email):
    """
    Apply the password validators configured in settings
    (AUTH_PASSWORD_VALIDATORS) to a custom Users account.

    UserAttributeSimilarityValidator inspects username/email/first_name on
    the passed object, so an anonymous namespace acts as the stand-in user
    (this project has no django.contrib.auth User model).
    """
    stand_in = types.SimpleNamespace(
        username=email or "",
        email=email or "",
        first_name=name or "",
    )

    validate_password(password, user=stand_in)


# ==========================================================
# Users (admin-managed accounts)
# ==========================================================

class UsersSerializer(serializers.ModelSerializer):
    # Model field is a plain CharField; force real e-mail validation.
    email = serializers.EmailField(max_length=150)

    class Meta:
        model = Users
        fields = "__all__"
        read_only_fields = ["created_at"]
        extra_kwargs = {
            "password": {
                "write_only": True,
                "required": False,
                "trim_whitespace": False,
            },
            "role": {"required": False},
        }

    def validate_password(self, value):
        if value is not None and value.strip() == "":
            raise serializers.ValidationError(
                "Password cannot be blank."
            )
        return value

    def validate_role(self, value):
        # Normalize legacy spellings ('Team Manager', 'Organizer',
        # 'user') to the canonical role string before storing.
        role = (value or ROLE_VIEWER).strip().lower().replace(" ", "_")
        role = LEGACY_ROLE_MAP.get(role, role)

        if role not in ALL_ROLES:
            raise serializers.ValidationError(
                "Role must be one of: %s." % ", ".join(ALL_ROLES)
            )

        return role

    def validate_email(self, value):
        # Surface DB unique-violations as a proper field error instead of
        # the generic 400 "database constraint" message.
        email = value.strip().lower()

        query = Users.objects.filter(email__iexact=email)

        if self.instance is not None:
            query = query.exclude(user_id=self.instance.user_id)

        if query.exists():
            raise serializers.ValidationError(
                "This email is already registered."
            )

        return email

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            # Account creation always needs an initial password.
            raise serializers.ValidationError(
                {"password": "Password is required."}
            )

        password = attrs.get("password")

        if password is not None:
            # Settings-configured policy (length, common passwords,
            # similarity with the account's own name/e-mail).
            validate_password_strength(
                password,
                attrs.get("name") or getattr(self.instance, "name", ""),
                attrs.get("email") or getattr(self.instance, "email", ""),
            )

        # A team/player/referee identity may only be linked to one user.
        for field in ("team", "player", "referee"):
            linked = attrs.get(field)

            if linked is None:
                continue

            duplicate = Users.objects.filter(**{field: linked})

            if self.instance is not None:
                duplicate = duplicate.exclude(pk=self.instance.pk)

            if duplicate.exists():
                raise serializers.ValidationError(
                    {
                        field: "This %s is already linked to another user." % field
                    }
                )

        return attrs

    def create(self, validated_data):
        validated_data["password"] = make_password(validated_data["password"])
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "password" in validated_data:
            validated_data["password"] = make_password(
                validated_data["password"]
            )
        return super().update(instance, validated_data)


# ==========================================================
# Signup (public)
# ==========================================================

class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
    )
    email = serializers.EmailField(max_length=150)

    class Meta:
        model = Users
        fields = ["name", "email", "password"]

    def validate_email(self, value):
        email = value.strip().lower()

        if Users.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "This email is already registered."
            )

        return email

    def validate(self, attrs):
        validate_password_strength(
            attrs["password"],
            attrs.get("name", ""),
            attrs.get("email", ""),
        )

        return attrs

    def create(self, validated_data):
        validated_data["password"] = make_password(
            validated_data["password"]
        )

        # Public signups always start as viewers; role changes are an
        # admin-only operation (UsersSerializer writes). created_at is
        # stamped server-side like every other resource.
        validated_data["role"] = ROLE_VIEWER
        validated_data.setdefault("created_at", timezone.now())

        return Users.objects.create(**validated_data)


# ==========================================================
# Master data
# ==========================================================

class TournamentsSerializer(serializers.ModelSerializer):
    # Resolved display name so the SPA never needs the (admin-only)
    # users list to render the organizer column.
    organizer_name = serializers.CharField(
        source="organized_by.name",
        read_only=True,
        default=None,
    )

    # The column is NOT NULL, but the *value* is always chosen
    # server-side by the view (organizer -> self; admin -> picked row or
    # self). It must not be required at validation, otherwise the
    # organizer flow (which never sends it) is rejected before
    # save_kwargs() can inject it.
    organized_by = serializers.PrimaryKeyRelatedField(
        queryset=Users.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Tournaments
        fields = "__all__"
        read_only_fields = ["created_at"]

    def validate_name(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError(
                "Tournament name cannot be empty."
            )
        return value.strip()

    def validate_dates(self, start, end, message):
        if (
            start is not None
            and end is not None
            and end < start
        ):
            raise serializers.ValidationError(message)

    def validate_organized_by(self, value):
        if value is not None and get_role(value) not in ("admin", "organizer"):
            raise serializers.ValidationError(
                "The organizer must be an admin or organizer account."
            )

        return value

    def validate(self, attrs):
        if self.instance is not None:
            start = attrs.get("start_date", self.instance.start_date)
            end = attrs.get("end_date", self.instance.end_date)
        else:
            start = attrs.get("start_date")
            end = attrs.get("end_date")

        self.validate_dates(
            start,
            end,
            "End date cannot be before the start date.",
        )

        return attrs


class TeamsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Teams
        fields = "__all__"
        read_only_fields = ["created_at"]

    def validate_team_name(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError("Team name cannot be empty.")
        return value.strip()


class PlayersSerializer(serializers.ModelSerializer):
    class Meta:
        model = Players
        fields = "__all__"
        read_only_fields = ["created_at"]
        extra_kwargs = {
            "jersey_no": {"min_value": 0},
        }

    def validate_name(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError("Player name cannot be empty.")
        return value.strip()

    def validate(self, attrs):
        team = attrs.get("team") or (
            self.instance.team if self.instance is not None else None
        )
        name = attrs.get("name") or (
            self.instance.name if self.instance is not None else None
        )
        date_of_birth = attrs.get("date_of_birth") or (
            self.instance.date_of_birth
            if self.instance is not None
            else None
        )

        if team is not None and name:
            duplicate = Players.objects.filter(
                team=team,
                name=name,
            )

            if date_of_birth is not None:
                duplicate = duplicate.filter(date_of_birth=date_of_birth)

            if self.instance is not None:
                duplicate = duplicate.exclude(pk=self.instance.pk)

            if duplicate.exists():
                raise serializers.ValidationError(
                    "This player is already registered in this team."
                )

        # A birth date in the future is factually impossible.
        dob = attrs.get("date_of_birth")

        if dob is not None and dob > timezone.now().date():
            raise serializers.ValidationError(
                "Date of birth cannot be in the future."
            )

        # Jersey numbers must be unique inside the roster.
        jersey = attrs["jersey_no"] if "jersey_no" in attrs else (
            self.instance.jersey_no if self.instance is not None else None
        )

        if jersey is not None and team is not None:
            clash = Players.objects.filter(team=team, jersey_no=jersey)

            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)

            if clash.exists():
                raise serializers.ValidationError(
                    "Jersey number %s is already used in this team." % jersey
                )

        return attrs


class VenuesSerializer(serializers.ModelSerializer):
    class Meta:
        model = Venues
        fields = "__all__"
        read_only_fields = ["created_at"]
        extra_kwargs = {
            "capacity": {"min_value": 0},
        }

    def validate_venue_name(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError("Venue name cannot be empty.")
        return value.strip()


class RefereesSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        max_length=150,
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    class Meta:
        model = Referees
        fields = "__all__"
        read_only_fields = ["created_at"]

    def validate_name(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError("Referee name cannot be empty.")
        return value.strip()


# ==========================================================
# Matches and their join records
# ==========================================================

class MatchesSerializer(serializers.ModelSerializer):
    class Meta:
        model = Matches
        fields = "__all__"
        read_only_fields = ["created_at"]

    def validate(self, attrs):
        match = self.instance

        if match is not None:
            tournament = attrs.get("tournament", match.tournament)
            match_date = attrs.get("match_date", match.match_date)
        else:
            tournament = attrs.get("tournament")
            match_date = attrs.get("match_date")

        # Match must be scheduled inside the tournament's date window
        # when the tournament dates are known.
        if (
            tournament is not None
            and match_date is not None
            and (tournament.start_date or tournament.end_date)
        ):
            if (
                tournament.start_date is not None
                and match_date < tournament.start_date
            ) or (
                tournament.end_date is not None
                and match_date > tournament.end_date
            ):
                raise serializers.ValidationError(
                    "Match date must fall within the tournament's "
                    "start and end dates."
                )

        return attrs


class TournamentTeamsSerializer(serializers.ModelSerializer):
    class Meta:
        model = TournamentTeams
        fields = "__all__"
        read_only_fields = ["registered_at"]

    def validate(self, attrs):
        tournament = attrs.get("tournament") or (
            self.instance.tournament if self.instance is not None else None
        )
        team = attrs.get("team") or (
            self.instance.team if self.instance is not None else None
        )

        if (
            tournament is not None
            and team is not None
            and TournamentTeams.objects.filter(tournament=tournament, team=team)
        ):
            if self.instance is not None:
                # Updating the same registration row is allowed.
                # Updating a different registration row for the same pair is not.
                if not TournamentTeams.objects.filter(
                    tournament=tournament,
                    team=team,
                    pk=self.instance.pk,
                ).exists():
                    raise serializers.ValidationError(
                        "This team is already registered in this tournament."
                    )
            else:
                raise serializers.ValidationError(
                    "This team is already registered in this tournament."
                )

        return attrs


class MatchTeamsSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchTeams
        fields = "__all__"

    def validate_team_side(self, value):
        value = (value or "").strip()

        if value not in ("Home", "Away"):
            raise serializers.ValidationError(
                'Team side must be "Home" or "Away".'
            )

        return value

    def validate(self, attrs):
        match = attrs.get("match") or (
            self.instance.match if self.instance is not None else None
        )
        team = attrs.get("team") or (
            self.instance.team if self.instance is not None else None
        )
        side = attrs.get("team_side") or (
            self.instance.team_side if self.instance is not None else None
        )

        if match is None:
            return attrs

        other_rows = MatchTeams.objects.filter(match=match).exclude(
            pk=self.instance.pk if self.instance is not None else None
        )

        if team is not None:
            # Team must be registered in the match's tournament.
            if not TournamentTeams.objects.filter(
                tournament=match.tournament,
                team=team,
            ).exists():
                raise serializers.ValidationError(
                    "This team is not registered in the match's tournament."
                )

            if other_rows.filter(team=team).exists():
                raise serializers.ValidationError(
                    "This team is already assigned to this match."
                )

        if side is not None and other_rows.filter(team_side=side).exists():
            raise serializers.ValidationError(
                'Side "%s" is already used by another team in this match.' % side
            )

        # A match has exactly two team slots.
        if (
            self.instance is None
            and other_rows.count() >= 2
        ):
            raise serializers.ValidationError(
                "This match already has two teams assigned."
            )

        return attrs


class MatchRefereesSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchReferees
        fields = "__all__"

    def validate(self, attrs):
        match = attrs.get("match") or (
            self.instance.match if self.instance is not None else None
        )
        referee = attrs.get("referee") or (
            self.instance.referee if self.instance is not None else None
        )

        if (
            match is not None
            and referee is not None
            and MatchReferees.objects.filter(
                match=match,
                referee=referee,
            )
            .exclude(pk=self.instance.pk if self.instance is not None else None)
            .exists()
        ):
            raise serializers.ValidationError(
                "This referee is already assigned to this match."
            )

        return attrs


# ==========================================================
# Results
# ==========================================================

class ResultsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Results
        fields = "__all__"
        read_only_fields = ["winner_team"]
        extra_kwargs = {
            "team1_score": {"min_value": 0, "required": True},
            "team2_score": {"min_value": 0, "required": True},
        }

    def get_match_sides(self, match):
        """Return (home_team, away_team) when the match assignment is complete."""
        rows = list(MatchTeams.objects.filter(match=match))

        home = [row.team for row in rows if row.team_side == "Home"]
        away = [row.team for row in rows if row.team_side == "Away"]

        if len(rows) != 2 or len(home) != 1 or len(away) != 1 or home[0] == away[0]:
            return None

        return home[0], away[0]

    def validate(self, attrs):
        match = attrs.get("match") or (
            self.instance.match if self.instance is not None else None
        )

        if match is None:
            raise serializers.ValidationError({"match": "Match is required."})

        # One result per match.
        duplicate = Results.objects.filter(match=match)

        if self.instance is not None:
            duplicate = duplicate.exclude(pk=self.instance.pk)

        if duplicate.exists():
            raise serializers.ValidationError(
                "This match already has a result; edit the existing one."
            )

        sides = self.get_match_sides(match)

        if sides is None:
            raise serializers.ValidationError(
                "The match must have exactly one Home and one Away team "
                "assigned before a result can be recorded."
            )

        home_team, away_team = sides

        home_score = attrs.get(
            "team1_score",
            self.instance.team1_score if self.instance is not None else 0,
        )
        away_score = attrs.get(
            "team2_score",
            self.instance.team2_score if self.instance is not None else 0,
        )

        # winner_team is always derived server-side so it cannot conflict
        # with the recorded scores.
        if home_score == away_score:
            attrs["winner_team"] = None
        else:
            attrs["winner_team"] = home_team if home_score > away_score else away_team

        return attrs


# ==========================================================
# Standings
# ==========================================================

STANDING_INPUT_FIELDS = ("won", "draw", "lost", "goals_for", "goals_against")


class TournamentStandingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = TournamentStandings
        fields = "__all__"
        # Derived columns are always recomputed server-side.
        read_only_fields = [
            "played",
            "goal_difference",
            "points",
        ]
        extra_kwargs = {
            field: {"min_value": 0}
            for field in STANDING_INPUT_FIELDS
        }

    def validate(self, attrs):
        tournament = attrs.get("tournament") or (
            self.instance.tournament if self.instance is not None else None
        )
        team = attrs.get("team") or (
            self.instance.team if self.instance is not None else None
        )

        if tournament is None or team is None:
            raise serializers.ValidationError(
                "Tournament and team are required."
            )

        if not TournamentTeams.objects.filter(
            tournament=tournament,
            team=team,
        ).exists():
            raise serializers.ValidationError(
                "This team is not registered in this tournament."
            )

        duplicate = TournamentStandings.objects.filter(
            tournament=tournament,
            team=team,
        )

        if self.instance is not None:
            duplicate = duplicate.exclude(pk=self.instance.pk)

        if duplicate.exists():
            raise serializers.ValidationError(
                "This team already has a standing row in this tournament; "
                "edit the existing one."
            )

        def existing(field):
            if field in attrs:
                return attrs[field]

            return (
                getattr(self.instance, field, 0)
                if self.instance is not None
                else 0
            )

        won = existing("won")
        draw = existing("draw")
        lost = existing("lost")
        goals_for = existing("goals_for")
        goals_against = existing("goals_against")

        # Same rule as services.recompute_standings: played counts
        # every completed result (win + draw + loss).
        attrs["played"] = won + draw + lost
        attrs["goal_difference"] = goals_for - goals_against
        attrs["points"] = won * 3 + draw

        return attrs
