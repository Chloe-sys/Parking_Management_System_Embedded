#include <SPI.h>
#include <MFRC522.h>

#define RST_PIN 9
#define SS_PIN 10

MFRC522 mfrc522(SS_PIN, RST_PIN);
MFRC522::MIFARE_Key key;

const int PLATE_BLOCK = 1;
const int BALANCE_BLOCK = 2;

bool isValidPlate(String plate) {
  if (plate.length() == 0 || plate.length() > 16) return false;
  for (int i = 0; i < plate.length(); i++) {
    char c = plate.charAt(i);
    if (!isAlphaNumeric(c)) return false;
  }
  return true;
}

void setup() {
  Serial.begin(9600);
  SPI.begin();
  mfrc522.PCD_Init();

  for (byte i = 0; i < 6; i++) {
    key.keyByte[i] = 0xFF;
  }

  Serial.println("Place your RFID card...");
  Serial.println("Type REGISTER to register a new card.");
}

void loop() {
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();

    if (command.equalsIgnoreCase("REGISTER")) {
      registerCard();
    }
  }

  // Normal payment processing mode
  if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
    processPayment();
  }
}

void registerCard() {
  Serial.println("=== Card Registration Mode ===");
  Serial.println("Enter Plate Number (max 16 alphanumeric chars):");
  String plate = waitForSerialInput();
  plate.trim();

  if (!isValidPlate(plate)) {
    Serial.println("Invalid plate format. Registration aborted.");
    return;
  }

  Serial.println("Enter Initial Balance (e.g., 1000.00):");
  String balanceStr = waitForSerialInput();
  balanceStr.trim();
  float balance = balanceStr.toFloat();

  if (balance < 0 || isnan(balance)) {
    Serial.println("Invalid balance amount. Registration aborted.");
    return;
  }

  Serial.println("Tap the card to register...");

  unsigned long start = millis();
  while (!mfrc522.PICC_IsNewCardPresent() || !mfrc522.PICC_ReadCardSerial()) {
    if (millis() - start > 20000) {  // 20 sec timeout
      Serial.println("Timeout waiting for card. Registration aborted.");
      return;
    }
  }

  if (writePlateAndBalance(plate, balance)) {
    Serial.println("Card registered successfully!");
    Serial.print("Plate: ");
    Serial.println(plate);
    Serial.print("Balance: ");
    Serial.println(balance, 2);
  } else {
    Serial.println("Failed to register card.");
  }

  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
  Serial.println("Exiting registration mode.");
}

String waitForSerialInput() {
  while (!Serial.available()) {
    // wait
  }
  return Serial.readStringUntil('\n');
}

bool writePlateAndBalance(String plate, float balance) {
  MFRC522::StatusCode status;
  byte buffer[16];

  // Authenticate Plate Block
  status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, PLATE_BLOCK, &key, &(mfrc522.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Auth failed for writing plate.");
    return false;
  }

  // Prepare plate buffer (16 bytes, padded with spaces)
  memset(buffer, ' ', sizeof(buffer));
  for (int i = 0; i < plate.length() && i < 16; i++) {
    buffer[i] = plate.charAt(i);
  }

  status = mfrc522.MIFARE_Write(PLATE_BLOCK, buffer, 16);
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Failed to write plate.");
    return false;
  }

  // Authenticate Balance Block
  status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, BALANCE_BLOCK, &key, &(mfrc522.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Auth failed for writing balance.");
    return false;
  }

  // Prepare balance buffer (store float in first 4 bytes, rest zeros)
  memset(buffer, 0, sizeof(buffer));
  memcpy(buffer, &balance, sizeof(float));

  status = mfrc522.MIFARE_Write(BALANCE_BLOCK, buffer, 16);
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Failed to write balance.");
    return false;
  }

  return true;
}

void processPayment() {
  byte buffer[18];
  byte size = sizeof(buffer);
  MFRC522::StatusCode status;

  // Authenticate and read Plate
  status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, PLATE_BLOCK, &key, &(mfrc522.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Auth failed for plate.");
    return;
  }

  status = mfrc522.MIFARE_Read(PLATE_BLOCK, buffer, &size);
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Reading plate failed.");
    return;
  }

  String plateNumber = "";
  for (int i = 0; i < 16; i++) {
    if (isPrintable(buffer[i])) {
      plateNumber += (char)buffer[i];
    }
  }
  plateNumber.trim();

  if (!isValidPlate(plateNumber)) {
    Serial.println("Invalid plate format.");
    return;
  }

  // Authenticate and read Balance
  status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, BALANCE_BLOCK, &key, &(mfrc522.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Auth failed for balance.");
    return;
  }

  status = mfrc522.MIFARE_Read(BALANCE_BLOCK, buffer, &size);
  if (status != MFRC522::STATUS_OK) {
    Serial.println("Reading balance failed.");
    return;
  }

  float currentBalance;
  memcpy(&currentBalance, buffer, sizeof(float));

  if (isnan(currentBalance) || currentBalance < 0) {
    Serial.println("Invalid balance stored on card.");
    return;
  }

  Serial.print("PLATE:");
  Serial.print(plateNumber);
  Serial.print(";BALANCE:");
  Serial.println(currentBalance, 2);

  // Wait for payment amount from PC
  unsigned long start = millis();
  while (!Serial.available()) {
    if (millis() - start > 10000) {
      Serial.println("Timeout waiting for amount.");
      return;
    }
  }
  String input = Serial.readStringUntil('\n');
  input.trim();
  float amountDue = input.toFloat();

  if (amountDue <= 0 || isnan(amountDue)) {
    Serial.println("Invalid amount received.");
    return;
  }

  if (amountDue > currentBalance) {
    Serial.println("INSUFFICIENT");
  } else {
    float newBalance = currentBalance - amountDue;

    byte writeBuffer[16];
    memset(writeBuffer, 0, sizeof(writeBuffer));
    memcpy(writeBuffer, &newBalance, sizeof(float));

    // Authenticate before writing
    status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, BALANCE_BLOCK, &key, &(mfrc522.uid));
    if (status != MFRC522::STATUS_OK) {
      Serial.println("Auth failed for write.");
      return;
    }

    status = mfrc522.MIFARE_Write(BALANCE_BLOCK, writeBuffer, 16);
    if (status != MFRC522::STATUS_OK) {
      Serial.println("Write failed.");
      return;
    }

    Serial.println("DONE");
  }

  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
}
