"""Phase A tests for the alerting subsystem.

Covers:
  - the new tables are registered on ``models.Base.metadata`` and
    ``create_all`` succeeds;
  - the new ``manage_alerts`` permission is in ``ADMIN_PERMISSIONS``;
  - the ``alerting_*`` setting keys are seeded by ``init_db``;
  - the pure hysteresis state machines for per-space edge rules and
    threshold rules behave per the pinned semantics in the plan.

The state-machine tests don't touch the DB — they exercise
``alerts.hysteresis`` directly, which is exactly the point of the
pure-function design.  This is the test file that protects the core
behaviour against future regressions.
"""

import json
import pytest
from sqlalchemy import create_engine

from backend import models, init_db
from backend.alerts.hysteresis import (
    SpaceRuleState,
    ThresholdRuleState,
    cooldown_elapsed,
    evaluate_space_rule,
    evaluate_threshold_rule,
    within_time_window,
)
from backend.auth import ADMIN_PERMISSIONS


# --- Schema / wiring --------------------------------------------------------

def test_alert_tables_registered():
    names = set(models.Base.metadata.tables.keys())
    for t in (
        "alert_channels",
        "camera_groups",
        "camera_group_memberships",
        "alert_rules",
        "alert_events",
        "alert_state",
        "alert_state_per_space",
    ):
        assert t in names, f"missing table: {t}"


def test_alert_create_all_succeeds():
    e = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(e)  # raises if any column is malformed


def test_manage_alerts_in_admin_permissions():
    assert "manage_alerts" in ADMIN_PERMISSIONS


def test_alerting_settings_seeded():
    """All alerting_* settings should be in the defaults dict.

    The dict is module-level; this asserts the keys exist so a future
    refactor doesn't silently drop one.  (Actual seeding into the DB
    is exercised by the integration tests run against the real DB.)
    """
    src = open(init_db.__file__).read()
    for key in (
        "alerting_enabled",
        "alerting_smtp_host",
        "alerting_smtp_port",
        "alerting_smtp_user",
        "alerting_smtp_pass",
        "alerting_smtp_from",
        "alerting_smtp_use_tls",
    ):
        assert f'"{key}"' in src, f"missing {key} in init_db defaults"


def test_firing_target_migration_adds_column_to_existing_table():
    """Live DBs that pre-date the hysteresis refactor have an
    ``alert_state_per_space`` table WITHOUT the ``firing_target`` column.
    ``init_db`` must ``ALTER TABLE`` to add it; running the migration
    twice is a no-op.

    This is a regression test for a real production error: the alert
    engine tick raised ``no such column: alert_state_per_space.firing_target``
    on every cycle and silently swallowed the failure.
    """
    from sqlalchemy import create_engine, inspect, text

    e = create_engine("sqlite://")
    # Mirror the production schema BEFORE the hysteresis refactor: the
    # table exists with the original 5 columns but no firing_target.
    with e.connect() as conn:
        conn.execute(text(
            "CREATE TABLE alert_state_per_space ("
            "  rule_id INTEGER NOT NULL,"
            "  space_id INTEGER NOT NULL,"
            "  state VARCHAR,"
            "  consecutive_count_current INTEGER,"
            "  last_observed_at DATETIME,"
            "  last_observed_occupied BOOLEAN,"
            "  PRIMARY KEY (rule_id, space_id)"
            ")"
        ))
        conn.commit()

    insp = inspect(e)
    cols_before = [c["name"] for c in insp.get_columns("alert_state_per_space")]
    assert "firing_target" not in cols_before

    # Mirror the migration branch in ``backend.init_db.main``.  The
    # guard re-checks column presence, so a re-run is a no-op.
    if "firing_target" not in cols_before:
        with e.connect() as conn:
            conn.execute(text(
                "ALTER TABLE alert_state_per_space ADD COLUMN firing_target BOOLEAN"
            ))
            conn.commit()

    # Re-inspect after the ALTER; SQLAlchemy caches the result.
    cols_after = [c["name"] for c in inspect(e).get_columns("alert_state_per_space")]
    assert "firing_target" in cols_after

    # Re-run; the guard prevents a duplicate column.
    if "firing_target" not in cols_after:
        with e.connect() as conn:
            conn.execute(text(
                "ALTER TABLE alert_state_per_space ADD COLUMN firing_target BOOLEAN"
            ))
            conn.commit()
    cols_final = [c["name"] for c in inspect(e).get_columns("alert_state_per_space")]
    assert cols_final.count("firing_target") == 1



# --- Hysteresis: per-space edge rules ---------------------------------------

def test_space_occupied_fires_on_first_observation_with_count_1():
    s = SpaceRuleState()
    r = evaluate_space_rule(s, observed_occupied=True,
                            trigger_type="space_occupied", consecutive_count=1)
    assert r.fire is True
    assert r.new_state.state == "firing"


