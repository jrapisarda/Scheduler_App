import sqlite3
import os
from datetime import datetime

def get_db_connection():
    conn = sqlite3.connect('hospital_scheduling.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    
    # Create employees table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            shift_type TEXT NOT NULL DEFAULT 'BOTH',
            hours_per_week INTEGER DEFAULT 40,
            special_schedule TEXT,
            active BOOLEAN DEFAULT 1,
            max_consecutive_days INTEGER DEFAULT 5,
            min_rest_hours INTEGER DEFAULT 10,
            cannot_work_days TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create time_off_requests table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS time_off_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            request_date DATE NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            shift_type TEXT DEFAULT 'BOTH',
            status TEXT DEFAULT 'PENDING',
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            approved_at DATETIME,
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')
    
    # Create schedules table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            schedule_date DATE NOT NULL,
            shift_start TIME NOT NULL,
            shift_end TIME NOT NULL,
            shift_type TEXT NOT NULL,
            role TEXT,
            is_overtime BOOLEAN DEFAULT 0,
            is_coverage BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')
    
    # Create shift_trades table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS shift_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requesting_employee_id INTEGER,
            target_employee_id INTEGER,
            original_schedule_id INTEGER,
            trade_schedule_id INTEGER,
            trade_reason TEXT,
            status TEXT DEFAULT 'PENDING',
            special_form_data TEXT,
            approved_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (requesting_employee_id) REFERENCES employees (id),
            FOREIGN KEY (target_employee_id) REFERENCES employees (id),
            FOREIGN KEY (original_schedule_id) REFERENCES schedules (id),
            FOREIGN KEY (trade_schedule_id) REFERENCES schedules (id)
        )
    ''')
    
    # Insert sample employees if database is empty
    cursor = conn.execute('SELECT COUNT(*) as count FROM employees')
    if cursor.fetchone()['count'] == 0:
        sample_employees = [
            ('Patty Golden', 'patty@hospital.com', 'DAY', 60, 'LEAD'),
            ('Nicole Dempster', 'nicole@hospital.com', 'NIGHT', 30, None),
            ('Vicki Theler', 'vicki@hospital.com', 'BOTH', 20, 'LEGAL_CAP'),
            ('Mayra Bradley', 'mayra@hospital.com', 'BOTH', 40, None),
            ('Lisa Dixon', 'lisa@hospital.com', 'BOTH', 40, None),
            ('Shala Johnson', 'shala@hospital.com', 'BOTH', 40, None),
            ('Chloe Gray', 'chloe@hospital.com', 'BOTH', 40, None),
            ('Tash Jaramillo', 'tash@hospital.com', 'BOTH', 40, None),
            ('NewHire A', 'newhirea@hospital.com', 'BOTH', 40, 'NEW_HIRE'),
            ('NewHire B', 'newhireb@hospital.com', 'BOTH', 40, 'NEW_HIRE'),
            ('NewHire C', 'newhirec@hospital.com', 'BOTH', 40, 'NEW_HIRE')
        ]
        
        for emp in sample_employees:
            conn.execute('''
                INSERT INTO employees (name, email, shift_type, hours_per_week, special_schedule)
                VALUES (?, ?, ?, ?, ?)
            ''', emp)
        
        conn.commit()
    
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully!")