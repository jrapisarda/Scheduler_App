from datetime import datetime, timedelta, time
from collections import defaultdict
import json
from database import get_db_connection

class SchedulingEngine:
    def __init__(self):
        self.day_shifts = {
            'weekday': [
                ('D1', time(7, 0), time(19, 0), 12),
                ('D2', time(7, 0), time(19, 0), 12),
                ('D3', time(7, 0), time(19, 0), 12),
                ('PATTY', time(8, 0), time(16, 0), 8),
                ('EARLY1', time(7, 0), time(8, 0), 1),
                ('LATE3', time(16, 0), time(19, 0), 3)
            ],
            'weekend': [
                ('D1', time(7, 0), time(19, 0), 12),
                ('D2', time(7, 0), time(19, 0), 12),
                ('D3', time(7, 0), time(19, 0), 12),
                ('D4', time(7, 0), time(19, 0), 12)
            ]
        }
        
        self.night_shifts = [
            ('N1', time(19, 0), time(5, 30), 10.5),
            ('N2', time(21, 30), time(8, 0), 10.5),
            ('N3', time(19, 0), time(7, 0), 12)
        ]
        
    def generate_schedule(self, start_date, weeks=4):
        """Generate schedule for specified number of weeks"""
        conn = get_db_connection()
        employees = conn.execute('SELECT * FROM employees WHERE active = 1').fetchall()
        conn.close()
        
        schedule_data = []
        current_date = start_date
        
        # Initialize tracking variables
        employee_hours = defaultdict(float)
        last_shift_end = defaultdict(lambda: None)
        consecutive_days = defaultdict(int)
        
        for week in range(weeks):
            for day in range(7):
                date = current_date + timedelta(days=day)
                is_weekend = date.weekday() >= 5  # Saturday = 5, Sunday = 6
                
                # Get day shifts for this date
                day_shifts = self.day_shifts['weekend'] if is_weekend else self.day_shifts['weekday']
                
                # Assign day shifts
                available_employees = self.get_available_employees(employees, date, 'Day', last_shift_end, consecutive_days)
                day_assignments = self.assign_shifts(day_shifts, available_employees, employee_hours, date, 'Day')
                
                # Assign night shifts
                available_night_employees = self.get_available_employees(employees, date, 'Night', last_shift_end, consecutive_days)
                night_assignments = self.assign_shifts(self.night_shifts, available_night_employees, employee_hours, date, 'Night')
                
                # Save assignments and update tracking
                all_assignments = day_assignments + night_assignments
                for assignment in all_assignments:
                    schedule_data.append({
                        'employee_id': assignment['employee_id'],
                        'employee_name': assignment['employee_name'],
                        'date': date.strftime('%Y-%m-%d'),
                        'shift_type': assignment['shift_type'],
                        'role': assignment['role'],
                        'start_time': assignment['start_time'].strftime('%H:%M'),
                        'end_time': assignment['end_time'].strftime('%H:%M'),
                        'hours': assignment['hours'],
                        'is_overtime': employee_hours[assignment['employee_id']] > 40
                    })
                    
                    # Update tracking variables
                    employee_id = assignment['employee_id']
                    employee_hours[employee_id] += assignment['hours']
                    last_shift_end[employee_id] = datetime.combine(date, assignment['end_time'])
                    consecutive_days[employee_id] += 1
                    
                    # Reset consecutive days if there's a gap
                    self.update_consecutive_days(consecutive_days, employee_id, date)
                
                # Clear consecutive days for employees not working today
                self.clear_non_working_days(consecutive_days, all_assignments)
        
        return schedule_data
    
    def get_available_employees(self, employees, date, shift_type, last_shift_end, consecutive_days):
        """Get employees available for a specific shift"""
        available = []
        
        for emp in employees:
            # Check shift type restrictions
            if emp['shift_type'] == 'DAY' and shift_type == 'Night':
                continue
            if emp['shift_type'] == 'NIGHT' and shift_type == 'Day':
                continue
            
            # Check day restrictions
            if emp['cannot_work_days']:
                restricted_days = json.loads(emp['cannot_work_days'])
                if date.strftime('%a') in restricted_days:
                    continue
            
            # Check rest period
            if last_shift_end[emp['id']]:
                time_since_last = (datetime.combine(date, time(0, 0)) - last_shift_end[emp['id']]).total_seconds() / 3600
                if time_since_last < emp['min_rest_hours']:
                    continue
            
            # Check consecutive days
            if consecutive_days[emp['id']] >= emp['max_consecutive_days']:
                continue
            
            # Check special schedules
            if emp['special_schedule'] == 'LEAD' and shift_type == 'Night':
                continue
            if emp['special_schedule'] == 'LEGAL_CAP' and self.get_employee_weekly_hours(emp['id']) >= emp['hours_per_week']:
                continue
            
            available.append(emp)
        
        return available
    
    def assign_shifts(self, shifts, available_employees, employee_hours, date, shift_type):
        """Assign shifts to available employees using priority-based algorithm"""
        assignments = []
        
        # Sort employees by priority (least hours first, then by availability)
        available_employees.sort(key=lambda emp: (
            employee_hours[emp['id']],
            -emp['hours_per_week']  # Higher target hours get priority
        ))
        
        for i, (role, start_time, end_time, hours) in enumerate(shifts):
            if i < len(available_employees):
                emp = available_employees[i]
                assignments.append({
                    'employee_id': emp['id'],
                    'employee_name': emp['name'],
                    'shift_type': shift_type,
                    'role': role,
                    'start_time': start_time,
                    'end_time': end_time,
                    'hours': hours
                })
        
        return assignments
    
    def get_employee_weekly_hours(self, employee_id):
        """Calculate weekly hours for an employee"""
        # This would query the database for current week's hours
        # For now, return 0 as a placeholder
        return 0
    
    def update_consecutive_days(self, consecutive_days, employee_id, current_date):
        """Update consecutive days tracking"""
        # Reset consecutive days if there's a gap
        # This is a simplified implementation
        pass
    
    def clear_non_working_days(self, consecutive_days, assignments):
        """Reset consecutive days for employees not working today"""
        working_today = {assign['employee_id'] for assign in assignments}
        for emp_id in consecutive_days:
            if emp_id not in working_today:
                consecutive_days[emp_id] = 0

# Example usage
if __name__ == '__main__':
    engine = SchedulingEngine()
    start_date = datetime(2025, 10, 5)
    schedule = engine.generate_schedule(start_date, 1)
    
    for shift in schedule[:10]:  # Show first 10 shifts
        print(f"{shift['date']} {shift['employee_name']}: {shift['shift_type']} {shift['role']}")
        
    print(f"\nTotal shifts generated: {len(schedule)}")