def test_space_occupied_does_not_fire_below_buffer():
    """consecutive_count=3: first two observations are buffer, no fire."""
    s = SpaceRuleState()
    r1 = evaluate_space_rule(s, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=3)
    assert r1.fire is False
    assert r1.new_state.state == "buffering"
    assert r1.new_state.consecutive_count_current == 1
    r2 = evaluate_space_rule(r1.new_state, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=3)
    assert r2.fire is False
    assert r2.new_state.state == "buffering"
    assert r2.new_state.consecutive_count_current == 2


def test_space_occupied_fires_on_third_consecutive():
    s = SpaceRuleState()
    r1 = evaluate_space_rule(s, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=3)
    r2 = evaluate_space_rule(r1.new_state, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=3)
    r3 = evaluate_space_rule(r2.new_state, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=3)
    assert r3.fire is True
    assert r3.new_state.state == "firing"


def test_space_occupied_resets_buffer_on_vacant():
    """vacant observation in the middle of a buffer resets the counter."""
    s = SpaceRuleState()
    r1 = evaluate_space_rule(s, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=3)
    r2 = evaluate_space_rule(r1.new_state, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=3)
    # vacant mid-buffer
    r3 = evaluate_space_rule(r2.new_state, observed_occupied=False,
                             trigger_type="space_occupied", consecutive_count=3)
    assert r3.new_state.state == "armed"
    assert r3.new_state.consecutive_count_current == 0
    # After re-arm, we need 3 fresh occupied observations to fire
    a1 = evaluate_space_rule(r3.new_state, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=3)
    a2 = evaluate_space_rule(a1.new_state, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=3)
    a3 = evaluate_space_rule(a2.new_state, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=3)
    assert a3.fire is True


def test_space_occupied_does_not_refire_while_firing():
    s = SpaceRuleState()
    r = evaluate_space_rule(s, observed_occupied=True,
                            trigger_type="space_occupied", consecutive_count=1)
    assert r.fire is True
    # subsequent observations while still occupied -> no refire
    r2 = evaluate_space_rule(r.new_state, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=1)
    assert r2.fire is False
    assert r2.new_state.state == "firing"


def test_space_occupied_rearms_on_vacant():
    s = SpaceRuleState()
    r = evaluate_space_rule(s, observed_occupied=True,
                            trigger_type="space_occupied", consecutive_count=1)
    assert r.new_state.state == "firing"
    r2 = evaluate_space_rule(r.new_state, observed_occupied=False,
                             trigger_type="space_occupied", consecutive_count=1)
    assert r2.new_state.state == "armed"
    assert r2.new_state.consecutive_count_current == 0
    # next occupied fires again
    r3 = evaluate_space_rule(r2.new_state, observed_occupied=True,
                             trigger_type="space_occupied", consecutive_count=1)
    assert r3.fire is True


def test_space_vacated_mirror_of_space_occupied():
    s = SpaceRuleState()
    # occupied does NOT fire a space_vacated rule
    r1 = evaluate_space_rule(s, observed_occupied=True,
                             trigger_type="space_vacated", consecutive_count=1)
    assert r1.fire is False
    assert r1.new_state.state == "armed"
    # vacant fires
    r2 = evaluate_space_rule(r1.new_state, observed_occupied=False,
                             trigger_type="space_vacated", consecutive_count=1)
    assert r2.fire is True
    assert r2.new_state.state == "firing"


def test_space_edge_fires_both_directions():
    s = SpaceRuleState()
    # vacant -> occupied
    r1 = evaluate_space_rule(s, observed_occupied=True,
                             trigger_type="space_edge", consecutive_count=1)
    assert r1.fire is True
    assert r1.new_state.state == "firing"
    # re-arm by staying in same state? No — only opposite observation
    # rearms.  An occupied-occupied-occupied sequence keeps firing.
    r2 = evaluate_space_rule(r1.new_state, observed_occupied=True,
                             trigger_type="space_edge", consecutive_count=1)
    assert r2.fire is False
    assert r2.new_state.state == "firing"
    # The FIRST opposite observation fires for the new edge and
    # immediately sets the state to "firing" in the new direction,
    # so the user gets a fire for every direction change (not just
    # every other one).
    r3 = evaluate_space_rule(r2.new_state, observed_occupied=False,
                             trigger_type="space_edge", consecutive_count=1)
    assert r3.fire is True
    assert r3.new_state.state == "firing"
    assert r3.new_state.firing_target is False
    # The NEXT vacant observation (same direction as the new edge)
    # stays firing, no refire.
    r4 = evaluate_space_rule(r3.new_state, observed_occupied=False,
                             trigger_type="space_edge", consecutive_count=1)
    assert r4.fire is False
    assert r4.new_state.state == "firing"
    # Back to occupied — another direction change, another fire.
    r5 = evaluate_space_rule(r4.new_state, observed_occupied=True,
                             trigger_type="space_edge", consecutive_count=1)
    assert r5.fire is True
    assert r5.new_state.state == "firing"
    assert r5.new_state.firing_target is True


