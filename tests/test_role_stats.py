import pytest

from ccc_mcp.core.roles import AGENT_ROLES, ROLE_STATS, STAT_AXES, STAT_MAX, role_stats, team_stats


def test_every_agent_role_has_a_score_for_every_ability():
    assert set(ROLE_STATS) == set(AGENT_ROLES)
    for role, scores in ROLE_STATS.items():
        assert len(scores) == len(STAT_AXES), role
        assert all(0 <= value <= STAT_MAX for value in scores), role
        assert max(scores) >= 4, f"{role} should be good at something"


def test_a_role_reports_its_scores_by_ability():
    assert role_stats("RS")["research"] == 5
    assert role_stats("RS")["risk"] == 0
    assert role_stats("nobody") is None


def test_the_team_is_as_strong_as_its_best_member_at_each_ability():
    team = team_stats(["RS", "WR"])["team"]
    assert team["research"] == 5 and team["creation"] == 5
    assert team["risk"] == 0


def test_strong_backups_add_a_bonus_below_the_cap():
    assert team_stats(["ED"])["team"]["verification"] == 3
    assert team_stats(["ED", "RS"])["team"]["verification"] == 3.5  # RS (3) backs ED up
    assert team_stats(["ED", "RS", "SEC"])["team"]["verification"] == 4.0
    assert team_stats(["RS", "VC", "CS"])["team"]["research"] == STAT_MAX  # the cap holds
    assert team_stats(["ED", "SD"])["team"]["verification"] == 3  # SD (1) is no backup


def test_a_role_that_appears_twice_counts_once():
    assert team_stats(["SEC", "SEC"]) == team_stats(["SEC"])


def test_gaps_name_the_abilities_nobody_has():
    chart = team_stats(["RS", "VC"])
    assert chart["gaps"] == ["planning", "creation"]
    assert team_stats([])["power"] == 0


def test_power_is_the_sum_of_the_team_scores():
    chart = team_stats(["PA", "RS", "AN", "PL", "VC"])
    assert chart["power"] == round(sum(chart["team"].values()), 1)


def test_workflows_and_tasks_carry_their_ability_charts(bridge, human):
    catalog = bridge.workflow_catalog("demo")
    assert catalog["stat_axes"] == list(STAT_AXES)
    assert set(catalog["role_stats"]) == set(ROLE_STATS)
    life = next(flow for flow in catalog["workflows"] if flow["id"] == "life")
    assert life["stats"]["team"]["research"] == 5

    bridge.authorize_project_local("demo")
    bridge.configure_workflow("demo", "life")
    task_id = bridge.start_project_request("demo", "Plan a trip")["workstream"]["id"]
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["workflow"]["stats"]["team"] == life["stats"]["team"]
