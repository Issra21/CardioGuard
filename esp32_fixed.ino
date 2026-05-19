#include <WiFi.h>
#include <WiFiClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

const char* SSID        = "TOPNET_LMLR";
const char* PASSWORD    = "ak49nj3dq8";
const char* MQTT_SERVER = "74.161.34.200";
const int   MQTT_PORT   = 1883;
const char* MQTT_USER   = "Issra";
const char* MQTT_PASS   = "Issra2026";
const char* TOPIC_DATA  = "ecg/data";
const char* TOPIC_ALERT = "ecg/alert";

#define PIN_ECG      36
#define PIN_LO_PLUS  25
#define PIN_LO_MINUS 26
#define PIN_BUZZER   27
#define PIN_LED      2

#define FS           250
#define INTERVAL_US  (1000000/FS)
#define BATCH_SIZE   25

WiFiClient   wifiClient;
PubSubClient mqttClient(wifiClient);

int16_t batch[BATCH_SIZE];
int     batchIdx      = 0;
unsigned long lastSample    = 0;
unsigned long lastReconnect = 0;
unsigned long lastMqttLoop  = 0;  // ← NOUVEAU : loop MQTT séparé

void connectWiFi() {
  Serial.print("WiFi");
  WiFi.begin(SSID, PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
  }
  Serial.println(" OK → " + WiFi.localIP().toString());
}

void connectMQTT() {
  Serial.print("MQTT ");
  // ← keepalive 60s (correspond au timeout Mosquitto)
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setKeepAlive(60);        // ← CORRECTION timeout
  mqttClient.setBufferSize(512);
  mqttClient.setSocketTimeout(15);    // ← timeout connexion
  mqttClient.setCallback(mqttCallback);
  if (mqttClient.connect("ESP32_ECG_Issra", MQTT_USER, MQTT_PASS)) {
    mqttClient.subscribe(TOPIC_ALERT);
    Serial.println("OK ✓");
    digitalWrite(PIN_LED, HIGH);
  } else {
    Serial.print("FAIL err=");
    Serial.println(mqttClient.state());
    digitalWrite(PIN_LED, LOW);
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  if (strcmp(topic, TOPIC_ALERT) == 0) {
    Serial.println("\n ALERTE !");
    for (int i = 0; i < 3; i++) {
      digitalWrite(PIN_BUZZER, HIGH); delay(200);
      digitalWrite(PIN_BUZZER, LOW);  delay(150);
    }
    for (int i = 0; i < 10; i++) {
      digitalWrite(PIN_LED, !digitalRead(PIN_LED));
      delay(250);
    }
    digitalWrite(PIN_LED, HIGH);
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== ESP32 ECG Azure 74.161.34.200 ===\n");
  pinMode(PIN_LO_PLUS,  INPUT);
  pinMode(PIN_LO_MINUS, INPUT);
  pinMode(PIN_BUZZER,   OUTPUT);
  pinMode(PIN_LED,      OUTPUT);
  digitalWrite(PIN_BUZZER, LOW);
  digitalWrite(PIN_LED,    LOW);
  connectWiFi();
  connectMQTT();
  Serial.println("Acquisition 250 Hz démarrée");
}

void loop() {
  // ← CORRECTION : appeler mqttClient.loop() toutes les 50ms
  // pas seulement quand on envoie un batch
  unsigned long now_ms = millis();
  if (now_ms - lastMqttLoop >= 50) {
    lastMqttLoop = now_ms;
    if (mqttClient.connected()) {
      mqttClient.loop();
    } else {
      if (now_ms - lastReconnect > 5000) {
        lastReconnect = now_ms;
        Serial.println("\nReconnexion MQTT...");
        connectMQTT();
      }
    }
  }

  // Acquisition 250 Hz
  unsigned long now_us = micros();
  if (now_us - lastSample < INTERVAL_US) return;
  lastSample = now_us;

  if (digitalRead(PIN_LO_PLUS) || digitalRead(PIN_LO_MINUS)) {
    digitalWrite(PIN_LED, !digitalRead(PIN_LED));
    return;
  }

  batch[batchIdx++] = analogRead(PIN_ECG);

  if (batchIdx >= BATCH_SIZE) {
    batchIdx = 0;
    if (mqttClient.connected()) {
      StaticJsonDocument<512> doc;
      JsonArray arr = doc.createNestedArray("b");
      for (int i = 0; i < BATCH_SIZE; i++) arr.add(batch[i]);
      doc["t"] = millis();
      char payload[512];
      serializeJson(doc, payload);
      mqttClient.publish(TOPIC_DATA, payload);
    }
  }
}