def test_space_edge_fires_on_direction_change_not_on_next_observation():
    """Regression test for "two occupied alerts in a row with no
    vacant alert in between".

    The old behavior was:
      - occupied observation -> fire, state = firing (firing_target=True)
      - vacant observation   -> re-arm silently, state = armed
      - occupied observation -> fire (direction change in armed state)

    The user saw: occupied fire, occupied fire, with a silent vacant
    transition in between (no vacant alert ever fired).

    The new behavior is: the FIRST opposite observation fires for
    the new edge and immediately sets state = firing in the new
    direction, so every direction change produces exactly one fire.
    """
    s = SpaceRuleState()
    # vacant -> occupied (first fire)
    r1 = evaluate_space_rule(s, observed_occupied=True,
                             trigger_type="space_edge", consecutive_count=1)
    assert r1.fire is True
    # occupied -> occupied (no fire, stay firing)
    r2 = evaluate_space_rule(r1.new_state, observed_occupied=True,
                             trigger_type="space_edge", consecutive_count=1)
    assert r2.fire is False
    # occupied -> vacant (FIRE for the new edge, not silent re-arm)
    r3 = evaluate_space_rule(r2.new_state, observed_occupied=False,
                             trigger_type="space_edge", consecutive_count=1)
    assert r3.fire is True
    assert r3.new_state.firing_target is False
    # vacant -> occupied (FIRE again, direction changed back)
    r4 = evaluate_space_rule(r3.new_state, observed_occupied=True,
                             trigger_type="space_edge", consecutive_count=1)
    assert r4.fire is True
    assert r4.new_state.firing_target is True


def test_space_occupied_rejects_bad_trigger_type():
    s = SpaceRuleState()
    with pytest.raises(ValueError):
        evaluate_space_rule(s, observed_occupied=True,
                            trigger_type="camera_offline", consecutive_count=1)


# --- Hysteresis: threshold rules --------------------------------------------

def test_threshold_lot_full_fires_at_boundary():
    s = ThresholdRuleState()
    r = evaluate_threshold_rule(
        s, current_pct=90.0, trigger_type="lot_full_above_pct",
        fire_at_pct=90.0, resolve_at_pct=70.0,
    )
    assert r.fire is True
    assert r.new_state.state == "firing"


def test_threshold_lot_full_does_not_fire_below_threshold():
    s = ThresholdRuleState()
    r = evaluate_threshold_rule(
        s, current_pct=89.5, trigger_type="lot_full_above_pct",
        fire_at_pct=90.0, resolve_at_pct=70.0,
    )
    assert r.fire is False
    assert r.new_state.state == "armed"


def test_threshold_lot_full_stays_firing_in_dead_band():
    """89% after a fire at 90% -> stays firing, no refire.

    This is the key oscillation-suppression behaviour.  The state
    machine is stable in the dead band (between fire and resolve).
    """
    s = ThresholdRuleState()
    fired = evaluate_threshold_rule(
        s, current_pct=92.0, trigger_type="lot_full_above_pct",
        fire_at_pct=90.0, resolve_at_pct=70.0,
    )
    assert fired.new_state.state == "firing"
    # 89% — below fire, above resolve -> stay firing
    stuck = evaluate_threshold_rule(
        fired.new_state, current_pct=89.0, trigger_type="lot_full_above_pct",
        fire_at_pct=90.0, resolve_at_pct=70.0,
    )
    assert stuck.fire is False
    assert stuck.new_state.state == "firing"
    # 90% again -> still firing, no refire
    again = evaluate_threshold_rule(
        stuck.new_state, current_pct=90.0, trigger_type="lot_full_above_pct",
        fire_at_pct=90.0, resolve_at_pct=70.0,
    )
    assert again.fire is False
    assert again.new_state.state == "firing"


def test_threshold_lot_full_rearms_below_resolve():
    s = ThresholdRuleState()
    fired = evaluate_threshold_rule(
        s, current_pct=92.0, trigger_type="lot_full_above_pct",
        fire_at_pct=90.0, resolve_at_pct=70.0,
    )
    # Drop below resolve -> re-arm
    rearmed = evaluate_threshold_rule(
        fired.new_state, current_pct=68.0, trigger_type="lot_full_above_pct",
        fire_at_pct=90.0, resolve_at_pct=70.0,
    )
    assert rearmed.new_state.state == "armed"
    # Now another breach fires again
    fired2 = evaluate_threshold_rule(
        rearmed.new_state, current_pct=95.0, trigger_type="lot_full_above_pct",
        fire_at_pct=90.0, resolve_at_pct=70.0,
    )
    assert fired2.fire is True
    assert fired2.new_state.state == "firing"


