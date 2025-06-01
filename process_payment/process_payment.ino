#include <SPI.h>
#include <MFRC522.h>

#define RST_PIN 9
#define SS_PIN 10
#define PAYMENT_PREFIX "PAY:"
#define PAYMENT_PREFIX_LEN 4
#define REGISTER_CMD "REGISTER"
#define MAX_PLATE_LENGTH 16
#define TIMEOUT_MS 10000

MFRC522 mfrc522(SS_PIN, RST_PIN);
MFRC522::MIFARE_Key key;

const int PLATE_BLOCK = 1;
const int BALANCE_BLOCK = 2;

void setup() {
  Serial.begin(9600);
  while (!Serial); // Wait for serial port to connect
  SPI.begin();
  mfrc522.PCD_Init();

  // Initialize default key
  for (byte i = 0; i < 6; i++) {
    key.keyByte[i] = 0xFF;
  }

  Serial.println("System Ready");
  Serial.println("Place your RFID card near the reader...");
  Serial.println("Type 'REGISTER' to register a new card.");
}

void loop() {
  // Handle serial commands
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    
    if (command.equalsIgnoreCase(REGISTER_CMD)) {
      registerCard();
    } else {
      Serial.println("Unknown command. Type 'REGISTER' to register a new card.");
    }
  }

  // Process RFID cards
  if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
    processPayment();
  }
}

bool isValidPlate(const String &plate) {
  if (plate.length() == 0 || plate.length() > MAX_PLATE_LENGTH) return false;
  
  // Rwanda plates start with RA followed by alphanumeric characters
  if (!plate.startsWith("RA")) return false;
  
  for (int i = 0; i < plate.length(); i++) {
    char c = plate.charAt(i);
    if (!isAlphaNumeric(c)) return false;
  }
  return true;
}

String waitForSerialInput(unsigned long timeout = TIMEOUT_MS) {
  unsigned long start = millis();
  while (!Serial.available()) {
    if (millis() - start > timeout) {
      return ""; // Timeout
    }
  }
  return Serial.readStringUntil('\n');
}

void registerCard() {
  Serial.println("\n=== CARD REGISTRATION ===");
  
  // Get plate number
  Serial.println("Enter Plate Number (format RAxxxxx, max 16 chars):");
  String plate = waitForSerialInput();
  plate.trim();
  
  if (!isValidPlate(plate)) {
    Serial.println("Error: Invalid plate format. Must start with RA and be alphanumeric.");
    return;
  }

  // Get initial balance
  Serial.println("Enter Initial Balance (e.g., 1000.00):");
  String balanceStr = waitForSerialInput();
  balanceStr.trim();
  float balance = balanceStr.toFloat();
  
  if (balance <= 0 || isnan(balance)) {
    Serial.println("Error: Invalid balance amount. Must be positive number.");
    return;
  }

  // Wait for card tap
  Serial.println("\nTap the card to register...");
  unsigned long start = millis();
  while (!mfrc522.PICC_IsNewCardPresent() || !mfrc522.PICC_ReadCardSerial()) {
    if (millis() - start > 20000) {
      Serial.println("Error: Timeout waiting for card.");
      return;
    }
  }

  // Write data to card
  if (writePlateAndBalance(plate, balance)) {
    Serial.println("\nCard registered successfully!");
    Serial.print("Plate: ");
    Serial.println(plate);
    Serial.print("Balance: ");
    Serial.println(balance, 2);
  } else {
    Serial.println("Error: Failed to write to card.");
  }

  // Clean up
  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
  Serial.println("Registration complete.\n");
}

bool writePlateAndBalance(const String &plate, float balance) {
  MFRC522::StatusCode status;
  byte buffer[16];

  // Write Plate Number
  status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, PLATE_BLOCK, &key, &(mfrc522.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Error: Authentication failed for plate block.");
    return false;
  }

  memset(buffer, ' ', sizeof(buffer)); // Pad with spaces
  for (int i = 0; i < plate.length() && i < sizeof(buffer); i++) {
    buffer[i] = plate.charAt(i);
  }

  status = mfrc522.MIFARE_Write(PLATE_BLOCK, buffer, 16);
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Error: Failed to write plate number.");
    return false;
  }

  // Write Balance
  status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, BALANCE_BLOCK, &key, &(mfrc522.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Error: Authentication failed for balance block.");
    return false;
  }

  memset(buffer, 0, sizeof(buffer));
  memcpy(buffer, &balance, sizeof(float));

  status = mfrc522.MIFARE_Write(BALANCE_BLOCK, buffer, 16);
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Error: Failed to write balance.");
    return false;
  }

  return true;
}

