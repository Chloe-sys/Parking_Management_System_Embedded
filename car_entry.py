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
import re
from database import Database

# Constants
MODEL_PATH = './best.pt'
SAVE_DIR = 'plates'
CSV_FILE = 'plates_log.csv'
ENTRY_COOLDOWN = 300  # seconds
PLATE_LENGTH = 7
VALID_PREFIX = 'RA'
DEBUG_IMAGE_DIR = 'debug_plates'

# Initialize directories and files
os.makedirs(SAVE_DIR, exist_ok=True)
os.makedirs(DEBUG_IMAGE_DIR, exist_ok=True)
if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, 'w', newline='') as f:
        csv.writer(f).writerow(['Plate Number', 'Action', 'Payment Status', 'Timestamp', 'Amount Due'])

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

# Load YOLO model
model = YOLO(MODEL_PATH)

def detect_arduino_port():
    """Detect Arduino port with better error handling"""
    ports = list(serial.tools.list_ports.comports())
    for port in ports:
        if "Arduino" in port.description or "USB-SERIAL" in port.description:
            return port.device
    # If no Arduino found, try common COM ports
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
            time.sleep(2)  # Wait for Arduino to initialize
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
        return 150  # Default safe distance when no Arduino
    
    try:
        if arduino.in_waiting > 0:
            val = arduino.readline().decode('utf-8').strip()
            if val:
                distance = float(val)
                # Validate reasonable distance range
                if 0 <= distance <= 400:  # HC-SR04 max range is ~400cm
                    return distance
                else:
                    print(f"[WARNING] Invalid distance reading: {distance}")
                    return 150
            else:
                return 150  # No data available
        else:
            return 150  # No data waiting
    except (UnicodeDecodeError, ValueError, serial.SerialException) as e:
        print(f"[ERROR] Reading distance: {e}")
        return 150
    except Exception as e:
        print(f"[UNEXPECTED ERROR] {e}")
        return 150

