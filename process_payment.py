import serial
import csv
import time
from datetime import datetime
import re
from database import Database

# Constants
CSV_FILE = 'plates_log.csv'
RATE_PER_HOUR = 500
EXPECTED_HEADERS = ['Plate Number', 'Action', 'Payment Status', 'Timestamp', 'Amount Due']

# Initialize serial communication
ser = serial.Serial('COM4', 9600, timeout=2)
time.sleep(2)  # Let serial port initialize

# Initialize database
db = Database()

print("Welcome to Parking Payment System 👋\n")

def is_valid_plate(plate):
    """Validates Rwanda plate number format."""
    return bool(re.fullmatch(r"RA[A-Z0-9]{5}", plate))  # RA followed by 5 alphanum chars

def read_serial_line():
    """Reads a line from the serial input."""
    while True:
        if ser.in_waiting:
            line = ser.readline().decode(errors='ignore').strip()
            print(f"[DEBUG] Raw serial input: {line}")  # Debug raw input
            return line

def parse_data(line):
    """Parses plate and balance info from serial data with flexible format."""
    try:
        # Handle both "BAL:" and "BALANCE:" formats
        if not line.startswith("PLATE:") or ("BAL:" not in line and "BALANCE:" not in line):
            raise ValueError("Invalid format. Expected 'PLATE:XXX;BAL:YYY' or 'PLATE:XXX;BALANCE:YYY'")
        
        # Split the line into parts
        parts = line.split(';')
        if len(parts) < 2:
            raise ValueError("Missing balance information")
        
        # Extract plate number
        plate_part = parts[0]
        plate = plate_part.split(':')[1].strip().upper()
        
        # Extract balance (handles both BAL: and BALANCE:)
        balance_part = parts[1]
        if 'BAL:' in balance_part:
            balance = float(balance_part.split('BAL:')[1])
        elif 'BALANCE:' in balance_part:
            balance = float(balance_part.split('BALANCE:')[1])
        else:
            raise ValueError("Could not find balance indicator")
        
        if not is_valid_plate(plate):
            raise ValueError(f"Invalid plate format: {plate}")
        
        if balance < 0:
            raise ValueError("Balance cannot be negative")

        return plate, balance
    except Exception as e:
        print(f"[ERROR] Parsing failed: {str(e)}")
        return None, None

def validate_csv_headers(header_row):
    """Validates that CSV file contains expected headers."""
    return header_row and all(field in header_row for field in EXPECTED_HEADERS)

def find_unpaid_entry(plate):
    """
    Finds the most recent unpaid entry for a plate that:
    1. Has an entry record
    2. Is unpaid
    3. Has no subsequent exit record
    Returns (entry_timestamp, amount_due) or (None, None)
    """
    try:
        # Try database first
        db.cursor.execute("""
            SELECT timestamp, amount_due 
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
        """, (plate,))
        
        result = db.cursor.fetchone()
        if result:
            return result[0], result[1]

        # Fallback to CSV if database fails
        with open(CSV_FILE, 'r') as file:
            reader = csv.DictReader(file)
            if not validate_csv_headers(reader.fieldnames):
                print(f"[ERROR] CSV file headers invalid: {reader.fieldnames}")
                return None, None

            records = list(reader)
            unpaid_entries = []
            
            for i, row in enumerate(records):
                if (row['Plate Number'].strip().upper() == plate and 
                    row['Action'] == 'entry' and 
                    row['Payment Status'] == '0'):
                    
                    # Check if there's a subsequent exit for this entry
                    has_exit = False
                    for later_row in records[i+1:]:
                        if (later_row['Plate Number'].strip().upper() == plate and 
                            later_row['Action'] == 'exit' and
                            later_row['Timestamp'] > row['Timestamp']):
                            has_exit = True
                            break
                    
                    if not has_exit:
                        unpaid_entries.append(row)

            if unpaid_entries:
                # Sort by timestamp (newest first)
                unpaid_entries.sort(
                    key=lambda x: datetime.strptime(x['Timestamp'], "%Y-%m-%d %H:%M:%S"),
                    reverse=True
                )
                entry_time = datetime.strptime(unpaid_entries[0]['Timestamp'], "%Y-%m-%d %H:%M:%S")
                amount_due = float(unpaid_entries[0].get('Amount Due', 0)) if unpaid_entries[0].get('Amount Due') else None
                
                if not amount_due:
                    # Calculate amount due if not already set
                    duration_hours = max(1, int((datetime.now() - entry_time).total_seconds() / 3600))
                    amount_due = duration_hours * RATE_PER_HOUR
                
                return entry_time, amount_due
    except FileNotFoundError:
        print(f"[ERROR] Log file '{CSV_FILE}' not found.")
    except Exception as e:
        print(f"[ERROR] Failed to lookup plate: {e}")
    
    return None, None

