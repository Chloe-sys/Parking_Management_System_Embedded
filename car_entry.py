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

# Constants
MODEL_PATH = './best.pt'
SAVE_DIR = 'plates'
CSV_FILE = 'plates_log.csv'
ENTRY_COOLDOWN = 300  # seconds
PLATE_LENGTH = 7
VALID_PREFIX = 'RA'

# Initialize directories and files
os.makedirs(SAVE_DIR, exist_ok=True)
if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, 'w', newline='') as f:
        csv.writer(f).writerow(['Plate Number', 'Action', 'Payment Status', 'Timestamp', 'Amount Due'])

# Load YOLO model
model = YOLO(MODEL_PATH)

def detect_arduino_port():
    """Detect Arduino serial port automatically."""
    ports = list(serial.tools.list_ports.comports())
    print(f"[DEBUG] Available ports: {[p.device for p in ports]}")
    for port in ports:
        if "usbmodem" in port.device.lower() or "wchusbmodem" in port.device.lower():
            print(f"[DEBUG] Found potential Arduino at {port.device}")
            return port.device
    print("[DEBUG] No Arduino port detected")
    return None

def connect_arduino():
    """Establish connection with Arduino."""
    port = detect_arduino_port()
    if port:
        try:
            print(f"[DEBUG] Attempting to connect to {port}")
            arduino = serial.Serial(port, 9600, timeout=1)
            time.sleep(2)  # Allow connection to stabilize
            print(f"[CONNECTED] Arduino on {port}")
            # Test communication
            arduino.write(b'TEST\n')
            response = arduino.readline().decode().strip()
            print(f"[DEBUG] Arduino test response: {response}")
            return arduino
        except Exception as e:
            print(f"[ERROR] Failed to connect to Arduino: {e}")
    print("[WARNING] Arduino not detected - running in simulation mode.")
    return None

def read_distance(arduino):
    """Read distance from ultrasonic sensor."""
    try:
        if arduino and arduino.in_waiting > 0:
            line = arduino.readline().decode('utf-8').strip()
            if line:
                distance = float(line)
                if distance >= 0:
                    print(f"[DEBUG] Distance reading: {distance} cm")
                    return distance
    except Exception as e:
        print(f"[ERROR] Reading distance: {e}")
    return None

def extract_plate_text(plate_img):
    """Extract text from license plate image using OCR with enhanced preprocessing."""
    try:
        # Save the detected plate for debugging
        debug_img_path = os.path.join(SAVE_DIR, f"debug_{time.time()}.jpg")
        cv2.imwrite(debug_img_path, plate_img)
        
        # Enhanced preprocessing
        gray = cv2.cvtColor(plate_img, cv2.COLOR_BGR2GRAY)
        
        # Apply CLAHE for better contrast
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        gray = clahe.apply(gray)
        
        # Denoising
        gray = cv2.fastNlMeansDenoising(gray, None, h=10)
        
        # Thresholding
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        
        # Morphological operations to clean up the image
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3,3))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        
        # Try multiple OCR configurations
        configs = [
            '--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
            '--psm 7 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
            '--psm 10 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        ]
        
        for config in configs:
            text = pytesseract.image_to_string(thresh, config=config).strip().replace(" ", "")
            if len(text) >= PLATE_LENGTH and "RA" in text:
                print(f"[DEBUG] OCR with config {config}: {text}")
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
    if not os.path.exists(CSV_FILE):
        return None
    
    try:
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
        print(f"[ERROR] Failed to check plate status: {e}")
        return None

def control_gate(arduino, action):
    """Handle gate control with robust error handling."""
    if not arduino:
        print("[SIMULATION] Would send gate command:", action)
        return True
    
    try:
        if action == 'open':
            print("[GATE] Sending OPEN command")
            arduino.write(b'OPEN\n')
        elif action == 'close':
            print("[GATE] Sending CLOSE command")
            arduino.write(b'CLOSE\n')
        elif action == 'buzzer':
            print("[GATE] Activating buzzer")
            arduino.write(b'BUZZ\n')
        
        arduino.flush()
        time.sleep(0.1)
        response = arduino.readline().decode().strip()
        print(f"[ARDUINO] Response: {response}")
        return True
    except Exception as e:
        print(f"[ERROR] Gate control failed: {e}")
        return False

def log_action(plate, action, arduino):
    """Log entry/exit and control gate."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    payment_status = '0' if action == 'entry' else '1'
    amount_due = '0'
    
    print(f"[ACTION] Processing {action} for {plate}")
    
    # Log to CSV
    try:
        with open(CSV_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([plate, action, payment_status, timestamp, amount_due])
        print(f"[LOGGED] {action.upper()} for {plate} at {timestamp}")
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