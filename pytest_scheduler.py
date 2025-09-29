# tests/test_app_fixed.py
import json
import logging
from datetime import datetime, timedelta, date

import pytest

# IMPORTANT: import your app AFTER pytest is loaded so we can monkeypatch easily
import app_fixed as appmod
from app_fixed import app, db, Employee, Schedule, TimeOffRequest, ShiftTrade

# ---------------------------------------------------------------------------
# Global logging config for verbose, readable test output
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
)
logger = logging.getLogger("tests")


# ---------------------------------------------------------------------------
# Pytest fixtures: app context, clean DB, seeded data, test client
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _show_versions():
    logger.info("Starting test session for Scheduler_App")
    logger.info("Flask: %s | SQLAlchemy bound: %s", app.__class__.__name__, bool(db))


@pytest.fixture(autouse=True)
def _is_testing_env(monkeypatch):
    # Make sure Flask knows we are testing
    app.config["TESTING"] = True
    # Force Werkzeug not to swallow exceptions
    app.config["PROPAGATE_EXCEPTIONS"] = True


@pytest.fixture
def app_ctx(monkeypatch):
    """
    - Uses the real SQLite file configured in the app (keeps the app wiring simple).
    - Drops & recreates all tables each test for isolation.
    - Seeds a minimal, realistic test dataset.
    - Monkeypatches render_template so HTML routes don't require actual templates.
    """
    with app.app_context():
        # Clean slate
        db.drop_all()
        db.create_all()

        # Disable first-request auto-seeding by ensuring at least 1 employee exists
        # (so appmod.create_tables will skip).
        # Then we'll reset to our precise seed below.
        tmp_emp = Employee(name="SEED_SKIP", email="seed_skip@localhost")
        db.session.add(tmp_emp)
        db.session.commit()

        # Now reset to our exact seed set
        db.session.query(Schedule).delete()
        db.session.query(TimeOffRequest).delete()
        db.session.query(ShiftTrade).delete()
        db.session.query(Employee).delete()
        db.session.commit()

        # Seed realistic employees
        emps = [
            # name, email, is_lead, nights_only, hours, special, pref
            ("Patty Golden", "patty@test.com", True,  False, 60, "LEAD",  "DAY"),
            ("Nicole Dempster", "nicole@test.com", False, True,  30, None, "NIGHT"),
            ("Vicki Theler", "vicki@test.com", False, False, 20, "LEGAL_CAP", "BOTH"),
            ("Mayra Bradley", "mayra@test.com", False, False, 40, None, "BOTH"),
            ("Lisa Dixon", "lisa@test.com", False, False, 40, None, "BOTH"),
            ("Dan Smith", "dan@test.com", False, False, 40, None, "BOTH"),
        ]
        for n, e, lead, night, hrs, spec, pref in emps:
            db.session.add(Employee(
                name=n, email=e, is_lead=lead, nights_only=night,
                max_hours_per_week=hrs, special_schedule=spec, shift_preference=pref
            ))
        db.session.commit()

        # Patch render_template so HTML routes don't fail without templates
        monkeypatch.setattr(appmod, "render_template", lambda *a, **k: "<html>OK</html>")

        yield  # tests run here

        # Clean up after each test
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app_ctx):
    return app.test_client()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def next_week_monday(base: date | None = None) -> date:
    base = base or date.today()
    # find next Monday (including today if Monday)
    return base + timedelta(days=(0 - base.weekday()) % 7)


def json_ok(resp):
    return resp.status_code == 200 and resp.is_json and resp.json.get("success") is True


# ---------------------------------------------------------------------------
# Smoke test: HTML page routes (render_template is monkeypatched)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("route", ["/", "/dashboard", "/schedule", "/employees", "/timeoff", "/shift-trades", "/rules"])
def test_page_routes_200(client, route, caplog):
    caplog.set_level(logging.INFO)
    r = client.get(route)
    assert r.status_code == 200, f"Route {route} should serve 200"