def mark_entry_as_paid(plate, amount_paid):
    """Marks the latest unpaid entry as paid in both database and CSV."""
    try:
        # Try database first
        success = db.update_payment(plate, amount_paid)
        if success:
            print("[SUCCESS] Database payment status updated")
            return True

        # Fallback to CSV if database fails
        with open(CSV_FILE, 'r') as file:
            reader = csv.reader(file)
            rows = list(reader)
    except FileNotFoundError:
        print(f"[ERROR] CSV file '{CSV_FILE}' not found.")
        return False
    except Exception as e:
        print(f"[ERROR] Failed to read CSV: {e}")
        return False

    if not rows or len(rows[0]) < 4:
        print("[ERROR] CSV header missing or incomplete.")
        return False

    header = rows[0]
    try:
        plate_index = header.index("Plate Number")
        action_index = header.index("Action")
        payment_index = header.index("Payment Status")
        timestamp_index = header.index("Timestamp")
        amount_due_index = header.index("Amount Due")
    except ValueError:
        print("[ERROR] Required columns not found.")
        return False

    # Find all unpaid entry records without subsequent exit
    unpaid_entries = []
    for i, row in enumerate(rows[1:]):  # Skip header
        if (row[plate_index].strip().upper() == plate and 
            row[action_index] == 'entry' and 
            row[payment_index] == '0'):
            
            # Check if there's a subsequent exit
            has_exit = False
            for later_row in rows[i+2:]:  # i+2 because we skipped header
                if (later_row[plate_index].strip().upper() == plate and 
                    later_row[action_index] == 'exit' and
                    later_row[timestamp_index] > row[timestamp_index]):
                    has_exit = True
                    break
            
            if not has_exit:
                unpaid_entries.append((i+1, row))  # i+1 to account for header

    if not unpaid_entries:
        print(f"[INFO] No unpaid entries found for plate {plate}")
        return False

    # Sort by timestamp (newest first)
    unpaid_entries.sort(
        key=lambda x: datetime.strptime(x[1][timestamp_index], "%Y-%m-%d %H:%M:%S"),
        reverse=True
    )

    # Mark the newest unpaid entry as paid
    latest_index = unpaid_entries[0][0]
    rows[latest_index][payment_index] = '1'  # Mark as paid
    rows[latest_index][amount_due_index] = str(amount_paid)

    try:
        with open(CSV_FILE, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerows(rows)
        print("[SUCCESS] CSV payment status updated")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to update CSV: {e}")
        return False

# === MAIN LOOP ===
while True:
    line = read_serial_line()

    if "PLATE:" in line:
        print(f"\n[RECEIVED] {line}")
        plate, balance = parse_data(line)

        if not plate or balance is None:
            print("[ERROR] Invalid data. Skipping entry.")
            ser.write("ERROR:INVALID_DATA\n".encode())
            continue

        # Find the most recent unpaid entry without exit
        entry_time, amount_due = find_unpaid_entry(plate)
        
        if not entry_time:
            print(f"[INFO] No unpaid record found for plate: {plate}")
            ser.write("ERROR:NO_ENTRY\n".encode())
            continue

        print(f"\n[INFO] Card Info:")
        print(f"Plate Number: {plate}")
        print(f"Current Balance: {balance:.2f} RWF")
        print(f"[INFO] Entry Time: {entry_time}")
        print(f"[INFO] Amount Due: {amount_due:.2f} RWF")

        if balance < amount_due:
            print("[ERROR] Insufficient balance. Recharge required.")
            ser.write("ERROR:INSUFFICIENT\n".encode())
            continue

        new_balance = balance - amount_due
        print(f"[INFO] Deducting {amount_due:.2f} RWF. New Balance: {new_balance:.2f} RWF")

        # Send payment command with proper formatting
        payment_command = f"PAY:{amount_due:.2f}\n".encode()
        print(f"[DEBUG] Sending payment command: {payment_command.decode().strip()}")
        ser.write(payment_command)
        ser.flush()  # Ensure data is sent immediately

        # Await response with timeout
        start_time = time.time()
        response = None
        while time.time() - start_time < 5:  # 5 second timeout
            if ser.in_waiting:
                response = ser.readline().decode().strip()
                break
            time.sleep(0.1)

        if not response:
            print("[WARNING] No response from device within timeout period")
            response = "TIMEOUT"

        print(f"[DEBUG] Device response: {response}")

        if response == "DONE":
            if mark_entry_as_paid(plate, amount_due):
                print(f"\n[SUCCESS] Payment of {amount_due:.2f} RWF successful for {plate}")
                print(f"[INFO] Remaining Balance: {new_balance:.2f} RWF")
                ser.write("SUCCESS\n".encode())
            else:
                print("[ERROR] Failed to update payment status in log")
                ser.write("ERROR:DB_UPDATE\n".encode())
        elif "INSUFFICIENT" in response:
            print("[FAILED] Device reported insufficient balance.")
        elif "Invalid" in response:
            print("[ERROR] Device rejected the payment amount format")
            print("[DEBUG] Ensure Arduino expects decimal amounts with exactly 2 decimal places")
        else:
            print(f"[WARNING] Unexpected response from device: {response}")