import serial
import csv
import time
from datetime import datetime

CSV_FILE = 'plates_log.csv'
RATE_PER_HOUR = 200

ser = serial.Serial('/dev/cu.usbmodem1101', 9600, timeout=2) 
time.sleep(2)

print("Welcome to Parking management system👋\n")

def read_serial_line():
    while True:
        if ser.in_waiting:
            return ser.readline().decode().strip()

def parse_data(line):
    try:
        parts = line.split(';')
        plate = parts[0].split(':')[1]
        balance = float(parts[1].split(':')[1])
        return plate, balance
    except Exception as e:
        print(f"Error parsing data: {e}")
        return None, None


def lookup_plate(plate):
    with open(CSV_FILE, 'r') as f:
        reader = csv.DictReader(f)
        unpaid_entries = [
            row for row in reader
            if row['Plate Number'] == plate and row['Payment Status'] == '0'
        ]
    
    if unpaid_entries:
        # Sort by timestamp (descending) and take the most recent
        unpaid_entries.sort(key=lambda x: datetime.strptime(x['Timestamp'], "%Y-%m-%d %H:%M:%S"), reverse=True)
        entry_time = datetime.strptime(unpaid_entries[0]['Timestamp'], '%Y-%m-%d %H:%M:%S')
        return entry_time
    
    return None

def update_payment_status(plate, amount_due):
    rows = []
    updated = False
    with open(CSV_FILE, 'r') as f:
        reader = csv.reader(f)
        rows = list(reader)

    header = rows[0]
    timestamp_index = header.index("Timestamp")
    
    unpaid_entries = [
        (i, row) for i, row in enumerate(rows[1:], start=1)
        if row[0] == plate and row[1] == '0'
    ]

    if unpaid_entries:
        unpaid_entries.sort(key=lambda x: datetime.strptime(x[1][timestamp_index], "%Y-%m-%d %H:%M:%S"), reverse=True)
        latest_index = unpaid_entries[0][0]
        rows[latest_index][1] = '1'  # Mark as paid
        rows[latest_index].append(str(amount_due))
        updated = True

    if updated:
        with open(CSV_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(rows)

while True:
    line = read_serial_line()
    if "PLATE:" in line:
        print(f"[RECEIVED] {line}")
        plate, balance = parse_data(line)
        entry_time = lookup_plate(plate)

        if not entry_time:
            print(f"[ERROR] No unpaid entry for plate {plate}")
            continue

        print(f"[INFO] Card Details:")
        print(f"Plate Number: {plate}")
        print(f"Current Balance: {balance} RWF")
        
        duration_hours = max(1, int((datetime.now() - entry_time).total_seconds() / 3600))
        amount_due = duration_hours * RATE_PER_HOUR
        print(f"\n[INFO] Duration: {duration_hours} hours, Amount Due: {amount_due} RWF")

        if balance < amount_due:
            print(f"[ERROR] Insufficient balance to make payment. Please recharge the card.")
            ser.write(f"INSUFFICIENT\n".encode())
            continue
        
        # Reduce balance
        new_balance = balance - amount_due
        print(f"\n[INFO] Reducing balance by {amount_due} RWF. New Balance: {new_balance} RWF")

        ser.write(f"{amount_due}\n".encode())

        response = read_serial_line()
        if response == "DONE":
            update_payment_status(plate, amount_due)
            print(f"\n[SUCCESS] Payment of {amount_due} RWF processed for {plate}")
            print(f"Updated Card Details:")
            print(f"Plate Number: {plate}")
            print(f"Remaining Balance: {new_balance} RWF")
        elif response == "INSUFFICIENT":
            print(f"[FAILED] Insufficient balance on card")
