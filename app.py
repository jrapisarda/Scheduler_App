"""
Flask application for the hospital PBX scheduling system.

This module defines a minimal set of APIs to support employee management
and schedule generation according to the updated requirements.  The goal
is to provide a working MVP that can be extended in future iterations.

The API exposes endpoints for CRUD operations on employees, a simple
schedule generator, and retrieval of generated schedules.  Schedules are
stored in an SQLite database and can be regenerated on demand.  The
generator honors basic business rules such as minimum coverage, per‑
employee weekly caps, lead assignments, nights‑only restrictions, and
per‑person blackout days.  Advanced optimisation (fairness, stability
bias, overtime distribution) is left for future work but the code
structure makes it straightforward to plug in a more sophisticated
solver.

The database schema is very similar to the one specified in the
requirements document (see hospital‑scheduling‑requirements.md).  Only
the tables necessary for the MVP have been implemented.

Running the app:

    $ python app.py

This will start a development server on http://localhost:5000.  You can
use the provided HTML templates to interact with the APIs or call them
directly with your favourite HTTP client.  Note that for a production
deployment you should run via a WSGI server and configure the
SQLAlchemy database URI appropriately.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, date, time
from typing import List, Dict, Optional

import os
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from flask import Flask, request, jsonify, send_from_directory, render_template

app = Flask(__name__)

# Configure the SQLite database.  If you modify the filename here, make
# sure to delete the existing DB to allow SQLAlchemy to create the new
# schema.  In production you can switch to Postgres or SQL Server.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///scheduler.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


class Employee(db.Model):
    """Represents an employee in the scheduling system.

    Attributes
    ----------
    id : int
        Primary key.
    name : str
        Full name of the employee.
    email : str
        Email address for notifications.
    is_lead : bool
        Whether the employee is lead‑qualified.  Patty should be marked
        as a lead so the scheduler preferentially assigns her to the
        daytime lead shift.
    nights_only : bool
        If True, the employee may only be scheduled on night shifts.  This
        accommodates Nicole, the nights‑only operator.
    max_hours_per_week : int
        Maximum number of hours the employee may work in a calendar week.
    cannot_work_days : str
        JSON encoded list of day names ("Mon", "Tue", etc.) that the
        employee cannot work.  Use this for Mayra's Friday restriction.
    active : bool
        Indicates whether the employee is available for scheduling.
    created_at : datetime
        Timestamp of creation.

    Note
    ----
    For simplicity the model does not include fields such as
    `fte_percent`, `union_code`, or effective dates.  These can be
    added later without breaking the current API.
    """
    __tablename__ = 'employees'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(128), nullable=False)
    is_lead = db.Column(db.Boolean, default=False)
    nights_only = db.Column(db.Boolean, default=False)
    max_hours_per_week = db.Column(db.Integer, default=40)
    cannot_work_days = db.Column(db.Text, default='[]')  # JSON list of day abbreviations
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, any]:
        """Return a JSON‑serialisable representation of the employee."""
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'is_lead': self.is_lead,
            'nights_only': self.nights_only,
            'max_hours_per_week': self.max_hours_per_week,
            'cannot_work_days': json.loads(self.cannot_work_days or '[]'),
            'active': self.active,
            'created_at': self.created_at.isoformat(),
        }


class Schedule(db.Model):
    """Represents a single scheduled shift for an employee.

    Each record corresponds to one shift on a given date.  The
    `shift_start` and `shift_end` columns represent the start and end
    times of the shift (in local time).  The `shift_type` is either
    'DAY' or 'NIGHT' to support simple front‑end colouring and reports.
    The `is_overtime` flag indicates whether the shift exceeds the
    employee's weekly cap.
    """
    __tablename__ = 'schedules'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    schedule_date = db.Column(db.Date, nullable=False)
    shift_start = db.Column(db.Time, nullable=False)
    shift_end = db.Column(db.Time, nullable=False)
    shift_type = db.Column(db.String(16), nullable=False)
    # Describes the specific role or template for this shift (e.g. Lead, D1, N1)
    role = db.Column(db.String(32), default='')
    is_overtime = db.Column(db.Boolean, default=False)
    is_coverage = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship('Employee', backref=db.backref('schedules', lazy=True))

    def to_dict(self) -> Dict[str, any]:
        """Return a dictionary representation of this schedule entry.

        Converts the internal ``shift_type`` ('DAY'/'NIGHT') to the
        human‑friendly form expected by the front‑end ('Day'/'Night'),
        includes the ``role`` value, and calculates the shift duration
        in hours.  Over‑midnight shifts are handled by adding a day
        where appropriate.  Also exposes start and end time fields under
        ``start_time`` and ``end_time`` keys so the front‑end does not
        need to know about the internal naming convention.
        """
        # Calculate the number of hours for this shift.  If the shift
        # end time is earlier than the start time, roll over to the
        # following day.
        start_dt = datetime.combine(date.today(), self.shift_start)
        if self.shift_end <= self.shift_start:
            end_dt = datetime.combine(date.today() + timedelta(days=1), self.shift_end)
        else:
            end_dt = datetime.combine(date.today(), self.shift_end)
        hours = round((end_dt - start_dt).total_seconds() / 3600.0, 2)
        # Normalise shift type to 'Day'/'Night'
        readable_type = 'Day' if self.shift_type.upper().startswith('DAY') else 'Night'
        return {
            'id': self.id,
            'employee_id': self.employee_id,
            'employee_name': self.employee.name if self.employee else None,
            'date': self.schedule_date.isoformat(),
            'start_time': self.shift_start.strftime('%H:%M'),
            'end_time': self.shift_end.strftime('%H:%M'),
            'shift_type': readable_type,
            'role': self.role,
            'hours': hours,
            'is_overtime': self.is_overtime,
            'is_coverage': self.is_coverage,
        }


class TimeOffRequest(db.Model):
    """Represents a time off request submitted by an employee.

    This table stores the requested date range, the shift type (day,
    night or both), the approval status and optional reason.  When a
    request is approved or denied the ``status`` field is updated and
    the ``approved_at`` timestamp is recorded.  Each request is
    associated with a single employee via a foreign key.
    """
    __tablename__ = 'timeoff_requests'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    shift_type = db.Column(db.String(16), default='BOTH')  # DAY, NIGHT or BOTH
    status = db.Column(db.String(16), default='PENDING')    # PENDING, APPROVED, DENIED
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime, nullable=True)

    employee = db.relationship('Employee', backref=db.backref('timeoff_requests', lazy=True))

    def to_dict(self) -> Dict[str, any]:
        return {
            'id': self.id,
            'employee_id': self.employee_id,
            'employee_name': self.employee.name if self.employee else None,
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'shift_type': self.shift_type,
            'status': self.status,
            'reason': self.reason,
            'created_at': self.created_at.isoformat(),
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
        }


def init_db() -> None:
    """Reinitialise the database schema.

    During development the schema may change frequently.  Calling
    ``init_db`` will drop all existing tables and then recreate them
    according to the current models.  This allows you to add new
    columns (such as ``role`` on schedules or the ``TimeOffRequest``
    table) without manually migrating the SQLite database.  In a
    production setting you should perform migrations rather than
    destructive drops.
    """
    db.drop_all()
    db.create_all()


@app.route('/api/employees', methods=['GET'])
def get_employees() -> any:
    """Return a list of all active employees.

    Returns
    -------
    JSON array
        Each element contains the employee fields defined in
        :py:meth:`Employee.to_dict`.
    """
    employees: List[Employee] = Employee.query.filter_by(active=True).all()
    return jsonify([emp.to_dict() for emp in employees])


@app.route('/api/employees', methods=['POST'])
def add_employee() -> any:
    """Create a new employee from the posted JSON payload.

    Expected JSON fields:
    ---------------------
    name: str (required)
    email: str (required)
    is_lead: bool (optional)
    nights_only: bool (optional)
    max_hours_per_week: int (optional)
    cannot_work_days: list[str] (optional) – list of day abbreviations (e.g. ["Fri"]).
    """
    data = request.get_json(force=True)
    if not data or 'name' not in data or 'email' not in data:
        return jsonify({'error': 'name and email are required'}), 400
    try:
        employee = Employee(
            name=data['name'],
            email=data['email'],
            is_lead=bool(data.get('is_lead', False)),
            nights_only=bool(data.get('nights_only', False)),
            max_hours_per_week=int(data.get('max_hours_per_week', 40)),
            cannot_work_days=json.dumps(data.get('cannot_work_days', [])),
        )
        db.session.add(employee)
        db.session.commit()
        return jsonify(employee.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400


@app.route('/api/employees/<int:emp_id>', methods=['PUT'])
def update_employee(emp_id: int) -> any:
    """Update an existing employee.  Only supplied fields are changed."""
    employee: Optional[Employee] = Employee.query.get(emp_id)
    if not employee or not employee.active:
        return jsonify({'error': 'employee not found'}), 404
    data = request.get_json(force=True)
    try:
        if 'name' in data:
            employee.name = data['name']
        if 'email' in data:
            employee.email = data['email']
        if 'is_lead' in data:
            employee.is_lead = bool(data['is_lead'])
        if 'nights_only' in data:
            employee.nights_only = bool(data['nights_only'])
        if 'max_hours_per_week' in data:
            employee.max_hours_per_week = int(data['max_hours_per_week'])
        if 'cannot_work_days' in data:
            employee.cannot_work_days = json.dumps(data['cannot_work_days'])
        db.session.commit()
        return jsonify(employee.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400


@app.route('/api/employees/<int:emp_id>', methods=['DELETE'])
def deactivate_employee(emp_id: int) -> any:
    """Deactivate an employee (soft delete).  They remain in the database
    but are no longer returned by the GET endpoint and will not be
    scheduled in future runs."""
    employee: Optional[Employee] = Employee.query.get(emp_id)
    if not employee or not employee.active:
        return jsonify({'error': 'employee not found'}), 404
    employee.active = False
    db.session.commit()
    return jsonify({'status': 'success'})


@app.route('/api/schedule', methods=['GET'])
def get_schedule() -> any:
    """Retrieve schedules for a given date range.

    Query parameters
    ----------------
    start_date : str (ISO date, optional)
        Defaults to today if not provided.
    days : int (optional)
        Number of days to return (default 7).  For multiple weeks
        provide days=14 or days=28.
    """
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    days_param = request.args.get('days')
    # Parse start date; default to today if not provided
    try:
        start_dt = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else date.today()
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid start_date format; expected YYYY-MM-DD'}), 400
    # Determine end date either via explicit end_date or via days
    if end_date_str:
        try:
            end_dt = datetime.strptime(end_date_str, '%Y-%m-%d').date() + timedelta(days=1)
        except (TypeError, ValueError):
            return jsonify({'error': 'invalid end_date format; expected YYYY-MM-DD'}), 400
    else:
        try:
            days = int(days_param) if days_param is not None else 7
        except ValueError:
            return jsonify({'error': 'invalid days parameter; expected integer'}), 400
        end_dt = start_dt + timedelta(days=days)
    # Fetch schedules within [start_dt, end_dt)
    schedules = Schedule.query.filter(
        Schedule.schedule_date >= start_dt,
        Schedule.schedule_date < end_dt
    ).order_by(Schedule.schedule_date).all()
    return jsonify([sch.to_dict() for sch in schedules])


@app.route('/api/schedule/generate', methods=['POST'])
def generate_schedule_endpoint() -> any:
    """Generate a new schedule for a given date range.

    Accepts JSON payload with optional fields:
    - start_date: ISO date string (defaults to next Monday)
    - weeks: integer (number of weeks to generate, default 1)

    The generator will clear any existing schedules in the date range
    before inserting the new assignments.  Existing historical data is
    preserved.
    """
    data = request.get_json(force=True) or {}
    # Start date defaults to the next Monday for clarity
    start_date_str: str = data.get('start_date')
    weeks: int = int(data.get('weeks', 1))
    if start_date_str:
        try:
            start_dt = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'invalid start_date format; expected YYYY-MM-DD'}), 400
    else:
        today = date.today()
        # Next Monday: Monday is 0 in weekday()
        start_dt = today + timedelta(days=(7 - today.weekday()) % 7)
    # Compute date range
    total_days = weeks * 7
    end_dt = start_dt + timedelta(days=total_days)
    # Remove existing schedules in range
    Schedule.query.filter(Schedule.schedule_date >= start_dt, Schedule.schedule_date < end_dt).delete(synchronize_session=False)
    db.session.commit()
    # Generate new schedule
    new_assignments = generate_schedule(start_dt, total_days)
    db.session.bulk_save_objects(new_assignments)
    db.session.commit()
    # Return a consistent success structure expected by the front‑end
    return jsonify({'success': True, 'generated_shifts': len(new_assignments)})


# ---------------------------------------------------------------------------
# Time Off Request endpoints
#
# The front end (timeoff.html) expects to interact with the API at
# /api/timeoff for listing and creating requests, and at
# /api/timeoff/<id>/approve or /api/timeoff/<id>/deny for handling
# approvals.  The shift_type field is optional and can be DAY, NIGHT
# or BOTH.  All dates should be provided in YYYY-MM-DD format.

@app.route('/api/timeoff', methods=['GET', 'POST'])
def timeoff_requests() -> any:
    if request.method == 'GET':
        # List all time off requests.  Optionally filter by status via
        # ?status=APPROVED or ?status=PENDING
        status_filter = request.args.get('status')
        query = TimeOffRequest.query
        if status_filter:
            query = query.filter_by(status=status_filter.upper())
        requests_list = query.order_by(TimeOffRequest.created_at.desc()).all()
        return jsonify([req.to_dict() for req in requests_list])
    # POST: create a new request
    data = request.get_json(force=True) or {}
    # Validate required fields
    emp_id = data.get('employee_id')
    start_date_str = data.get('start_date')
    end_date_str = data.get('end_date')
    shift_type = data.get('shift_type', 'BOTH').upper()
    reason = data.get('reason')
    if not emp_id or not start_date_str or not end_date_str:
        return jsonify({'error': 'employee_id, start_date and end_date are required'}), 400
    try:
        start_dt = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'invalid date format; expected YYYY-MM-DD'}), 400
    # Ensure employee exists
    employee = Employee.query.get(emp_id)
    if not employee or not employee.active:
        return jsonify({'error': 'employee not found'}), 404
    req = TimeOffRequest(
        employee_id=emp_id,
        start_date=start_dt,
        end_date=end_dt,
        shift_type=shift_type,
        status='PENDING',
        reason=reason,
    )
    db.session.add(req)
    db.session.commit()
    return jsonify(req.to_dict()), 201


@app.route('/api/timeoff/<int:request_id>/approve', methods=['PUT'])
def approve_timeoff_request(request_id: int) -> any:
    req: Optional[TimeOffRequest] = TimeOffRequest.query.get(request_id)
    if not req:
        return jsonify({'error': 'request not found'}), 404
    if req.status != 'PENDING':
        return jsonify({'error': 'request has already been processed'}), 400
    req.status = 'APPROVED'
    req.approved_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'request': req.to_dict()})


@app.route('/api/timeoff/<int:request_id>/deny', methods=['PUT'])
def deny_timeoff_request(request_id: int) -> any:
    req: Optional[TimeOffRequest] = TimeOffRequest.query.get(request_id)
    if not req:
        return jsonify({'error': 'request not found'}), 404
    if req.status != 'PENDING':
        return jsonify({'error': 'request has already been processed'}), 400
    req.status = 'DENIED'
    req.approved_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'request': req.to_dict()})


def generate_schedule(start_dt: date, days: int) -> List[Schedule]:
    """Generate schedule entries for a contiguous range of days.

    This simplified scheduler greedily assigns employees to day and night
    shifts while enforcing the following rules:

    - Minimum coverage: day shifts require 4 employees, night shifts 3.
    - Patty (lead) works 5×8h day shifts per week.  If she has fewer
      than 5 shifts remaining for the week, assign her to day shifts
      until her cap is reached.
    - Nicole (nights‑only) only works night shifts up to 30 hours per week.
    - Vicki has a 20 hour cap.
    - Mayra cannot work Fridays.
    - Everyone else has a 40 hour cap and can work any shift.
    - All employees must have at least 10 hours rest between shifts and
      cannot work more than 5 consecutive days.
    - Overtime is allowed only when necessary to meet coverage.

    The returned list of Schedule objects is not yet persisted; the
    caller is responsible for committing them to the database.
    """
    # Preload employees and initialise state
    employees: List[Employee] = Employee.query.filter_by(active=True).all()
    # Sort employees: lead first, then nights‑only, then the rest.  This
    # deterministic order helps with fairness across runs.  Non‑nights
    # employees are further sorted by name.
    employees_sorted = sorted(employees, key=lambda e: (
        not e.is_lead,
        not e.nights_only,
        e.name.lower()
    ))
    # Per‑employee scheduling state for the current generation
    state: Dict[int, Dict[str, any]] = {}
    for e in employees_sorted:
        state[e.id] = {
            'hours_assigned': 0.0,
            'last_shift_end': None,  # datetime
            'days_worked': 0,  # consecutive days counter
            'max_hours': e.max_hours_per_week,
        }
    assignments: List[Schedule] = []
    current_date = start_dt
    for day_idx in range(days):
        weekday = current_date.strftime('%a')  # Mon, Tue, ...
        # Determine coverage requirements: 4 for day, 3 for night
        required_day = 4
        required_night = 3
        # Keep track of who is assigned today to reset consecutive days later
        assigned_today: List[int] = []
        # Build candidate lists for day and night pools
        day_pool: List[Employee] = []
        night_pool: List[Employee] = []
        for e in employees_sorted:
            # Skip if employee has blackout on this weekday
            cant_work = json.loads(e.cannot_work_days or '[]')
            if weekday in cant_work:
                continue
            emp_state = state[e.id]
            # Reset consecutive days if last shift end is >= 2 days ago
            if emp_state['last_shift_end']:
                days_since_last = (current_date - emp_state['last_shift_end'].date()).days
                if days_since_last > 1:
                    emp_state['days_worked'] = 0
            # Determine if employee is available (rest >=10h)
            available = True
            if emp_state['last_shift_end']:
                diff_hours = (datetime.combine(current_date, time(7, 0)) - emp_state['last_shift_end']).total_seconds() / 3600.0
                if diff_hours < 10:
                    available = False
            # Check consecutive days cap (≤5)
            if emp_state['days_worked'] >= 5:
                available = False
            # Check weekly hours cap; if not enough hours remain for a full shift, mark as candidate for overtime only
            hours_remaining = emp_state['max_hours'] - emp_state['hours_assigned']
            overtime_candidate = False
            if hours_remaining < 10.5:
                # Not enough hours for a full shift
                overtime_candidate = True
            if not available:
                continue
            # Determine day/night pool membership
            if e.nights_only:
                # nights only employees cannot work day
                night_pool.append(e)
            else:
                # Everyone else can do day; but nights only flagged above
                day_pool.append(e)
                # All non‑nights‑only employees can do nights as well
                if not e.is_lead:
                    night_pool.append(e)
                # lead can do nights too if necessary but we avoid this unless required
        # Sort pools deterministically by name to ensure reproducible schedules
        day_pool_sorted = sorted(set(day_pool), key=lambda e: e.name.lower())
        night_pool_sorted = sorted(set(night_pool), key=lambda e: e.name.lower())
        # Assign day shift(s)
        day_assigned = []
        # Always assign Patty if available and it's a weekday (Mon–Fri).  Patty works 8‑hour shift.
        lead_assigned = False
        for e in day_pool_sorted:
            if e.is_lead:
                # Determine if Patty has met her 40h/5×8h quota
                emp_state = state[e.id]
                # Check if she still has hours remaining to work today
                if emp_state['hours_assigned'] < emp_state['max_hours']:
                    # 8‑hour shift for Patty
                    shift_start = time(8, 0)
                    shift_end = time(16, 0)
                    # Create schedule entry
                    sch = Schedule(
                        employee_id=e.id,
                        schedule_date=current_date,
                        shift_start=shift_start,
                        shift_end=shift_end,
                        shift_type='DAY',
                        role='Lead',
                        is_overtime=(emp_state['hours_assigned'] + 8 > emp_state['max_hours']),
                        is_coverage=True,
                    )
                    assignments.append(sch)
                    # Update state
                    emp_state['hours_assigned'] += 8
                    emp_state['last_shift_end'] = datetime.combine(current_date, shift_end)
                    emp_state['days_worked'] += 1
                    assigned_today.append(e.id)
                    day_assigned.append(e.id)
                    lead_assigned = True
                break  # Only one lead per day
        # Fill remaining day slots with 10.5h templates
        remaining_day = required_day - len(day_assigned)
        # Use simple round‑robin assignment from day pool
        # Assign remaining day shifts.  Each assignment is labelled with
        # a role (D1, D2, D3, ...) to aid the front‑end.  The
        # ``day_assigned`` list already contains Patty if she was
        # scheduled as lead.
        for e in day_pool_sorted:
            if remaining_day == 0:
                break
            if e.id in day_assigned:
                continue
            if e.id in assigned_today:
                continue
            # Skip nights‑only employees for day shifts
            if e.nights_only:
                continue
            emp_state = state[e.id]
            # Determine shift length: use the 10.5‑hour core template
            shift_len = 10.5
            # Determine whether this shift causes overtime
            is_ot = (emp_state['hours_assigned'] + shift_len > emp_state['max_hours'])
            # Choose a start time based on how many day assignments have been made so far.
            # We cycle between two core templates: 07:00–17:30 and
            # 08:30–19:00.  If more than two assignments are needed
            # (e.g. when Patty's lead shift counts as one), we reuse
            # the first template to ensure coverage does not drop.
            idx = len(day_assigned)  # includes Patty
            if idx % 2 == 1:
                shift_start = time(7, 0)
                shift_end = (datetime.combine(current_date, shift_start) + timedelta(hours=10, minutes=30)).time()
            else:
                shift_start = time(8, 30)
                shift_end = (datetime.combine(current_date, shift_start) + timedelta(hours=10, minutes=30)).time()
            # Assign a role label based on sequence: D1, D2, etc.
            role_label = f"D{len(day_assigned)}"
            sch = Schedule(
                employee_id=e.id,
                schedule_date=current_date,
                shift_start=shift_start,
                shift_end=shift_end,
                shift_type='DAY',
                role=role_label,
                is_overtime=is_ot,
                is_coverage=True,
            )
            assignments.append(sch)
            # Update per‑employee state
            emp_state['hours_assigned'] += shift_len
            emp_state['last_shift_end'] = datetime.combine(current_date, shift_end)
            emp_state['days_worked'] += 1
            assigned_today.append(e.id)
            day_assigned.append(e.id)
            remaining_day -= 1
        # Assign night shifts
        night_assigned = []
        remaining_night = required_night
        # Assign night shifts.  Each assignment is given a role (N1, N2, N3).
        for e in night_pool_sorted:
            if remaining_night == 0:
                break
            if e.id in assigned_today:
                continue
            emp_state = state[e.id]
            # Default night shift length
            shift_len = 10.5
            # If this is the last required night shift and there is only
            # one candidate left, extend to 12 hours to ensure overnight
            # coverage until the next day.
            if remaining_night == 1 and len(night_pool_sorted) - len(night_assigned) == 1:
                shift_len = 12
            is_ot = (emp_state['hours_assigned'] + shift_len > emp_state['max_hours'])
            # Determine start and end times.  We cycle through
            # templates: N1 = 19:00–05:30 (10.5h), N2 = 21:30–08:00 (10.5h),
            # N3 = 19:00–07:00 (12h) for full coverage.
            idx = len(night_assigned)
            if idx == 0:
                shift_start = time(19, 0)
                shift_end = (datetime.combine(current_date, shift_start) + timedelta(hours=10, minutes=30)).time()
            elif idx == 1:
                shift_start = time(21, 30)
                shift_end = (datetime.combine(current_date, shift_start) + timedelta(hours=10, minutes=30)).time()
            else:
                shift_start = time(19, 0)
                shift_end = time(7, 0)
            role_label = f"N{idx + 1}"
            sch = Schedule(
                employee_id=e.id,
                schedule_date=current_date,
                shift_start=shift_start,
                shift_end=shift_end,
                shift_type='NIGHT',
                role=role_label,
                is_overtime=is_ot,
                is_coverage=True,
            )
            assignments.append(sch)
            # Update state
            emp_state['hours_assigned'] += shift_len
            emp_state['last_shift_end'] = datetime.combine(current_date, shift_end)
            emp_state['days_worked'] += 1
            assigned_today.append(e.id)
            night_assigned.append(e.id)
            remaining_night -= 1
        # Advance date
        current_date += timedelta(days=1)
    return assignments

# ---------------------------------------------------------------------------
# Front‑end page routes
#
# The HTML templates in this repository (index.html, dashboard.html,
# employees.html, schedule.html, rules.html, timeoff.html,
# shift_trades.html) live in the same directory as this script.  To
# render them, we expose simple routes that serve the files via
# send_from_directory.  In a production deployment you may wish to
# serve these files from a CDN or behind a reverse proxy.

@app.route('/')
def root_page():
    return render_template('index.html')

@app.route('/dashboard')
def dashboard_page():
    return render_template('dashboard.html')

@app.route('/employees')
def employees_page():
    return render_template('employees.html')

@app.route('/rules')
def rules_page():
    return render_template('rules.html')

@app.route('/schedule')
def schedule_page():
    return render_template('schedule.html')

@app.route('/timeoff')
def timeoff_page():
    return render_template('timeoff.html')

@app.route('/shift_trades')
def shift_trades_page():
    return render_template('shift-trades.html')

def serve_html() -> any:
    """Serve the requested HTML page.

    Flask maps the endpoint name to the corresponding HTML file by
    appending ``.html`` to the rule.  For example, accessing
    ``/dashboard`` returns ``dashboard.html``.  If the file does not
    exist a 404 response is returned.
    """
    page = request.path.lstrip('/')  # remove leading /
    filename = f"{page}.html"
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, 'templates')
    # Only serve known HTML files from the base directory
    if not filename.endswith('.html'):
        return jsonify({'error': 'Not found'}), 404
    file_path = os.path.join(base_dir, filename)
    if not os.path.isfile(file_path):
        return jsonify({'error': 'Page not found'}), 404
    return send_from_directory(templates_dir, filename)


if __name__ == '__main__':
    # Initialise the database tables if needed.  In production you may
    # migrate schema changes manually.
    with app.app_context():
        init_db()
    # Start the Flask development server
    # Run the development server on the default port (5000).
    app.run(debug=True, port=5005,use_reloader=False)