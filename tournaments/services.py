"""
Business services that keep derived database state consistent.
"""

from django.db import transaction

from .models import (
    MatchTeams,
    Matches,
    Results,
    TournamentStandings,
)


def recompute_standings(tournament_id):
    """
    Rebuild every standings row of one tournament from its recorded
    results (matching the existing client-side rules: win 3, draw 1).

    Aggregates only matches whose Home/Away assignment is complete,
    because that is what maps result scores to teams. Standings rows for
    teams without results are reset to zero, never deleted — manual
    rows stay assignable.

    Returns `None` when the tournament doesn't exist or has nothing to
    recompute.
    """
    from .models import Teams, Tournaments

    exists = Tournaments.objects.filter(
        tournament_id=tournament_id,
    ).exists()

    if not exists:
        return None

    rows = list(
        TournamentStandings.objects.filter(tournament=tournament_id)
    )
    progress = {}

    # Rebuild means rebuild: every recomputation starts each row at zero,
    # otherwise repeated calls would accumulate totals on top of stale
    # values (a delete would grow `played` instead of shrinking it).
    for row in rows:
        row.played = 0
        row.won = 0
        row.draw = 0
        row.lost = 0
        row.goals_for = 0
        row.goals_against = 0
        progress[row.team_id] = row

    # Also seed rows for registered teams, so a table always contains
    # every participating team as soon as it has a result… but only teams
    # that already have a row OR a result; pure-registration blanks would
    # otherwise multiply rows.
    matches = list(
        Matches.objects.filter(tournament=tournament_id).only(
            "match_id",
            "tournament",
        )
    )

    match_sides = {}
    for match in matches:
        rows_match = list(MatchTeams.objects.filter(match=match))
        home = [value.team_id for value in rows_match if value.team_side == "Home"]
        away = [value.team_id for value in rows_match if value.team_side == "Away"]

        if len(rows_match) == 2 and len(home) == 1 and len(away) == 1:
            match_sides[match.match_id] = (home[0], away[0])

    for match_id, team1_score, team2_score, winner in Results.objects.filter(
        match__match_id__in=match_sides.keys(),
        match__tournament=tournament_id,
    ).values_list(
        "match",
        "team1_score",
        "team2_score",
        "winner_team",
    ).iterator():
        sides = match_sides.get(match_id)

        if sides is None:
            continue

        home_id, away_id = sides
        home_score = team1_score if team1_score is not None else 0
        away_score = team2_score if team2_score is not None else 0

        home = progress.setdefault(
            home_id,
            TournamentStandings(
                tournament_id=tournament_id,
                team_id=home_id,
                played=0,
                won=0,
                draw=0,
                lost=0,
                goals_for=0,
                goals_against=0,
            ),
        )
        away = progress.setdefault(
            away_id,
            TournamentStandings(
                tournament_id=tournament_id,
                team_id=away_id,
                played=0,
                won=0,
                draw=0,
                lost=0,
                goals_for=0,
                goals_against=0,
            ),
        )

        home.played += 1
        away.played += 1

        home.goals_for += home_score
        home.goals_against += away_score
        away.goals_for += away_score
        away.goals_against += home_score

        if winner is None:
            home.draw += 1
            away.draw += 1
        elif winner == home_id:
            home.won += 1
            away.lost += 1
        else:
            away.won += 1
            home.lost += 1

    with transaction.atomic():
        for team_id, stats in progress.items():
            stats.played = stats.won + stats.draw + stats.lost
            stats.goal_difference = stats.goals_for - stats.goals_against
            stats.points = stats.won * 3 + stats.draw

            TournamentStandings.objects.update_or_create(
                tournament_id=tournament_id,
                team_id=team_id,
                defaults={
                    "played": stats.played,
                    "won": stats.won,
                    "draw": stats.draw,
                    "lost": stats.lost,
                    "goals_for": stats.goals_for,
                    "goals_against": stats.goals_against,
                    "goal_difference": stats.goal_difference,
                    "points": stats.points,
                },
            )

    return progress
