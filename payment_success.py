import csv
import os
from datetime import datetime
from database import Database

# Constants
CSV_FILE = 'plates_log.csv'
RATE_PER_HOUR = 500  # Parking rate in RWF per hour
EXPECTED_HEADERS = ['Plate Number', 'Action', 'Payment Status', 'Timestamp', 'Amount Due']

# Initialize database
db = Database()

def validate_csv_headers(header_row):
    """Validates that CSV file contains expected headers."""
    return header_row and all(field in header_row for field in EXPECTED_HEADERS)

def calculate_payment_amount(entry_time):
    """Calculate payment amount based on entry time."""
    current_time = datetime.now()
    duration = current_time - entry_time
    hours = max(1, int(duration.total_seconds() / 3600))  # Minimum 1 hour
    return hours * RATE_PER_HOUR

def get_unpaid_entry(plate_number):
    """Get the most recent unpaid entry from both database and CSV."""
    try:
        # Try database first
        db.ensure_connection()
        db.cursor.execute("""
            SELECT id, timestamp, amount_due 
            FROM vehicles 
            WHERE plate_number = %s 
            AND action = 'entry' 
            AND payment_status = FALSE
            AND NOT EXISTS (
                SELECT 1 FROM vehicles v2
                WHERE v2.plate_number = vehicles.plate_number
                AND v2.action = 'exit'
                AND v2.timestamp > vehicles.timestamp
            )
            ORDER BY timestamp DESC LIMIT 1
        """, (plate_number,))
        
        result = db.cursor.fetchone()
        if result:
            return {
                'source': 'database',
                'id': result[0],
                'timestamp': result[1],
                'plate_number': plate_number,
                'amount_due': result[2]
            }

        # If not found in database, try CSV
        with open(CSV_FILE, 'r') as file:
            reader = csv.reader(file)
            rows = list(reader)

        if not rows or len(rows[0]) < 4:
            return None

        header = rows[0]
        if not validate_csv_headers(header):
            return None

        plate_index = header.index("Plate Number")
        action_index = header.index("Action")
        payment_index = header.index("Payment Status")
        timestamp_index = header.index("Timestamp")
        amount_due_index = header.index("Amount Due")

        unpaid_entries = []
        for i, row in enumerate(rows[1:]):
            if (row[plate_index].strip().upper() == plate_number and 
                row[action_index] == 'entry' and 
                row[payment_index] == '0'):
                
                has_exit = False
                for later_row in rows[i+2:]:
                    if (later_row[plate_index].strip().upper() == plate_number and 
                        later_row[action_index] == 'exit' and
                        later_row[timestamp_index] > row[timestamp_index]):
                        has_exit = True
                        break
                
                if not has_exit:
                    unpaid_entries.append((i+1, row))

        if unpaid_entries:
            unpaid_entries.sort(
                key=lambda x: datetime.strptime(x[1][timestamp_index], "%Y-%m-%d %H:%M:%S"),
                reverse=True
            )
            latest_entry = unpaid_entries[0]
            return {
                'source': 'csv',
                'index': latest_entry[0],
                'timestamp': datetime.strptime(latest_entry[1][timestamp_index], "%Y-%m-%d %H:%M:%S"),
                'plate_number': latest_entry[1][plate_index],
                'amount_due': float(latest_entry[1][amount_due_index]) if latest_entry[1][amount_due_index] else None
            }
    except Exception as e:
        print(f"[WARNING] Error getting unpaid entry: {e}")

    return None

def update_database_payment(record_id, amount_paid):
    """Updates payment status in MySQL database."""
    try:
        db.ensure_connection()
        db.cursor.execute("""
            UPDATE vehicles 
            SET payment_status = TRUE,
                amount_due = %s
            WHERE id = %s
        """, (amount_paid, record_id))
        
        db.conn.commit()
        print(f"[SUCCESS] Database payment status updated")
        return True
    except Exception as e:
        print(f"[ERROR] Database error: {e}")
        if db.conn:
            db.conn.rollback()
        return False

def update_csv_payment(row_index, amount_paid):
    """Updates payment status in CSV file."""
    try:
        with open(CSV_FILE, 'r') as file:
            reader = csv.reader(file)
            rows = list(reader)

        header = rows[0]
        payment_index = header.index("Payment Status")
        amount_due_index = header.index("Amount Due")

        # Update payment status and amount
        rows[row_index][payment_index] = '1'  # Mark as paid
        rows[row_index][amount_due_index] = str(amount_paid)

        with open(CSV_FILE, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerows(rows)
        
        print(f"[SUCCESS] CSV payment status updated")
        return True
    except Exception as e:
        print(f"[ERROR] CSV update error: {e}")
        return False

def process_payment(plate_number, amount_paid):
    """
    Process payment for a vehicle:
    1. Find unpaid entry
    2. Update payment status in database/CSV
    """
    # Get unpaid entry
    entry_record = get_unpaid_entry(plate_number)
    if not entry_record:
        print(f"[ERROR] No unpaid entry found for plate {plate_number}")
        return False

    # Calculate amount due if not set
    if entry_record['amount_due'] is None:
        entry_record['amount_due'] = calculate_payment_amount(entry_record['timestamp'])

    # Update payment status
    if entry_record['source'] == 'database':
        success = update_database_payment(entry_record['id'], amount_paid)
    else:
        success = update_csv_payment(entry_record['index'], amount_paid)

    if success:
        print(f"\n[SUCCESS] Payment of {amount_paid:.2f} RWF successful for {plate_number}")
        return True
    else:
        print("[ERROR] Failed to update payment status")
        return False

# ==== TESTING USAGE ====
if __name__ == "__main__":
    plate = input("Enter plate number: ").strip().upper()
    amount = float(input("Enter amount paid: ").strip())
    success = process_payment(plate, amount)
    if success:
        print("Payment processed successfully!")
    else:
        print("Payment processing failed!")