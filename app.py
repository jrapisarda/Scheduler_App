from flask import Flask, render_template, request, jsonify, redirect, url_for
import sqlite3
import json
from datetime import datetime, timedelta, time
import os
from scheduling_engine import SchedulingEngine
from database import init_db, get_db_connection

app = Flask(__name__)
app.secret_key = 'hospital-scheduling-secret-key'

# Initialize database on startup
init_db()

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/schedule')
def schedule_view():
    return render_template('schedule.html')

@app.route('/employees')
def employees():
    return render_template('employees.html')

@app.route('/timeoff')
def timeoff():
    return render_template('timeoff.html')

@app.route('/shift-trades')
def shift_trades():
    return render_template('shift_trades.html')

@app.route('/rules')
def rules():
    return render_template('rules.html')

# API Endpoints
@app.route('/api/employees', methods=['GET', 'POST'])
def api_employees():
    conn = get_db_connection()
    
    if request.method == 'GET':
        employees = conn.execute('SELECT * FROM employees WHERE active = 1').fetchall()
        conn.close()
        return jsonify([dict(emp) for emp in employees])
    
    elif request.method == 'POST':
        data = request.json
        conn.execute('''
            INSERT INTO employees (name, email, shift_type, hours_per_week, special_schedule)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            data['name'],
            data['email'],
            data['shift_type'],
            data.get('hours_per_week', 40),
            data.get('special_schedule')
        ))
        conn.commit()
        conn.close()
        return jsonify({'success': True})

@app.route('/api/schedule/generate', methods=['POST'])
def generate_schedule():
    try:
        data = request.json
        start_date = datetime.strptime(data['start_date'], '%Y-%m-%d')
        weeks = data.get('weeks', 4)
        
        engine = SchedulingEngine()
        schedule_data = engine.generate_schedule(start_date, weeks)
        
        return jsonify({
            'success': True,
            'schedule': schedule_data,
            'message': f'Schedule generated for {weeks} weeks starting {start_date.strftime("%Y-%m-%d")}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/schedule', methods=['GET'])
def get_schedule():
    conn = get_db_connection()
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = '''
        SELECT s.*, e.name as employee_name 
        FROM schedules s 
        JOIN employees e ON s.employee_id = e.id 
        WHERE 1=1
    '''
    params = []
    
    if start_date:
        query += ' AND s.schedule_date >= ?'
        params.append(start_date)
    if end_date:
        query += ' AND s.schedule_date <= ?'
        params.append(end_date)
    
    query += ' ORDER BY s.schedule_date, s.shift_start'
    
    schedules = conn.execute(query, params).fetchall()
    conn.close()
    
    return jsonify([dict(sched) for sched in schedules])

@app.route('/api/timeoff', methods=['GET', 'POST'])
def api_timeoff():
    conn = get_db_connection()
    
    if request.method == 'GET':
        requests = conn.execute('''
            SELECT t.*, e.name as employee_name 
            FROM time_off_requests t 
            JOIN employees e ON t.employee_id = e.id 
            ORDER BY t.created_at DESC
        ''').fetchall()
        conn.close()
        return jsonify([dict(req) for req in requests])
    
    elif request.method == 'POST':
        data = request.json
        conn.execute('''
            INSERT INTO time_off_requests (employee_id, request_date, start_date, end_date, shift_type, reason)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            data['employee_id'],
            datetime.now().strftime('%Y-%m-%d'),
            data['start_date'],
            data['end_date'],
            data.get('shift_type', 'BOTH'),
            data.get('reason', '')
        ))
        conn.commit()
        conn.close()
        return jsonify({'success': True})

@app.route('/api/timeoff/<int:request_id>/approve', methods=['PUT'])
def approve_timeoff(request_id):
    conn = get_db_connection()
    conn.execute('''
        UPDATE time_off_requests 
        SET status = 'APPROVED', approved_at = ? 
        WHERE id = ?
    ''', (datetime.now(), request_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/timeoff/<int:request_id>/deny', methods=['PUT'])
def deny_timeoff(request_id):
    conn = get_db_connection()
    conn.execute('''
        UPDATE time_off_requests 
        SET status = 'DENIED' 
        WHERE id = ?
    ''', (request_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5005)