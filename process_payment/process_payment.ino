#include <SPI.h>
#include <MFRC522.h>

#define RST_PIN 9
#define SS_PIN 10

MFRC522 mfrc522(SS_PIN, RST_PIN);
MFRC522::MIFARE_Key key;

const int PLATE_BLOCK = 1;
const int BALANCE_BLOCK = 2;

void setup() {
  Serial.begin(9600);
  SPI.begin();
  mfrc522.PCD_Init();

  for (byte i = 0; i < 6; i++) {
    key.keyByte[i] = 0xFF;
  }

  Serial.println("Place your RFID card...");
}

void loop() {
  if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
    byte buffer[18];
    byte size = sizeof(buffer);
    byte status;

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

    Serial.print("PLATE:");
    Serial.print(plateNumber);
    Serial.print(";BALANCE:");
    Serial.println(currentBalance, 2);

    // Wait for payment amount from PC
    while (!Serial.available());
    String input = Serial.readStringUntil('\n');
    float amountDue = input.toFloat();

    if (amountDue > currentBalance) {
      Serial.println("INSUFFICIENT");
    } else {
      float newBalance = currentBalance - amountDue;

      byte writeBuffer[16];
      memset(writeBuffer, 0, sizeof(writeBuffer));
      memcpy(writeBuffer, &newBalance, sizeof(float));

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
}
