import re
import cv2
import time
import os
import csv
import serial
from collections import Counter
from ultralytics import YOLO
import pytesseract
import serial.tools.list_ports
from datetime import datetime
from database import Database

# Constants
MODEL_PATH = './best.pt'
CSV_FILE = 'plates_log.csv'
PLATE_LENGTH = 7
VALID_PREFIX = 'RA'
RATE_PER_HOUR = 500
DEBUG_IMAGE_DIR = 'debug_plates'
os.makedirs(DEBUG_IMAGE_DIR, exist_ok=True)

# Initialize database connection
try:
    db = Database()
    if not db.conn or not db.cursor:
        print("[ERROR] Failed to initialize database connection")
        db = None
    else:
        print("[DATABASE] Connection initialized successfully")
except Exception as e:
    print(f"[ERROR] Database initialization failed: {e}")
    db = None

def detect_arduino_port():
    """Detect Arduino port with better error handling"""
    ports = list(serial.tools.list_ports.comports())
    for port in ports:
        if "Arduino" in port.description or "USB-SERIAL" in port.description:
            return port.device
    for port_name in ['COM3', 'COM4', 'COM5', 'COM6']:
        try:
            test_serial = serial.Serial(port_name, 9600, timeout=1)
            test_serial.close()
            return port_name
        except:
            continue
    return None

def connect_arduino(max_retries=3):
    """Connect to Arduino with retry logic"""
    arduino_port = detect_arduino_port()
    if not arduino_port:
        print("[ERROR] No Arduino port detected.")
        return None
    for attempt in range(max_retries):
        try:
            print(f"[CONNECTING] Attempting to connect to Arduino on {arduino_port} (attempt {attempt + 1})")
            arduino = serial.Serial(arduino_port, 9600, timeout=1)
            time.sleep(2)
            print(f"[CONNECTED] Arduino successfully connected on {arduino_port}")
            return arduino
        except serial.SerialException as e:
            print(f"[ERROR] Connection attempt {attempt + 1} failed: {e}")
            if "Access is denied" in str(e):
                print("[HELP] Port may be in use. Try:")
                print("- Close Arduino IDE")
                print("- Unplug and replug Arduino")
                print("- Check Device Manager for correct COM port")
            time.sleep(1)
    print("[ERROR] Failed to connect to Arduino after all attempts.")
    return None

def read_distance(arduino):
    """Read distance from Arduino with proper error handling"""
    if not arduino:
        return 150
    try:
        if arduino.in_waiting > 0:
            val = arduino.readline().decode('utf-8').strip()
            if val:
                distance = float(val)
                if 0 <= distance <= 400:
                    return distance
                else:
                    print(f"[WARNING] Invalid distance reading: {distance}")
                    return 150
            else:
                return 150
        else:
            return 150
    except (UnicodeDecodeError, ValueError, serial.SerialException) as e:
        print(f"[ERROR] Reading distance: {e}")
        return 150
    except Exception as e:
        print(f"[UNEXPECTED ERROR] {e}")
        return 150

