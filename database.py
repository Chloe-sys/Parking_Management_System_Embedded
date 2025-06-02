import mysql.connector
from mysql.connector import Error
from datetime import datetime
from config import DB_CONFIG

class Database:
    def __init__(self):
        self.conn = None
        self.cursor = None
        self.connect()

    def connect(self):
        try:
            self.conn = mysql.connector.connect(**DB_CONFIG)
            if self.conn.is_connected():
                self.cursor = self.conn.cursor()
                self.create_tables()
                print("[DATABASE] Successfully connected to MySQL database")
        except Error as e:
            print(f"[ERROR] Failed to connect to MySQL: {e}")
            print("[HELP] Please make sure MySQL is running and the database exists")
            self.conn = None
            self.cursor = None

    def ensure_connection(self):
        if not self.conn or not self.conn.is_connected():
            self.connect()
            if not self.conn or not self.conn.is_connected():
                raise Exception("Database connection failed")

    def create_tables(self):
        try:
            # Create vehicles table matching CSV structure
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS vehicles (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    plate_number VARCHAR(20) NOT NULL,
                    action ENUM('entry', 'exit') NOT NULL,
                    payment_status BOOLEAN DEFAULT FALSE,
                    timestamp DATETIME NOT NULL,
                    amount_due DECIMAL(10,2) DEFAULT 0.00,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create parking_logs table for detailed tracking
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS parking_logs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    plate_number VARCHAR(20) NOT NULL,
                    action ENUM('entry', 'exit', 'unauthorized_exit') NOT NULL,
                    timestamp DATETIME NOT NULL
                )
            """)

            # Create unauthorized_exits table
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS unauthorized_exits (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    plate_number VARCHAR(20) NOT NULL,
                    timestamp DATETIME NOT NULL
                )
            """)

            self.conn.commit()
            print("[DATABASE] Tables initialized successfully")
        except Error as e:
            print(f"[ERROR] Failed to create tables: {e}")
            if self.conn:
                self.conn.rollback()

    def log_vehicle_entry(self, plate_number):
        try:
            self.ensure_connection()
            current_time = datetime.now()
            
            # Insert entry record
            query = """
                INSERT INTO vehicles (plate_number, action, payment_status, timestamp)
                VALUES (%s, 'entry', FALSE, %s)
            """
            self.cursor.execute(query, (plate_number, current_time))
            vehicle_id = self.cursor.lastrowid

            # Log the entry in parking_logs
            self.cursor.execute("""
                INSERT INTO parking_logs (plate_number, action, timestamp)
                VALUES (%s, 'entry', %s)
            """, (plate_number, current_time))

            self.conn.commit()
            print(f"[DATABASE] Entry logged for {plate_number}")
            return vehicle_id
        except Error as e:
            print(f"[ERROR] Failed to log vehicle entry: {e}")
            if self.conn:
                self.conn.rollback()
            return None

    def log_vehicle_exit(self, plate_number):
        try:
            self.ensure_connection()
            current_time = datetime.now()
            
            # First check if vehicle has paid
            self.cursor.execute("""
                SELECT id, payment_status 
                FROM vehicles 
                WHERE plate_number = %s 
                AND action = 'entry' 
                AND payment_status = FALSE
                ORDER BY timestamp DESC LIMIT 1
            """, (plate_number,))
            
            result = self.cursor.fetchone()
            if not result:
                return False

            vehicle_id, payment_status = result

            if not payment_status:
                # Log unauthorized exit attempt
                self.cursor.execute("""
                    INSERT INTO unauthorized_exits (plate_number, timestamp)
                    VALUES (%s, %s)
                """, (plate_number, current_time))
                
                # Log in parking_logs
                self.cursor.execute("""
                    INSERT INTO parking_logs (plate_number, action, timestamp)
                    VALUES (%s, 'unauthorized_exit', %s)
                """, (plate_number, current_time))
                
                self.conn.commit()
                print(f"[DATABASE] Unauthorized exit logged for {plate_number}")
                return False

            # Log authorized exit
            self.cursor.execute("""
                INSERT INTO vehicles (plate_number, action, payment_status, timestamp)
                VALUES (%s, 'exit', TRUE, %s)
            """, (plate_number, current_time))

            # Log in parking_logs
            self.cursor.execute("""
                INSERT INTO parking_logs (plate_number, action, timestamp)
                VALUES (%s, 'exit', %s)
            """, (plate_number, current_time))

            self.conn.commit()
            print(f"[DATABASE] Exit logged for {plate_number}")
            return True
        except Error as e:
            print(f"[ERROR] Failed to log vehicle exit: {e}")
            if self.conn:
                self.conn.rollback()
            return False

    def update_payment(self, plate_number, amount):
        try:
            self.ensure_connection()
            current_time = datetime.now()
            
            # Get the vehicle entry record
            self.cursor.execute("""
                SELECT id FROM vehicles 
                WHERE plate_number = %s 
                AND action = 'entry' 
                AND payment_status = FALSE
                ORDER BY timestamp DESC LIMIT 1
            """, (plate_number,))
            
            result = self.cursor.fetchone()
            if not result:
                return False

            vehicle_id = result[0]

            # Update payment status
            self.cursor.execute("""
                UPDATE vehicles 
                SET payment_status = TRUE,
                    amount_due = %s
                WHERE id = %s
            """, (amount, vehicle_id))

            self.conn.commit()
            print(f"[DATABASE] Payment updated for {plate_number}")
            return True
        except Error as e:
            print(f"[ERROR] Failed to update payment: {e}")
            if self.conn:
                self.conn.rollback()
            return False

    def get_dashboard_data(self):
        try:
            self.ensure_connection()
            # Get current vehicles in parking
            self.cursor.execute("""
                SELECT COUNT(*) FROM vehicles 
                WHERE action = 'entry' 
                AND payment_status = FALSE
            """)
            current_vehicles = self.cursor.fetchone()[0]

            # Get today's revenue
            self.cursor.execute("""
                SELECT COALESCE(SUM(amount_due), 0) 
                FROM vehicles 
                WHERE DATE(timestamp) = CURDATE()
                AND payment_status = TRUE
            """)
            today_revenue = self.cursor.fetchone()[0]

            # Get unauthorized exits
            self.cursor.execute("""
                SELECT COUNT(*) FROM unauthorized_exits 
                WHERE DATE(timestamp) = CURDATE()
            """)
            unauthorized_count = self.cursor.fetchone()[0]

            # Get hourly parking counts for today
            self.cursor.execute("""
                SELECT HOUR(timestamp) as hour, COUNT(*) as count
                FROM vehicles
                WHERE DATE(timestamp) = CURDATE()
                AND action = 'entry'
                GROUP BY HOUR(timestamp)
                ORDER BY hour
            """)
            hourly_data = self.cursor.fetchall()

            # Get recent activity
            self.cursor.execute("""
                SELECT 
                    v.plate_number,
                    v.action,
                    v.timestamp,
                    v.payment_status,
                    v.amount_due
                FROM vehicles v
                ORDER BY v.timestamp DESC
                LIMIT 10
            """)
            recent_activity = self.cursor.fetchall()

            return {
                'current_vehicles': current_vehicles,
                'today_revenue': today_revenue,
                'unauthorized_count': unauthorized_count,
                'hourly_data': hourly_data,
                'recent_activity': recent_activity
            }
        except Error as e:
            print(f"[ERROR] Failed to get dashboard data: {e}")
            return None

    def close(self):
        if self.conn and self.conn.is_connected():
            self.cursor.close()
            self.conn.close()
            print("[DATABASE] Connection closed")