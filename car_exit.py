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

# Constants
MODEL_PATH = './best.pt'
CSV_FILE = 'plates_log.csv'
PLATE_LENGTH = 7
VALID_PREFIX = 'RA'
RATE_PER_HOUR = 200
DEBUG_IMAGE_DIR = 'debug_plates'
os.makedirs(DEBUG_IMAGE_DIR, exist_ok=True)

def detect_arduino_port():
    """Enhanced Arduino detection with more detailed debugging"""
    try:
        ports = list(serial.tools.list_ports.comports())
        print(f"[HARDWARE] Scanning serial ports...")
        
        for port in ports:
            print(f"[HARDWARE] Found port: {port.device} - {port.description}")
            if "usbmodem" in port.device.lower() or "wchusbmodem" in port.device.lower():
                print(f"[HARDWARE] Potential Arduino at {port.device}")
                return port.device
        
        print("[HARDWARE] No Arduino-compatible port found")
        return None
    except Exception as e:
        print(f"[ERROR] Port detection failed: {e}")
        return None

def connect_arduino():
    """More robust Arduino connection with simulation mode"""
    try:
        port = detect_arduino_port()
        if port:
            print(f"[HARDWARE] Attempting connection to {port}")
            arduino = serial.Serial(port, 9600, timeout=1)
            time.sleep(2)  # Initialization delay
            
            # Verify connection
            arduino.write(b'STATUS\n')
            response = arduino.readline().decode().strip()
            
            if response == "READY":
                print(f"[HARDWARE] Arduino connected at {port}")
                return arduino
            
            print("[HARDWARE] Arduino not responding correctly")
            arduino.close()
        
        print("[SYSTEM] Running in simulation mode - no physical Arduino")
        return None
        
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        return None

def read_distance(arduino):
    """Distance reading with better simulation handling"""
    if arduino is None:  # Simulation mode
        # Return a simulated distance (alternating between near and far)
        simulated_distance = 30 if time.time() % 4 < 2 else 100
        print(f"[SIMULATION] Distance: {simulated_distance} cm")
        return simulated_distance
    
    try:
        if arduino.in_waiting > 0:
            line = arduino.readline().decode().strip()
            if line:
                distance = float(line)
                print(f"[SENSOR] Distance: {distance} cm")
                return distance
    except Exception as e:
        print(f"[ERROR] Distance reading failed: {e}")
    
    return None

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
    """Improved plate validation for Rwanda formats"""
    if not text or "RA" not in text:
        return None
    
    idx = text.find("RA")
    candidate = text[idx:idx + PLATE_LENGTH]
    
    if len(candidate) != PLATE_LENGTH:
        return None
    
    # Rwanda plate pattern: RA followed by alphanumeric characters
    if not re.fullmatch(r'RA[A-Z0-9]{5}', candidate):
        return None
    
    # Must contain at least 2 digits
    if sum(c.isdigit() for c in candidate[2:]) < 2:
        return None
    
    print(f"[VALIDATION] Valid plate: {candidate}")
    return candidate


def check_exit_requirements(plate_number):
    """More robust exit validation"""
    if not os.path.exists(CSV_FILE):
        print("[DATA] No log file found")
        return (False, None)
    
    try:
        with open(CSV_FILE, 'r') as f:
            reader = csv.DictReader(f)
            records = list(reader)
            
            # Find most recent unpaid entry without exit
            for i, record in enumerate(reversed(records)):
                if (record['Plate Number'] == plate_number and 
                    record['Action'] == 'entry' and 
                    record.get('Payment Status', '0') == '1'):
                    
                    # Check for subsequent exit
                    has_exit = any(
                        r['Action'] == 'exit' and r['Timestamp'] > record['Timestamp']
                        for r in records[len(records)-i:]
                        if r['Plate Number'] == plate_number
                    )
                    
                    if not has_exit:
                        print(f"[DATA] Found valid entry at {record['Timestamp']}")
                        return (True, record['Timestamp'])
            
            print(f"[DATA] No valid entry for {plate_number}")
            return (False, None)
    except Exception as e:
        print(f"[ERROR] Database check failed: {e}")
        return (False, None)

def control_gate(arduino, action):
    """Gate control with simulation support"""
    if arduino is None:
        print(f"[SIMULATION] Gate would {action}")
        return True
    
    try:
        command = {
            'open': b'OPEN\n',
            'close': b'CLOSE\n',
            'buzzer': b'BUZZ\n'
        }.get(action, b'')
        
        if command:
            arduino.write(command)
            arduino.flush()
            time.sleep(0.1)
            
            if arduino.in_waiting > 0:
                response = arduino.readline().decode().strip()
                print(f"[HARDWARE] Response: {response}")
                return response == "OK"
        
        return False
    except Exception as e:
        print(f"[ERROR] Gate control failed: {e}")
        return False

def process_exit(plate_number, arduino):
    """Complete exit processing"""
    print(f"[PROCESSING] Handling exit for {plate_number}")
    
    try:
        # Find matching entry
        with open(CSV_FILE, 'r') as f:
            records = list(csv.DictReader(f))
            
            entry = next((
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
            
            if not entry:
                print("[ERROR] No valid entry found")
                control_gate(arduino, 'buzzer')
                return False
            
            # Calculate duration and amount
            entry_time = datetime.strptime(entry['Timestamp'], '%Y-%m-%d %H:%M:%S')
            exit_time = datetime.now()
            duration = max(1, int((exit_time - entry_time).total_seconds() / 3600))
            amount = duration * RATE_PER_HOUR
            
            print(f"[BILLING] Duration: {duration} hrs, Amount: {amount} RWF")
            
            # Log exit
            with open(CSV_FILE, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    plate_number, 
                    'exit', 
                    '1', 
                    exit_time.strftime('%Y-%m-%d %H:%M:%S'), 
                    amount
                ])
            
            # Control gate
            if control_gate(arduino, 'open'):
                time.sleep(15)
                control_gate(arduino, 'close')
                return True
            
            return False
    except Exception as e:
        print(f"[ERROR] Exit processing failed: {e}")
        return False

def main():
    """Main execution with enhanced error handling"""
    # Initialize hardware
    arduino = connect_arduino()
    
    # Initialize camera
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] Camera initialization failed")
        return
    
    # Load model
    try:
        model = YOLO(MODEL_PATH)
    except Exception as e:
        print(f"[ERROR] Model loading failed: {e}")
        cap.release()
        return
    
    # State tracking
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
            
            # Check for quit command
            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("[SYSTEM] Shutting down...")
                break
            
            # Check for vehicle presence
            distance = read_distance(arduino)
            if distance is None or distance > 50:
                cv2.imshow("Exit Camera", frame)
                continue
            
            # Process frame
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
                        
                        # Show processing
                        cv2.imshow("Plate", plate_img)
                        
                        # Process if we have consistent readings
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
            
            # Display result
            cv2.imshow("Exit Camera", results[0].plot() if results else frame)
    
    finally:
        # Cleanup
        cap.release()
        cv2.destroyAllWindows()
        if arduino:
            arduino.close()
        print("[SYSTEM] Shutdown complete")

if __name__ == "__main__":
    main()