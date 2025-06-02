from flask import Flask, render_template, jsonify
import mysql.connector
from datetime import datetime, timedelta
import pandas as pd
import json

app = Flask(__name__)

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'parking_system_intelligent'
}

def get_db_connection():
    """Create database connection."""
    return mysql.connector.connect(**DB_CONFIG)

@app.route('/')
def index():
    """Render main dashboard page."""
    return render_template('dashboard.html')

@app.route('/api/parking-stats')
def parking_stats():
    """Get parking statistics for dashboard."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get current parking count
        cursor.execute("""
            SELECT COUNT(*) as current_count 
            FROM vehicles 
            WHERE action = 'entry' 
            AND payment_status = 0
        """)
        current_count = cursor.fetchone()['current_count']

        # Get today's revenue
        cursor.execute("""
            SELECT COALESCE(SUM(amount_paid), 0) as today_revenue 
            FROM vehicles 
            WHERE DATE(payment_time) = CURDATE()
        """)
        today_revenue = cursor.fetchone()['today_revenue']

        # Get unauthorized exits
        cursor.execute("""
            SELECT COUNT(*) as unauthorized_count 
            FROM vehicles 
            WHERE action = 'exit' 
            AND payment_status = 0
        """)
        unauthorized_count = cursor.fetchone()['unauthorized_count']

        return jsonify({
            'current_count': current_count,
            'today_revenue': today_revenue,
            'unauthorized_count': unauthorized_count
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/parking-trends')
def parking_trends():
    """Get parking trends for charts."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get hourly parking counts for today
        cursor.execute("""
            SELECT HOUR(entry_time) as hour, COUNT(*) as count
            FROM vehicles
            WHERE DATE(entry_time) = CURDATE()
            AND action = 'entry'
            GROUP BY HOUR(entry_time)
            ORDER BY hour
        """)
        hourly_data = cursor.fetchall()

        # Get daily revenue for the last 7 days
        cursor.execute("""
            SELECT DATE(payment_time) as date, SUM(amount_paid) as revenue
            FROM vehicles
            WHERE payment_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            GROUP BY DATE(payment_time)
            ORDER BY date
        """)
        revenue_data = cursor.fetchall()

        return jsonify({
            'hourly_data': hourly_data,
            'revenue_data': revenue_data
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/recent-activity')
def recent_activity():
    """Get recent parking activity."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get recent entries and exits
        cursor.execute("""
            SELECT 
                plate_number,
                action,
                entry_time,
                payment_status,
                amount_paid,
                payment_time
            FROM vehicles
            ORDER BY entry_time DESC
            LIMIT 10
        """)
        recent_activity = cursor.fetchall()

        # Convert datetime objects to strings
        for activity in recent_activity:
            activity['entry_time'] = activity['entry_time'].strftime('%Y-%m-%d %H:%M:%S')
            if activity['payment_time']:
                activity['payment_time'] = activity['payment_time'].strftime('%Y-%m-%d %H:%M:%S')

        return jsonify(recent_activity)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/unauthorized-exits')
def unauthorized_exits():
    """Get unauthorized exit attempts."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT 
                plate_number,
                entry_time,
                payment_status,
                amount_paid
            FROM vehicles
            WHERE action = 'exit'
            AND payment_status = 0
            ORDER BY entry_time DESC
        """)
        unauthorized_exits = cursor.fetchall()

        # Convert datetime objects to strings
        for exit in unauthorized_exits:
            exit['entry_time'] = exit['entry_time'].strftime('%Y-%m-%d %H:%M:%S')

        return jsonify(unauthorized_exits)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    app.run(debug=True) 