def extract_plate_text(plate_img):
    """OCR with enhanced debugging"""
    try:
        timestamp = int(time.time())
        debug_path = os.path.join(DEBUG_IMAGE_DIR, f"plate_{timestamp}.jpg")
        cv2.imwrite(debug_path, plate_img)
        gray = cv2.cvtColor(plate_img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(blur)
        thresh = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        configs = [
            '--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
            '--psm 7 --oem 3',
            '--psm 10 --oem 3'
        ]
        for config in configs:
            text = pytesseract.image_to_string(thresh, config=config).strip().replace(" ", "")
            if len(text) >= PLATE_LENGTH and "RA" in text:
                print(f"[OCR] Config {config}: {text}")
                return text, thresh
        return "", thresh
    except Exception as e:
        print(f"[ERROR] OCR failed: {e}")
        return "", None

def validate_plate(text):
    """Improved plate validation for Rwanda formats"""
    if not text or "RA" not in text:
        return None
    idx = text.find("RA")
    candidate = text[idx:idx + PLATE_LENGTH]
    if len(candidate) != PLATE_LENGTH:
        return None
    if not re.fullmatch(r'RA[A-Z0-9]{5}', candidate):
        return None
    if sum(c.isdigit() for c in candidate[2:]) < 2:
        return None
    print(f"[VALIDATION] Valid plate: {candidate}")
    return candidate

def check_exit_requirements(plate_number):
    """Check exit requirements using database first, then CSV as fallback"""
    try:
        if db and db.conn and db.conn.is_connected():
            db.ensure_connection()
            db.cursor.execute("""
                SELECT timestamp, payment_status
                FROM vehicles 
                WHERE plate_number = %s 
                AND action = 'entry'
                AND payment_status = TRUE
                AND NOT EXISTS (
                    SELECT 1 FROM vehicles v2
                    WHERE v2.plate_number = vehicles.plate_number
                    AND v2.action = 'exit'
                    AND v2.timestamp > vehicles.timestamp
                )
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (plate_number,))
            result = db.cursor.fetchone()
            if result:
                entry_time, payment_status = result
                print(f"[DATABASE] Found valid paid entry for {plate_number} at {entry_time}")
                return (True, entry_time)
            print(f"[DATABASE] No valid paid entry for {plate_number}")
            return (False, None)
        else:
            print("[WARNING] Database not available, falling back to CSV check")
            return check_exit_requirements_csv(plate_number)
    except Exception as e:
        print(f"[ERROR] Database check failed: {e}")
        return check_exit_requirements_csv(plate_number)

def check_exit_requirements_csv(plate_number):
    """Fallback to CSV check when database is unavailable"""
    try:
        if os.path.exists(CSV_FILE):
            with open(CSV_FILE, 'r') as f:
                reader = csv.DictReader(f)
                records = list(reader)
                for i, record in enumerate(reversed(records)):
                    if (record['Plate Number'] == plate_number and 
                        record['Action'] == 'entry' and 
                        record.get('Payment Status', '0') == '1'):
                        has_exit = any(
                            r['Action'] == 'exit' and r['Timestamp'] > record['Timestamp']
                            for r in records[len(records)-i:]
                            if r['Plate Number'] == plate_number
                        )
                        if not has_exit:
                            print(f"[CSV] Found valid entry at {record['Timestamp']}")
                            return (True, record['Timestamp'])
        print(f"[CSV] No valid entry for {plate_number}")
        return (False, None)
    except Exception as e:
        print(f"[ERROR] CSV check failed: {e}")
        return (False, None)

def control_gate(arduino, action):
    """Control gate using numeric codes: 1=open, 0=close, 2=buzzer"""
    if not arduino:
        print(f"[SIMULATION] Would send gate command: {action}")
        return True
    try:
        command_map = {
            'open': b'1',
            'close': b'0',
            'buzzer': b'2'
        }
        if action in command_map:
            print(f"[GATE] Sending {action.upper()} command (code {command_map[action].decode()})")
            arduino.write(command_map[action])
            arduino.flush()
            time.sleep(0.1)
            if arduino.in_waiting:
                response = arduino.readline().decode().strip()
                print(f"[ARDUINO] Response: {response}")
            else:
                print("[ARDUINO] No response")
            if action == 'open':
                time.sleep(15)
                arduino.write(b'0')
                print("[GATE] Sent CLOSE command after delay")
            return True
        else:
            print(f"[ERROR] Unknown gate command: {action}")
            return False
    except Exception as e:
        print(f"[ERROR] Gate control failed: {e}")
        return False

def process_exit(plate_number, arduino):
    """Complete exit processing using database primarily, with CSV as fallback"""
    print(f"[PROCESSING] Handling exit for {plate_number}")
    exit_time = datetime.now()
    
    try:
        if db and db.conn and db.conn.is_connected():
            db.ensure_connection()
            # Check for paid entry
            db.cursor.execute("""
                SELECT id, timestamp, payment_status, amount_due 
                FROM vehicles 
                WHERE plate_number = %s 
                AND action = 'entry'
                AND payment_status = TRUE
                AND NOT EXISTS (
                    SELECT 1 FROM vehicles v2
                    WHERE v2.plate_number = vehicles.plate_number
                    AND v2.action = 'exit'
                    AND v2.timestamp > vehicles.timestamp
                )
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (plate_number,))
            result = db.cursor.fetchone()
            
            if result:
                entry_id, entry_time, payment_status, amount_due = result
                print(f"[DATABASE] Found paid entry for {plate_number} at {entry_time}")
                
                # Log exit in vehicles table
                db.cursor.execute("""
                    INSERT INTO vehicles (plate_number, action, timestamp, payment_status, amount_due)
                    VALUES (%s, %s, %s, %s, %s)
                """, (plate_number, 'exit', exit_time, payment_status, amount_due))
                
                # Log in parking_logs table
                db.cursor.execute("""
                    INSERT INTO parking_logs (plate_number, action, timestamp)
                    VALUES (%s, %s, %s)
                """, (plate_number, 'exit', exit_time))
                
                db.conn.commit()
                print(f"[DATABASE] Exit logged for {plate_number}")
                
                # Fallback CSV logging
                try:
                    with open(CSV_FILE, 'a', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow([
                            plate_number, 
                            'exit', 
                            '1', 
                            exit_time.strftime('%Y-%m-%d %H:%M:%S'), 
                            amount_due
                        ])
                    print(f"[CSV] Exit logged for {plate_number}")
                except Exception as e:
                    print(f"[ERROR] CSV logging failed: {e}")
                
                # Control gate
                control_gate(arduino, 'open')
                return True
            
            # Check for unpaid entry
            db.cursor.execute("""
                SELECT id, timestamp 
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
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (plate_number,))
            unpaid_result = db.cursor.fetchone()
            
            if unpaid_result:
                # Log unauthorized exit in vehicles table
                db.cursor.execute("""
                    INSERT INTO vehicles (plate_number, action, timestamp, payment_status)
                    VALUES (%s, %s, %s, %s)
                """, (plate_number, 'unauthorized_exit', exit_time, False))
                
                # Log in unauthorized_exits table
                db.cursor.execute("""
                    INSERT INTO unauthorized_exits (plate_number, timestamp)
                    VALUES (%s, %s)
                """, (plate_number, exit_time))
                
                # Log in parking_logs table
                db.cursor.execute("""
                    INSERT INTO parking_logs (plate_number, action, timestamp)
                    VALUES (%s, %s, %s)
                """, (plate_number, 'unauthorized_exit', exit_time))
                
                db.conn.commit()
                print(f"[DATABASE] Unauthorized exit logged for {plate_number}")
                
                # Fallback CSV logging for unauthorized exit
                try:
                    with open(CSV_FILE, 'a', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow([
                            plate_number, 
                            'unauthorized_exit', 
                            '0', 
                            exit_time.strftime('%Y-%m-%d %H:%M:%S'), 
                            '0.00'
                        ])
                    print(f"[CSV] Unauthorized exit logged for {plate_number}")
                except Exception as e:
                    print(f"[ERROR] CSV logging failed: {e}")
                
                # Buzz for 3 seconds
                for _ in range(3):
                    control_gate(arduino, 'buzzer')
                    time.sleep(1)
                return False
            
            print(f"[DATABASE] No valid entry found for {plate_number}")
            return False
        
        else:
            print("[WARNING] Database not available, using CSV")
            # Fallback to CSV processing
            with open(CSV_FILE, 'r') as f:
                records = list(csv.DictReader(f))
                paid_entry = next((
                    r for r in reversed(records)
                    if (r['Plate Number'] == plate_number and 
                        r['Action'] == 'entry' and 
                        r.get('Payment Status', '0') == '1' and
                        not any(
                            e['Action'] == 'exit' and e['Timestamp'] > r['Timestamp']
                            for e in records[records.index(r)+1:]
                            if e['Plate Number'] == plate_number
                        ))
                ), None)
                
                if paid_entry:
                    try:
                        with open(CSV_FILE, 'a', newline='') as f:
                            writer = csv.writer(f)
                            writer.writerow([
                                plate_number, 
                                'exit', 
                                '1', 
                                exit_time.strftime('%Y-%m-%d %H:%M:%S'), 
                                paid_entry.get('Amount Due', '0')
                            ])
                        print(f"[CSV] Exit logged for {plate_number}")
                        control_gate(arduino, 'open')
                        return True
                    except Exception as e:
                        print(f"[ERROR] CSV logging failed: {e}")
                        return False
                
                unpaid_entry = next((
                    r for r in reversed(records)
                    if (r['Plate Number'] == plate_number and 
                        r['Action'] == 'entry' and 
                        r.get('Payment Status', '0') == '0' and
                        not any(
                            e['Action'] == 'exit' and e['Timestamp'] > r['Timestamp']
                            for e in records[records.index(r)+1:]
                            if e['Plate Number'] == plate_number
                        ))
                ), None)
                
                if unpaid_entry:
                    try:
                        with open(CSV_FILE, 'a', newline='') as f:
                            writer = csv.writer(f)
                            writer.writerow([
                                plate_number, 
                                'unauthorized_exit', 
                                '0', 
                                exit_time.strftime('%Y-%m-%d %H:%M:%S'), 
                                '0.00'
                            ])
                        print(f"[CSV] Unauthorized exit logged for {plate_number}")
                        for _ in range(3):
                            control_gate(arduino, 'buzzer')
                            time.sleep(1)
                        return False
                    except Exception as e:
                        print(f"[ERROR] CSV logging failed: {e}")
                        return False
                
                print(f"[CSV] No valid entry found for {plate_number}")
                return False
                
    except Exception as e:
        print(f"[ERROR] Exit processing failed: {e}")
        if db and db.conn and db.conn.is_connected():
            db.conn.rollback()
        return False

def main():
    """Main execution with enhanced error handling"""
    arduino = connect_arduino()
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] Camera initialization failed")
        return
    try:
        model = YOLO(MODEL_PATH)
    except Exception as e:
        print(f"[ERROR] Model loading failed: {e}")
        cap.release()
        return
    plate_buffer = []
    last_processed = None
    last_time = 0
    cooldown = 5
    print("[SYSTEM] Ready for vehicle exits. Press 'q' to quit.")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[ERROR] Frame capture failed")
                break
            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("[SYSTEM] Shutting down...")
                break
            distance = read_distance(arduino)
            if distance is None or distance > 50:
                cv2.imshow("Exit Camera", frame)
                continue
            results = model(frame, verbose=False)
            annotated = frame.copy()
            for result in results:
                for box in result.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    plate_img = frame[y1:y2, x1:x2]
                    text, _ = extract_plate_text(plate_img)
                    plate = validate_plate(text)
                    if plate:
                        print(f"[DETECTION] Found plate: {plate}")
                        plate_buffer.append(plate)
                        cv2.imshow("Plate", plate_img)
                        if len(plate_buffer) >= 3:
                            plate_counter = Counter(plate_buffer)
                            common_plate, count = plate_counter.most_common(1)[0]
                            current_time = time.time()
                            if count >= 3 and (common_plate != last_processed or 
                                             (current_time - last_time) > cooldown):
                                print(f"[PROCESSING] Verifying exit for {common_plate}")
                                can_exit, _ = check_exit_requirements(common_plate)
                                if can_exit:
                                    if process_exit(common_plate, arduino):
                                        last_processed = common_plate
                                        last_time = current_time
                                else:
                                    print(f"[REJECTED] Exit not allowed for {common_plate}")
                                    control_gate(arduino, 'buzzer')
                                plate_buffer.clear()
            cv2.imshow("Exit Camera", results[0].plot() if results else frame)
    finally:
        cap.release()
        cv2.destroyAllWindows()
        if arduino:
            arduino.close()
        if db:
            db.close()
        print("[SYSTEM] Shutdown complete")

if __name__ == "__main__":
    main()