# ---------------------------------------------------------------------------
# Employees CRUD
# ---------------------------------------------------------------------------
def test_employees_crud_flow(client, caplog):
    caplog.set_level(logging.INFO)

    # Initial list (from seed)
    r = client.get("/api/employees")
    assert json_ok(r), f"/api/employees GET failed: {r.json}"
    seed_count = r.json["count"]
    assert seed_count >= 6

    # Create new employee via API
    payload = {
        "name": "Alex Johnson",
        "email": "alex@test.com",
        "shift_type": "BOTH",       # maps to shift_preference + nights_only False
        "hours_per_week": 32,       # mapped to max_hours_per_week
        "special_schedule": "NEW_HIRE",
        "cannot_work_days": ["Sun", "Sat"],
        "max_consecutive_days": 5,
        "min_rest_hours": 10
    }
    r = client.post("/api/employees", json=payload)
    assert r.status_code == 201 and r.json.get("success") is True, f"Create employee failed: {r.json}"
    new_emp = r.json["employee"]
    assert new_emp["email"] == "alex@test.com"
    assert new_emp["max_hours_per_week"] == 32
    assert new_emp["shift_preference"] == "BOTH"

    # Update newly created employee
    update_payload = {
        "hours_per_week": 36,
        "shift_type": "DAY",          # should flip nights_only False and set pref DAY
        "special_schedule": "LEAD"    # should set is_lead True
    }
    r = client.put(f"/api/employees/{new_emp['id']}", json=update_payload)
    assert json_ok(r), f"Update employee failed: {r.json}"
    updated = r.json["employee"]
    assert updated["max_hours_per_week"] == 36
    assert updated["shift_preference"] == "DAY"
    assert updated["is_lead"] is True
    assert updated["nights_only"] is False

    # Deactivate employee
    r = client.delete(f"/api/employees/{new_emp['id']}")
    assert json_ok(r), f"Deactivate employee failed: {r.json}"

    # Ensure deactivated employee no longer comes back in active list
    r = client.get("/api/employees")
    assert json_ok(r), f"/api/employees after delete failed: {r.json}"
    emails = [e["email"] for e in r.json["employees"]]
    assert "alex@test.com" not in emails


# ---------------------------------------------------------------------------
# Time Off creation/approval + Schedule generation should respect PTO
# ---------------------------------------------------------------------------
def test_timeoff_and_generation_respects_pto(client, caplog):
    caplog.set_level(logging.INFO)

    # choose an existing day-shift-friendly employee (Patty or Mayra/Lisa/Dan)
    with app.app_context():
        emp_day = Employee.query.filter_by(name="Mayra Bradley").first()
        assert emp_day is not None

    start_monday = next_week_monday()
    pto_day = start_monday  # PTO on the first day we will generate

    # Create PTO (DAY only) for that date
    r = client.post("/api/timeoff", json={
        "employee_id": emp_day.id,
        "start_date": pto_day.isoformat(),
        "end_date": pto_day.isoformat(),
        "shift_type": "DAY",
        "reason": "Test PTO"
    })
    assert r.status_code == 201 and r.json.get("success"), f"Create PTO failed: {r.json}"
    pto_id = r.json["request"]["id"]

    # Approve the PTO
    r = client.put(f"/api/timeoff/{pto_id}/approve")
    assert json_ok(r), f"PTO approve failed: {r.json}"

    # Generate 1 week schedule that includes the PTO date
    r = client.post("/api/schedule/generate", json={
        "start_date": start_monday.isoformat(),
        "weeks": 1
    })

    # NOTE: If this test fails HERE with 500 and a message referencing
    # `_assign_shifts_with_fair_distribution`, your agent needs to fix the
    # function to use the `available_employees` parameter instead of
    # undefined `available_day_employees`/`available_night_employees`.
    assert r.status_code == 200 and r.is_json, f"Schedule generation HTTP failed: {r.data}"
    assert r.json.get("success") is True, f"Schedule generation failed: {r.json}"

    # Fetch that day’s schedule
    r = client.get("/api/schedule", query_string={
        "start_date": pto_day.isoformat(),
        "end_date": pto_day.isoformat(),
    })
    assert json_ok(r), f"Get schedule failed: {r.json}"
    day_sched = r.json["schedules"]

    # Ensure the PTO employee is NOT assigned any DAY shift that date
    illegal = [
        s for s in day_sched
        if s["employee_id"] == emp_day.id and s["shift_type"] == "DAY"
    ]
    assert not illegal, (
        f"Employee {emp_day.name} was scheduled for DAY on PTO date {pto_day} -> {illegal}"
    )