void processPayment() {
  byte buffer[18];
  byte size = sizeof(buffer);
  MFRC522::StatusCode status;

  // Read Plate Number
  status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, PLATE_BLOCK, &key, &(mfrc522.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.println("ERROR:AUTH_PLATE");
    mfrc522.PICC_HaltA();
    return;
  }

  status = mfrc522.MIFARE_Read(PLATE_BLOCK, buffer, &size);
  if (status != MFRC522::STATUS_OK) {
    Serial.println("ERROR:READ_PLATE");
    mfrc522.PICC_HaltA();
    return;
  }

  String plateNumber = "";
  for (int i = 0; i < 16 && isPrintable(buffer[i]); i++) {
    plateNumber += (char)buffer[i];
  }
  plateNumber.trim();

  if (!isValidPlate(plateNumber)) {
    Serial.println("ERROR:INVALID_PLATE");
    mfrc522.PICC_HaltA();
    return;
  }

  // Read Balance
  status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, BALANCE_BLOCK, &key, &(mfrc522.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.println("ERROR:AUTH_BALANCE");
    mfrc522.PICC_HaltA();
    return;
  }

  status = mfrc522.MIFARE_Read(BALANCE_BLOCK, buffer, &size);
  if (status != MFRC522::STATUS_OK) {
    Serial.println("ERROR:READ_BALANCE");
    mfrc522.PICC_HaltA();
    return;
  }

  float currentBalance;
  memcpy(&currentBalance, buffer, sizeof(float));

  if (isnan(currentBalance) || currentBalance < 0) {
    Serial.println("ERROR:INVALID_BALANCE");
    mfrc522.PICC_HaltA();
    return;
  }

  // Send card data to Python
  Serial.print("PLATE:");
  Serial.print(plateNumber);
  Serial.print(";BALANCE:");
  Serial.println(currentBalance, 2);

  // Wait for payment command
  unsigned long start = millis();
  while (!Serial.available()) {
    if (millis() - start > TIMEOUT_MS) {
      Serial.println("ERROR:TIMEOUT");
      mfrc522.PICC_HaltA();
      return;
    }
  }

  String paymentCmd = Serial.readStringUntil('\n');
  paymentCmd.trim();

  // Parse payment amount
  float amountDue = 0;
  if (paymentCmd.startsWith(PAYMENT_PREFIX)) {
    String amountStr = paymentCmd.substring(PAYMENT_PREFIX_LEN);
    amountDue = amountStr.toFloat();
  } else {
    // Backward compatibility
    amountDue = paymentCmd.toFloat();
  }

  // Validate amount
  if (amountDue <= 0 || isnan(amountDue)) {
    Serial.println("ERROR:INVALID_AMOUNT");
    mfrc522.PICC_HaltA();
    return;
  }

  // Check balance
  if (amountDue > currentBalance) {
    Serial.println("INSUFFICIENT");
    mfrc522.PICC_HaltA();
    return;
  }

  // Process payment
  float newBalance = currentBalance - amountDue;
  byte writeBuffer[16];
  memset(writeBuffer, 0, sizeof(writeBuffer));
  memcpy(writeBuffer, &newBalance, sizeof(float));

  status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, BALANCE_BLOCK, &key, &(mfrc522.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.println("ERROR:AUTH_WRITE");
    mfrc522.PICC_HaltA();
    return;
  }

  status = mfrc522.MIFARE_Write(BALANCE_BLOCK, writeBuffer, 16);
  if (status != MFRC522::STATUS_OK) {
    Serial.println("ERROR:WRITE_FAILED");
    mfrc522.PICC_HaltA();
    return;
  }

  Serial.println("DONE");
  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
}