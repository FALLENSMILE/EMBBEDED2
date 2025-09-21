void setup() {
  Serial.begin(9600);  // Start serial communication at 9600 baud
}

void loop() {
  int sensorValue = analogRead(A0);  // Read analog value from MQ-2
  Serial.println(sensorValue);       // Send value over serial
  delay(1000);                       // Wait 1 second before next reading
}
