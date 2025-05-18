#include <SPI.h>
#include <MFRC522.h>

#define SS_PIN 10
#define RST_PIN 9
MFRC522 rfid(SS_PIN, RST_PIN);
MFRC522::MIFARE_Key key;

void setup() {
  Serial.begin(9600);
  SPI.begin();
  rfid.PCD_Init();
  Serial.println("Place your RFID card...");
}

void loop() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) {
    return;
  }

  Serial.print("Card UID:");
  for (byte i = 0; i < rfid.uid.size; i++) {
    Serial.print(rfid.uid.uidByte[i] < 0x10 ? " 0" : " ");
    Serial.print(rfid.uid.uidByte[i], HEX);
  }
  Serial.println();

  byte plateBlock = 1;
  byte balanceBlock = 2; 
  byte data[16];
  memset(data, 0, 16); 

  Serial.println("Type plate number (7 chars only) and press ENTER:");
  while (!Serial.available());

  String plate = Serial.readStringUntil('\n');
  plate.trim();

  // Enforce exactly 7 characters, pad with spaces if needed
  if (plate.length() < 7) {
    while (plate.length() < 7) {
      plate += ' ';
    }
  } else if (plate.length() > 7) {
    plate = plate.substring(0, 7);
  }

  // Copy to data buffer
  plate.getBytes(data, 8); // +1 for null terminator (safe), but block is 16 bytes

  for (byte i = 0; i < 6; i++) key.keyByte[i] = 0xFF;
  MFRC522::StatusCode status = rfid.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, plateBlock, &key, &(rfid.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.print("Auth failed: "); Serial.println(rfid.GetStatusCodeName(status));
    return;
  }

  status = rfid.MIFARE_Write(plateBlock, data, 16);
  if (status != MFRC522::STATUS_OK) {
    Serial.print("Write failed: "); Serial.println(rfid.GetStatusCodeName(status));
  } else {
    Serial.println("Plate number written successfully!");
  }

  delay(2000);

  float balance = 0.0;
  Serial.println("Enter balance amount to store (e.g., 1000.00) and press ENTER:");

  while (!Serial.available());
  String balanceStr = Serial.readStringUntil('\n');
  balance = balanceStr.toFloat();

  byte balanceData[16];
  memcpy(balanceData, &balance, sizeof(balance));

  status = rfid.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, balanceBlock, &key, &(rfid.uid));
  if (status != MFRC522::STATUS_OK) {
    Serial.print("Auth failed: "); Serial.println(rfid.GetStatusCodeName(status));
    return;
  }

  status = rfid.MIFARE_Write(balanceBlock, balanceData, 16);
  if (status != MFRC522::STATUS_OK) {
    Serial.print("Write failed: "); Serial.println(rfid.GetStatusCodeName(status));
  } else {
    Serial.println("Balance written successfully!");
  }

  delay(2000);

  byte readBuffer[18];
  byte size = sizeof(readBuffer);
  
  status = rfid.MIFARE_Read(plateBlock, readBuffer, &size);
  if (status != MFRC522::STATUS_OK) {
    Serial.print("Plate Read failed: "); Serial.println(rfid.GetStatusCodeName(status));
  } else {
    Serial.print("Stored plate number: ");
    for (int i = 0; i < 7; i++) {
      Serial.print((char)readBuffer[i]);
    }
    Serial.println();
  }

  status = rfid.MIFARE_Read(balanceBlock, readBuffer, &size);
  if (status != MFRC522::STATUS_OK) {
    Serial.print("Balance Read failed: "); Serial.println(rfid.GetStatusCodeName(status));
  } else {
    float storedBalance;
    memcpy(&storedBalance, readBuffer, sizeof(float));
    Serial.print("Stored balance: ");
    Serial.println(storedBalance, 2);
  }

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}