def extract_plate_text(plate_img):
    """OCR with enhanced debugging"""
    try:
        # Save debug image
        timestamp = int(time.time())
        debug_path = os.path.join(DEBUG_IMAGE_DIR, f"plate_{timestamp}.jpg")
        cv2.imwrite(debug_path, plate_img)
        
        # Enhanced preprocessing
        gray = cv2.cvtColor(plate_img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(blur)
        thresh = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        
        # OCR with multiple configurations
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
    """More flexible validation for Rwanda plates."""
    if not text or "RA" not in text:
        return None
    
    # Find RA prefix
    idx = text.find("RA")
    candidate = text[idx:idx + PLATE_LENGTH]
    
    if len(candidate) != PLATE_LENGTH:
        return None
    
    # Rwanda plate formats can be:
    # RA + letter + 3 digits + letter (RAH972U)
    # RA + 4 digits + letter (RA1234A)
    # RA + 3 letters + 2 digits (RAAAB12)
    
    # Basic validation rules:
    # 1. Must start with RA
    # 2. Must be 7 characters total
    # 3. Must contain only letters and digits
    # 4. Must have at least 2 digits
    
    if not re.fullmatch(r'RA[A-Z0-9]{5}', candidate):
        return None
    
    # Count digits in the plate (after RA)
    digit_count = sum(1 for c in candidate[2:] if c.isdigit())
    
    if digit_count < 2:
        return None
    
    print(f"[DEBUG] Valid plate detected: {candidate}")
    return candidate

def check_plate_status(plate_number):
    """Check if plate can enter (no unclosed entry)."""
    try:
        if not db or not db.cursor:
            print("[WARNING] Database not available, falling back to CSV check")
            return check_plate_status_csv(plate_number)

        # First check database
        db.cursor.execute("""
            SELECT action, payment_status 
            FROM vehicles 
            WHERE plate_number = %s 
            ORDER BY timestamp DESC 
            LIMIT 1
        """, (plate_number,))
        result = db.cursor.fetchone()
        
        if result:
            action, payment_status = result
            if action == 'entry' and not payment_status:
                return 'entry'
            elif action == 'exit':
                return 'exit'
        
        return check_plate_status_csv(plate_number)
    except Exception as e:
        print(f"[ERROR] Failed to check plate status: {e}")
        return check_plate_status_csv(plate_number)

def check_plate_status_csv(plate_number):
    """Fallback to CSV check when database is unavailable."""
    try:
        if os.path.exists(CSV_FILE):
            with open(CSV_FILE, 'r') as f:
                reader = csv.DictReader(f)
                records = list(reader)
                
                for record in reversed(records):
                    if record['Plate Number'] == plate_number:
                        if record['Action'] == 'entry' and record['Payment Status'] == '0':
                            return 'entry'
                        elif record['Action'] == 'exit':
                            return 'exit'
        return None
    except Exception as e:
        print(f"[ERROR] CSV check failed: {e}")
        return None

def control_gate(arduino, action):
    """Control gate using numeric codes: 1=open, 0=close, 2=buzzer."""
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

            # Read Arduino's response (optional)
            if arduino.in_waiting:
                response = arduino.readline().decode().strip()
                print(f"[ARDUINO] Response: {response}")
            else:
                print("[ARDUINO] No response")

            # Optional: close the gate after delay
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

def log_action(plate, action, arduino):
    """Log entry/exit and control gate."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    payment_status = False if action == 'entry' else True
    amount_due = 0
    
    print(f"[ACTION] Processing {action} for {plate}")
    
    try:
        # Log to database if available
        if db and db.cursor:
            try:
                if action == 'entry':
                    db.log_vehicle_entry(plate)
                else:
                    db.log_vehicle_exit(plate)
            except Exception as e:
                print(f"[ERROR] Database logging failed: {e}")
        
        # Always log to CSV for backup
        with open(CSV_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([plate, action, '0' if action == 'entry' else '1', timestamp, amount_due])
        print(f"[CSV] {action.upper()} for {plate} at {timestamp}")
    except Exception as e:
        print(f"[ERROR] Failed to log action: {e}")
        return
    
    # Control gate
    if action == 'entry':
        if control_gate(arduino, 'open'):
            time.sleep(15)  # Keep gate open for 15 seconds
            control_gate(arduino, 'close')

def main():
    arduino = connect_arduino()
    cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("[ERROR] Failed to open camera")
        return
    
    plate_buffer = []
    last_detected_plate = None
    last_action_time = 0
    cooldown = 5  # seconds between processing same plate

    print("[ENTRY SYSTEM] Active. Press 'q' to quit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] Camera frame not available.")
            break

        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("[SYSTEM] Shutting down...")
            break

        distance = read_distance(arduino)
        if distance is not None and distance <= 50:
            print(f"[DISTANCE] Car detected at {distance} cm")
            
            results = model(frame, verbose=False)
            annotated = frame.copy()
            
            for result in results:
                boxes = result.boxes
                if boxes:
                    annotated = result.plot()
                    
                    for box in boxes:
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        plate_img = frame[y1:y2, x1:x2]
                        
                        plate_text, processed = extract_plate_text(plate_img)
                        plate_number = validate_plate(plate_text)
                        
                        if plate_number:
                            print(f"[PLATE] Detected: {plate_number}")
                            plate_buffer.append(plate_number)
                            
                            if len(plate_buffer) >= 3:
                                most_common, count = Counter(plate_buffer).most_common(1)[0]
                                now = time.time()
                                
                                if count >= 3 and (most_common != last_detected_plate or (now - last_action_time) > cooldown):
                                    print(f"[PROCESSING] Plate {most_common} detected {count} times")
                                    
                                    plate_status = check_plate_status(most_common)
                                    print(f"[STATUS] Plate {most_common} status: {plate_status}")
                                    
                                    if plate_status is None or plate_status == 'exit':
                                        print(f"[ENTRY] Allowing entry for {most_common}")
                                        log_action(most_common, 'entry', arduino)
                                        last_detected_plate = most_common
                                        last_action_time = now
                                    elif plate_status == 'entry':
                                        print(f"[DENIED] {most_common} already has unclosed entry")
                                        control_gate(arduino, 'buzzer')
                                    
                                    plate_buffer.clear()

            cv2.imshow("Entry Camera", annotated)
        else:
            cv2.imshow("Entry Camera", frame)

    cap.release()
    if arduino:
        arduino.close()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()