# ---------------------------------------------------------------------------
# Shift Trades flow: create trade, approve, verify swap in DB
# ---------------------------------------------------------------------------
def test_shift_trades_flow(client, caplog):
    caplog.set_level(logging.INFO)

    start_monday = next_week_monday()

    # Generate 1 week schedule to ensure there are shifts to trade
    r = client.post("/api/schedule/generate", json={
        "start_date": start_monday.isoformat(),
        "weeks": 1
    })
    assert r.status_code == 200 and r.is_json, f"Schedule generation HTTP failed: {r.data}"
    assert r.json.get("success") is True, f"Schedule generation failed: {r.json}"

    # Pull all schedule for the week, pick two different employees' shifts on 2 different days
    r = client.get("/api/schedule", query_string={
        "start_date": start_monday.isoformat(),
        "end_date": (start_monday + timedelta(days=6)).isoformat()
    })
    assert json_ok(r), f"Fetching schedule failed: {r.json}"
    full = r.json["schedules"]
    assert len(full) > 0, "No shifts generated to trade"

    # Pick two distinct shifts with different employees
    s1 = None
    s2 = None
    seen_emp = set()
    for s in full:
        emp = s["employee_id"]
        if s1 is None:
            s1 = s
            seen_emp.add(emp)
        elif emp not in seen_emp:
            s2 = s
            break
    assert s1 and s2, "Unable to find two different employees' shifts for a trade"

    req_emp_id = s1["employee_id"]
    tgt_emp_id = s2["employee_id"]
    original_schedule_id = s1["id"]
    trade_schedule_id = s2["id"]

    # Create the trade
    r = client.post("/api/trades", json={
        "requesting_employee_id": req_emp_id,
        "target_employee_id": tgt_emp_id,
        "original_schedule_id": original_schedule_id,
        "trade_schedule_id": trade_schedule_id,
        "trade_reason": "Coverage swap test"
    })
    assert r.status_code == 201 and r.json.get("success") is True, f"Create trade failed: {r.json}"
    trade_id = r.json["trade"]["id"]

    # Approve the trade (this should swap employee_ids for those two schedule rows)
    r = client.put(f"/api/trades/{trade_id}/approve")
    assert json_ok(r), f"Approve trade failed: {r.json}"

    # Verify in DB the swap occurred
    with app.app_context():
        s1_db = db.session.get(Schedule, original_schedule_id)
        s2_db = db.session.get(Schedule, trade_schedule_id)
        assert s1_db is not None and s2_db is not None

        assert s1_db.employee_id == tgt_emp_id, (
            f"s1 not swapped: expected employee {tgt_emp_id}, got {s1_db.employee_id}"
        )
        assert s2_db.employee_id == req_emp_id, (
            f"s2 not swapped: expected employee {req_emp_id}, got {s2_db.employee_id}"
        )


# ---------------------------------------------------------------------------
# Get upcoming shifts for an employee
# ---------------------------------------------------------------------------
def test_get_employee_shifts_endpoint(client, caplog):
    caplog.set_level(logging.INFO)

    # pick an existing employee
    with app.app_context():
        any_emp = Employee.query.filter_by(active=True).first()
        assert any_emp is not None

    start_monday = next_week_monday()
    # Ensure schedule exists for the window
    r = client.post("/api/schedule/generate", json={
        "start_date": start_monday.isoformat(),
        "weeks": 1
    })
    assert r.status_code == 200 and r.is_json, f"Schedule generation HTTP failed: {r.data}"
    assert r.json.get("success") is True, f"Schedule generation failed: {r.json}"

    r = client.get(f"/api/employees/{any_emp.id}/shifts", query_string={
        "start_date": start_monday.isoformat(),
        "end_date": (start_monday + timedelta(days=14)).isoformat()
    })
    assert json_ok(r), f"Get employee shifts failed: {r.json}"
    assert isinstance(r.json.get("shifts"), list)
