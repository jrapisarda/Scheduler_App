@app.route('/api/trades', methods=['GET', 'POST'])
def api_trades():
    """Shift trade API - fully implemented"""
    
    if request.method == 'GET':
        # ... KEEP THE EXISTING GET METHOD CODE AS IS - DON'T TOUCH THIS PART ...
        try:
            # Get trades with proper filtering
            status_filter = request.args.get('status')
            query = ShiftTrade.query.join(Employee, ShiftTrade.requesting_employee_id == Employee.id).filter(
                Employee.active == True
            )
            
            if status_filter:
                query = query.filter(ShiftTrade.status == status_filter)
            
            trades = query.order_by(ShiftTrade.created_at.desc()).all()
            valid_trades = []
            for trade in trades:
                original_exists = Schedule.query.get(trade.original_schedule_id) is not None
                trade_exists = Schedule.query.get(trade.trade_schedule_id) is not None
            if original_exists and trade_exists:
                valid_trades.append(trade)
            else:
                logger.warning(f"Invalid trade {trade.id}: missing schedules (original: {original_exists}, trade: {trade_exists})")
            
            return jsonify({
                'success': True,
                'trades': [trade.to_dict() for trade in trades],
                'count': len(trades)
            })
        except Exception as e:
            logger.error(f"Error fetching trades: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    # ↓↓↓ REPLACE FROM HERE DOWN TO THE END OF THE POST METHOD ↓↓↓
    elif request.method == 'POST':
        try:
            data = request.json
            logger.info(f"Creating shift trade: {data}")
            
            # Validate that both schedules exist and belong to the correct employees
            original_schedule = Schedule.query.get(data['original_schedule_id'])
            trade_schedule = Schedule.query.get(data['trade_schedule_id'])
            
            if not original_schedule:
                return jsonify({'success': False, 'error': f'Original schedule {data["original_schedule_id"]} not found'}), 400
            
            if not trade_schedule:
                return jsonify({'success': False, 'error': f'Trade schedule {data["trade_schedule_id"]} not found'}), 400
            
            if original_schedule.employee_id != data['requesting_employee_id']:
                return jsonify({'success': False, 'error': 'Original schedule does not belong to requesting employee'}), 400
            
            if trade_schedule.employee_id != data['target_employee_id']:
                return jsonify({'success': False, 'error': 'Trade schedule does not belong to target employee'}), 400
            
            # Check if schedules are in the future (can't trade past shifts)
            if original_schedule.schedule_date < datetime.now().date():
                return jsonify({'success': False, 'error': 'Cannot trade shifts that have already occurred'}), 400
            
            if trade_schedule.schedule_date < datetime.now().date():
                return jsonify({'success': False, 'error': 'Cannot trade shifts that have already occurred'}), 400
            
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