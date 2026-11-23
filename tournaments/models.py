# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models


class MatchReferees(models.Model):
    match = models.ForeignKey('Matches', models.DO_NOTHING)
    referee = models.ForeignKey('Referees', models.DO_NOTHING)
    role = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'match_referees'


class MatchTeams(models.Model):
    match = models.ForeignKey('Matches', models.DO_NOTHING)
    team = models.ForeignKey('Teams', models.DO_NOTHING)
    team_side = models.CharField(max_length=20)

    class Meta:
        managed = False
        db_table = 'match_teams'
        unique_together = (('match', 'team'),)


class Matches(models.Model):
    match_id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey('Tournaments', models.DO_NOTHING)
    venue = models.ForeignKey('Venues', models.DO_NOTHING)
    match_date = models.DateField(blank=True, null=True)
    match_time = models.TimeField(blank=True, null=True)
    round = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'matches'


class Players(models.Model):
    player_id = models.AutoField(primary_key=True)
    team = models.ForeignKey('Teams', models.DO_NOTHING)
    name = models.CharField(max_length=100)
    date_of_birth = models.DateField(blank=True, null=True)
    position = models.CharField(max_length=50, blank=True, null=True)
    jersey_no = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'players'


class Referees(models.Model):
    referee_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True, null=True)
    email = models.CharField(max_length=150, blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'referees'


class Results(models.Model):
    result_id = models.AutoField(primary_key=True)
    match = models.ForeignKey(Matches, models.DO_NOTHING)
    team1_score = models.IntegerField(blank=True, null=True)
    team2_score = models.IntegerField(blank=True, null=True)
    winner_team = models.ForeignKey('Teams', models.DO_NOTHING, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'results'


class Teams(models.Model):
    team_id = models.AutoField(primary_key=True)
    team_name = models.CharField(max_length=100)
    coach_name = models.CharField(max_length=100, blank=True, null=True)
    contact_info = models.CharField(max_length=150, blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'teams'


class TournamentStandings(models.Model):
    standing_id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey('Tournaments', models.DO_NOTHING)
    team = models.ForeignKey(Teams, models.DO_NOTHING)
    played = models.IntegerField(blank=True, null=True)
    won = models.IntegerField(blank=True, null=True)
    draw = models.IntegerField(blank=True, null=True)
    lost = models.IntegerField(blank=True, null=True)
    goals_for = models.IntegerField(blank=True, null=True)
    goals_against = models.IntegerField(blank=True, null=True)
    goal_difference = models.IntegerField(blank=True, null=True)
    points = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tournament_standings'
        unique_together = (('tournament', 'team'),)


class TournamentTeams(models.Model):
    tournament = models.ForeignKey('Tournaments', models.DO_NOTHING)
    team = models.ForeignKey(Teams, models.DO_NOTHING)
    registered_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tournament_teams'
        unique_together = (('tournament', 'team'),)


class Tournaments(models.Model):
    tournament_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, null=True)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    organized_by = models.ForeignKey('Users', models.DO_NOTHING, db_column='organized_by')
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tournaments'


class Users(models.Model):
    user_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    email = models.CharField(unique=True, max_length=150)
    password = models.CharField(max_length=255)
    role = models.CharField(max_length=50)
    created_at = models.DateTimeField(blank=True, null=True)
    # Object-level RBAC links (added manually in the DB; see
    # sql/0001_users_role_link_columns.sql). Only relevant for the
    # matching roles; NULL for everyone else.
    team = models.ForeignKey(
        'Teams', models.DO_NOTHING, blank=True, null=True,
        db_column='team_id', related_name='managed_by_users',
    )
    player = models.ForeignKey(
        'Players', models.DO_NOTHING, blank=True, null=True,
        db_column='player_id', related_name='player_users',
    )
    referee = models.ForeignKey(
        'Referees', models.DO_NOTHING, blank=True, null=True,
        db_column='referee_id', related_name='referee_users',
    )

    class Meta:
        managed = False
        db_table = 'users'


class Venues(models.Model):
    venue_id = models.AutoField(primary_key=True)
    venue_name = models.CharField(max_length=150)
    location = models.CharField(max_length=255, blank=True, null=True)
    capacity = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'venues'
