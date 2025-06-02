from flask import Flask, render_template, jsonify, request
from database import Database
from datetime import datetime
import threading
import time
from config import FLASK_CONFIG

app = Flask(__name__)
db = Database()

def update_dashboard_data():
    """Background thread to update dashboard data periodically."""
    while True:
        try:
            data = db.get_dashboard_data()
            if data:
                app.config['dashboard_data'] = data
            time.sleep(5)
        except Exception as e:
            print(f"[ERROR] Failed to update dashboard data: {e}")
            time.sleep(5)

def decode_if_bytes(value):
    """Decode bytearray or bytes to string, handle None or other types."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8', errors='ignore')
    return value if value is not None else ''

@app.route('/')
def index():
    """Render the main dashboard page."""
    return render_template('index.html')

@app.route('/api/parking-stats')
def parking_stats():
    """Get current parking statistics."""
    try:
        if not db or not db.conn or not db.conn.is_connected():
            print("[ERROR] Database or connection not initialized")
            return jsonify({
                'current_count': 0,
                'today_revenue': 0.00,
                'unauthorized_count': 0
            })

        db.ensure_connection()
        # Current vehicles count
        db.cursor.execute("""
            SELECT COUNT(*) 
            FROM vehicles 
            WHERE action = 'entry' 
            AND payment_status = FALSE
            AND NOT EXISTS (
                SELECT 1 FROM vehicles v2
                WHERE v2.plate_number = vehicles.plate_number
                AND v2.action = 'exit'
                AND v2.timestamp > vehicles.timestamp
            )
        """)
        result = db.cursor.fetchone()
        print(f"[DEBUG] Current count raw result: {result}")
        current_count = int(result[0]) if result and result[0] is not None else 0

        # Today's revenue
        db.cursor.execute("""
            SELECT COALESCE(SUM(amount_due), 0) 
            FROM vehicles 
            WHERE DATE(timestamp) = CURDATE()
            AND payment_status = TRUE
        """)
        result = db.cursor.fetchone()
        print(f"[DEBUG] Revenue raw result: {result}")
        today_revenue = float(result[0]) if result and result[0] is not None else 0.00

        # Unauthorized exits count
        db.cursor.execute("""
            SELECT COUNT(*) 
            FROM unauthorized_exits 
            WHERE DATE(timestamp) = CURDATE()
        """)
        result = db.cursor.fetchone()
        print(f"[DEBUG] Unauthorized count raw result: {result}")
        unauthorized_count = int(result[0]) if result and result[0] is not None else 0

        return jsonify({
            'current_count': current_count,
            'today_revenue': today_revenue,
            'unauthorized_count': unauthorized_count
        })
    except Exception as e:
        print(f"[ERROR] Failed to get parking stats: {e}")
        return jsonify({
            'current_count': 0,
            'today_revenue': 0.00,
            'unauthorized_count': 0
        })

@app.route('/api/parking-trends')
def parking_trends():
    """Get parking trends data for charts."""
    try:
        db.ensure_connection()
        # Hourly parking counts for today
        db.cursor.execute("""
            SELECT HOUR(timestamp) as hour, COUNT(*) as count
            FROM vehicles
            WHERE DATE(timestamp) = CURDATE()
            AND action = 'entry'
            GROUP BY HOUR(timestamp)
            ORDER BY hour
        """)
        hourly_data = []
        for row in db.cursor.fetchall():
            print(f"[DEBUG] Hourly data row: {row}")
            hour, count = row
            hourly_data.append({
                'hour': f"{int(hour):02d}:00",
                'count': int(count)
            })

        # Today's revenue
        db.cursor.execute("""
            SELECT COALESCE(SUM(amount_due), 0) 
            FROM vehicles 
            WHERE DATE(timestamp) = CURDATE()
            AND payment_status = TRUE
        """)
        result = db.cursor.fetchone()
        print(f"[DEBUG] Revenue data raw result: {result}")
        revenue_data = float(result[0]) if result and result[0] is not None else 0.00

        return jsonify({
            'hourly_data': hourly_data,
            'revenue_data': revenue_data
        })
    except Exception as e:
        print(f"[ERROR] Failed to get parking trends: {e}")
        return jsonify({
            'hourly_data': [],
            'revenue_data': 0.00
        })

@app.route('/api/recent-activity')
def recent_activity():
    """Get recent parking activity."""
    try:
        db.ensure_connection()
        db.cursor.execute("""
            SELECT 
                plate_number,
                action,
                timestamp,
                payment_status,
                COALESCE(amount_due, 0) as amount_due,
                CASE 
                    WHEN action = 'entry' THEN 'Entry'
                    WHEN action = 'exit' THEN 'Exit'
                    WHEN action = 'unauthorized_exit' THEN 'Unauthorized Exit'
                    ELSE action
                END as action_display
            FROM vehicles
            ORDER BY timestamp DESC
            LIMIT 50
        """)
        activities = []
        for activity in db.cursor.fetchall():
            print(f"[DEBUG] Recent activity row: {activity}")
            activities.append({
                'plate_number': decode_if_bytes(activity[0]),
                'action': decode_if_bytes(activity[1]),
                'action_display': decode_if_bytes(activity[5]),
                'timestamp': activity[2].strftime('%Y-%m-%d %H:%M:%S') if activity[2] else '',
                'payment_status': bool(activity[3]) if activity[3] is not None else False,
                'amount_due': float(activity[4]) if activity[4] is not None else 0.00
            })
        return jsonify(activities)
    except Exception as e:
        print(f"[ERROR] Failed to get recent activity: {e}")
        return jsonify([])

@app.route('/api/unauthorized-exits')
def unauthorized_exits():
    """Get recent unauthorized exit attempts."""
    try:
        db.ensure_connection()
        db.cursor.execute("""
            SELECT 
                ue.plate_number,
                ue.timestamp,
                COALESCE(v.amount_due, 0) as amount_due,
                COUNT(*) OVER (PARTITION BY ue.plate_number) as attempt_count
            FROM unauthorized_exits ue
            LEFT JOIN vehicles v ON ue.plate_number = v.plate_number 
            AND v.action = 'entry'
            AND v.payment_status = FALSE
            AND NOT EXISTS (
                SELECT 1 FROM vehicles v2
                WHERE v2.plate_number = v.plate_number
                AND v2.action = 'exit'
                AND v2.timestamp > v.timestamp
            )
            ORDER BY ue.timestamp DESC
            LIMIT 20
        """)
        exits = []
        for exit_record in db.cursor.fetchall():
            print(f"[DEBUG] Unauthorized exit row: {exit_record}")
            exits.append({
                'plate_number': decode_if_bytes(exit_record[0]),
                'timestamp': exit_record[1].strftime('%Y-%m-%d %H:%M:%S') if exit_record[1] else '',
                'amount_due': float(exit_record[2]) if exit_record[2] is not None else 0.00,
                'attempt_count': int(exit_record[3]) if exit_record[3] is not None else 0
            })
        return jsonify(exits)
    except Exception as e:
        print(f"[ERROR] Failed to get unauthorized exits: {e}")
        return jsonify([])

@app.route('/api/current-vehicles')
def current_vehicles():
    """Get currently parked vehicles."""
    try:
        db.ensure_connection()
        db.cursor.execute("""
            SELECT 
                plate_number,
                timestamp as entry_time,
                payment_status,
                COALESCE(amount_due, 0) as amount_due,
                TIMESTAMPDIFF(MINUTE, timestamp, NOW()) as duration_minutes
            FROM vehicles
            WHERE action = 'entry'
            AND payment_status = FALSE
            AND NOT EXISTS (
                SELECT 1 FROM vehicles v2
                WHERE v2.plate_number = vehicles.plate_number
                AND v2.action = 'exit'
                AND v2.timestamp > vehicles.timestamp
            )
            ORDER BY timestamp DESC
        """)
        vehicles = []
        for vehicle in db.cursor.fetchall():
            print(f"[DEBUG] Current vehicle row: {vehicle}")
            vehicles.append({
                'plate_number': decode_if_bytes(vehicle[0]),
                'entry_time': vehicle[1].strftime('%Y-%m-%d %H:%M:%S') if vehicle[1] else '',
                'payment_status': bool(vehicle[2]) if vehicle[2] is not None else False,
                'amount_due': float(vehicle[3]) if vehicle[3] is not None else 0.00,
                'duration_minutes': int(vehicle[4]) if vehicle[4] is not None else 0
            })
        return jsonify(vehicles)
    except Exception as e:
        print(f"[ERROR] Failed to get current vehicles: {e}")
        return jsonify([])

@app.route('/api/vehicle-entry', methods=['POST'])
def vehicle_entry():
    """Handle vehicle entry."""
    try:
        data = request.get_json()
        plate_number = data.get('plate_number')
        if not plate_number:
            return jsonify({'error': 'Plate number is required'}), 400
        vehicle_id = db.log_vehicle_entry(plate_number)
        if vehicle_id:
            return jsonify({'message': 'Vehicle entry logged successfully', 'vehicle_id': vehicle_id})
        return jsonify({'error': 'Failed to log vehicle entry'}), 500
    except Exception as e:
        print(f"[ERROR] Failed to log vehicle entry: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/vehicle-exit', methods=['POST'])
def vehicle_exit():
    """Handle vehicle exit."""
    try:
        data = request.get_json()
        plate_number = data.get('plate_number')
        if not plate_number:
            return jsonify({'error': 'Plate number is required'}), 400
        success = db.log_vehicle_exit(plate_number)
        if success:
            return jsonify({'message': 'Vehicle exit logged successfully'})
        return jsonify({'error': 'Unauthorized exit attempt'}), 403
    except Exception as e:
        print(f"[ERROR] Failed to log vehicle exit: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/process-payment', methods=['POST'])
def process_payment():
    """Process payment for a vehicle."""
    try:
        data = request.get_json()
        plate_number = data.get('plate_number')
        amount = data.get('amount')
        if not plate_number or amount is None:
            return jsonify({'error': 'Plate number and amount are required'}), 400
        success = db.update_payment(plate_number, float(amount))
        if success:
            return jsonify({'message': 'Payment processed successfully'})
        return jsonify({'error': 'Failed to process payment'}), 500
    except Exception as e:
        print(f"[ERROR] Failed to process payment: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.config['dashboard_data'] = db.get_dashboard_data()
    update_thread = threading.Thread(target=update_dashboard_data, daemon=True)
    update_thread.start()
    app.run(**FLASK_CONFIG)