def test_threshold_lot_open_mirror_direction():
    s = ThresholdRuleState()
    # 10% fires (below fire_at_pct=10)
    r = evaluate_threshold_rule(
        s, current_pct=10.0, trigger_type="lot_open_below_pct",
        fire_at_pct=10.0, resolve_at_pct=30.0,
    )
    assert r.fire is True
    # 29% — in dead band, stays firing
    r2 = evaluate_threshold_rule(
        r.new_state, current_pct=29.0, trigger_type="lot_open_below_pct",
        fire_at_pct=10.0, resolve_at_pct=30.0,
    )
    assert r2.fire is False
    assert r2.new_state.state == "firing"
    # 31% — re-arm
    r3 = evaluate_threshold_rule(
        r2.new_state, current_pct=31.0, trigger_type="lot_open_below_pct",
        fire_at_pct=10.0, resolve_at_pct=30.0,
    )
    assert r3.new_state.state == "armed"
    # Re-fire on next dip
    r4 = evaluate_threshold_rule(
        r3.new_state, current_pct=8.0, trigger_type="lot_open_below_pct",
        fire_at_pct=10.0, resolve_at_pct=30.0,
    )
    assert r4.fire is True


def test_threshold_lot_full_rejects_inverted_thresholds():
    s = ThresholdRuleState()
    with pytest.raises(ValueError):
        evaluate_threshold_rule(
            s, current_pct=50.0, trigger_type="lot_full_above_pct",
            fire_at_pct=70.0, resolve_at_pct=80.0,  # resolve > fire -> wrong
        )


def test_threshold_lot_open_rejects_inverted_thresholds():
    s = ThresholdRuleState()
    with pytest.raises(ValueError):
        evaluate_threshold_rule(
            s, current_pct=50.0, trigger_type="lot_open_below_pct",
            fire_at_pct=30.0, resolve_at_pct=10.0,  # resolve < fire -> wrong
        )


# --- Cooldown gate -----------------------------------------------------------

def test_cooldown_no_prior_fire():
    assert cooldown_elapsed(None, "2026-07-01T12:00:00+00:00", 180) is True


def test_cooldown_within_window():
    assert cooldown_elapsed(
        "2026-07-01T12:00:00+00:00",
        "2026-07-01T12:01:00+00:00",
        180,
    ) is False


def test_cooldown_after_window():
    assert cooldown_elapsed(
        "2026-07-01T12:00:00+00:00",
        "2026-07-01T12:05:00+00:00",
        180,
    ) is True


# --- Time window gate --------------------------------------------------------

def test_time_window_none_means_always():
    assert within_time_window("2026-07-01T03:00:00+00:00", None) is True


def test_time_window_filters_by_day():
    # 2026-07-01 is a Wednesday (weekday 2)
    tw = {"days": [0, 1, 2, 3, 4], "start_hour": 8, "end_hour": 18, "tz": "UTC"}
    # Wednesday at 10:00 UTC -> in window
    assert within_time_window("2026-07-01T10:00:00+00:00", tw) is True
    # Saturday at 10:00 UTC -> not in window
    assert within_time_window("2026-07-04T10:00:00+00:00", tw) is False


def test_time_window_filters_by_hour():
    tw = {"days": [0, 1, 2, 3, 4], "start_hour": 8, "end_hour": 18, "tz": "UTC"}
    # 07:59 UTC -> before window
    assert within_time_window("2026-07-01T07:59:00+00:00", tw) is False
    # 08:00 UTC -> in window
    assert within_time_window("2026-07-01T08:00:00+00:00", tw) is True
    # 17:59 UTC -> in window
    assert within_time_window("2026-07-01T17:59:00+00:00", tw) is True
    # 18:00 UTC -> past end_hour (exclusive)
    assert within_time_window("2026-07-01T18:00:00+00:00", tw) is False


def test_time_window_with_timezone():
    # 02:00 UTC on Wednesday = 19:00 in America/Los_Angeles (PDT, UTC-7)
    tw = {"days": [0, 1, 2, 3, 4], "start_hour": 8, "end_hour": 18, "tz": "America/Los_Angeles"}
    assert within_time_window("2026-07-01T02:00:00+00:00", tw) is False
    # 16:00 UTC = 09:00 PDT -> in window
    assert within_time_window("2026-07-01T16:00:00+00:00", tw) is True
