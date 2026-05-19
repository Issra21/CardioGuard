#include <M5StickCPlus.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ══════════════════════════════════════════
// CONFIGURATION
// ══════════════════════════════════════════
const char* SSID        = "TOPNET_LMLR";
const char* PASSWORD    = "ak49nj3dq8";
const char* MQTT_SERVER = "74.161.34.200";
const int   MQTT_PORT   = 1883;
const char* MQTT_USER   = "Issra";
const char* MQTT_PASS   = "Issra2026";
const char* TOPIC_ECG   = "ecg/data/ISSRA2026";
const char* TOPIC_ALERT = "ecg/alert/ISSRA2026";
const char* PATIENT     = "Issra Saidi";

// ══════════════════════════════════════════
// PINS
// ══════════════════════════════════════════
#define PIN_ECG      36   // G36/G25 ← AD8232 OUTPUT
#define PIN_LO_MINUS 26   // G26     ← AD8232 LO-

#define FS           250
#define INTERVAL_US  (1000000/FS)
#define BATCH_SIZE   25

// ══════════════════════════════════════════
// DÉTECTION ÉLECTRODE
// ── Méthode : on vérifie la valeur ADC
// ── Si pas d'électrode → ADC retourne 0 ou 4095
// ── Si électrode branchée → ADC retourne ~1400-2600
// ══════════════════════════════════════════
#define ECG_MIN_VALID  800    // valeur ADC minimum = électrode branchée
#define ECG_MAX_VALID  3900   // valeur ADC maximum = électrode branchée
#define CHECK_SAMPLES  10     // nombre de samples pour vérifier connexion

// ══════════════════════════════════════════
// VARIABLES GLOBALES
// ══════════════════════════════════════════
WiFiClient   wifiClient;
PubSubClient mqttClient(wifiClient);

int16_t batch[BATCH_SIZE];
int     batchIdx          = 0;
bool    alertActive       = false;
bool    electrodeOK       = false;  // ← état électrode
float   alertProba        = 0.0;
int     badSampleCount    = 0;      // compteur samples invalides
int     goodSampleCount   = 0;      // compteur samples valides

unsigned long lastSample    = 0;
unsigned long lastReconnect = 0;
unsigned long lastDisplay   = 0;
unsigned long lastMqttLoop  = 0;
unsigned long alertTime     = 0;

// ══════════════════════════════════════════
// VÉRIFICATION ÉLECTRODE via valeur ADC
// ══════════════════════════════════════════
bool checkElectrode(int adcValue) {
  // Si la valeur ADC est dans la plage normale → électrode branchée
  if (adcValue > ECG_MIN_VALID && adcValue < ECG_MAX_VALID) {
    goodSampleCount++;
    badSampleCount = 0;
    if (goodSampleCount >= CHECK_SAMPLES) {
      goodSampleCount = CHECK_SAMPLES; // cap
      return true;  // électrode OK
    }
  } else {
    // Valeur invalide (0 ou proche de 4095)
    badSampleCount++;
    goodSampleCount = 0;
    if (badSampleCount >= CHECK_SAMPLES) {
      badSampleCount = CHECK_SAMPLES; // cap
      return false; // électrode déconnectée
    }
  }
  return electrodeOK; // garder état précédent si pas encore décidé
}

// ══════════════════════════════════════════
// CONNEXION WIFI
// ══════════════════════════════════════════
void connectWiFi() {
  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setTextSize(2);
  M5.Lcd.setTextColor(CYAN);
  M5.Lcd.setCursor(10, 40);
  M5.Lcd.println("WiFi...");
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(WHITE);
  M5.Lcd.setCursor(10, 70);
  M5.Lcd.println(SSID);
  WiFi.begin(SSID, PASSWORD);
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t < 20) {
    delay(500); t++;
    M5.Lcd.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    M5.Lcd.fillScreen(BLACK);
    M5.Lcd.setTextColor(GREEN);
    M5.Lcd.setTextSize(2);
    M5.Lcd.setCursor(10, 55);
    M5.Lcd.println("WiFi OK!");
  } else {
    M5.Lcd.fillScreen(BLACK);
    M5.Lcd.setTextColor(RED);
    M5.Lcd.setTextSize(2);
    M5.Lcd.setCursor(10, 55);
    M5.Lcd.println("WiFi FAIL");
  }
  delay(800);
}

// ══════════════════════════════════════════
// CONNEXION MQTT
// ══════════════════════════════════════════
void connectMQTT() {
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setKeepAlive(60);
  mqttClient.setBufferSize(512);
  mqttClient.setCallback(onMessage);
  if (mqttClient.connect("M5StickC_Issra", MQTT_USER, MQTT_PASS)) {
    mqttClient.subscribe(TOPIC_ALERT);
    Serial.println("MQTT OK");
  } else {
    Serial.print("MQTT FAIL err=");
    Serial.println(mqttClient.state());
  }
}

// ══════════════════════════════════════════
// CALLBACK MQTT — alerte depuis Azure
// ══════════════════════════════════════════
void onMessage(char* topic, byte* payload, unsigned int length) {
  if (strcmp(topic, TOPIC_ALERT) == 0) {
    String msg = "";
    for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
    StaticJsonDocument<128> doc;
    deserializeJson(doc, msg);
    if ((doc["alert"] | 0) == 1) {
      alertActive = true;
      alertProba  = doc["proba"] | 0.0f;
      alertTime   = millis();
      Serial.println("ALERTE reçue !");
      for (int i = 0; i < 3; i++) {
        M5.Beep.tone(1000, 250); delay(350);
      }
      M5.Beep.mute();
    }
  }
}

// ══════════════════════════════════════════
// AFFICHAGE — EN ATTENTE (électrode non branchée)
// ══════════════════════════════════════════
void displayWaiting() {
  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.fillRect(0, 0, 240, 4, 0x4A69);

  // Nom patient
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(0x6B6D);
  M5.Lcd.setCursor(5, 8);
  M5.Lcd.println(PATIENT);
  M5.Lcd.drawLine(0, 20, 240, 20, 0x2945);

  // Titre état
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(0x6B6D);
  M5.Lcd.setCursor(5, 28);
  M5.Lcd.println("Etat de sante :");

  // État en attente
  M5.Lcd.setTextSize(2);
  M5.Lcd.setTextColor(YELLOW);
  M5.Lcd.setCursor(5, 42);
  M5.Lcd.println("En attente");
  M5.Lcd.drawLine(0, 68, 240, 68, 0x2945);

  // Instruction
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(WHITE);
  M5.Lcd.setCursor(5, 76);
  M5.Lcd.println("Veuillez brancher");
  M5.Lcd.setCursor(5, 90);
  M5.Lcd.println("les electrodes.");

  // WiFi + MQTT
  M5.Lcd.drawLine(0, 108, 240, 108, 0x2945);
  M5.Lcd.setCursor(5, 114);
  M5.Lcd.setTextColor(WHITE);
  M5.Lcd.print("WiFi: ");
  M5.Lcd.setTextColor(WiFi.status() == WL_CONNECTED ? GREEN : RED);
  M5.Lcd.print(WiFi.status() == WL_CONNECTED ? "OK" : "--");
  M5.Lcd.setTextColor(WHITE);
  M5.Lcd.setCursor(120, 114);
  M5.Lcd.print("MQTT: ");
  M5.Lcd.setTextColor(mqttClient.connected() ? GREEN : RED);
  M5.Lcd.println(mqttClient.connected() ? "OK" : "--");
}

// ══════════════════════════════════════════
// AFFICHAGE — TOUT VA BIEN
// ══════════════════════════════════════════
void displayNormal() {
  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.fillRect(0, 0, 240, 4, GREEN);

  // Nom patient
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(CYAN);
  M5.Lcd.setCursor(5, 8);
  M5.Lcd.println(PATIENT);
  M5.Lcd.drawLine(0, 20, 240, 20, 0x2945);

  // Titre état
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(0x6B6D);
  M5.Lcd.setCursor(5, 28);
  M5.Lcd.println("Etat de sante :");

  // État bien
  M5.Lcd.setTextSize(3);
  M5.Lcd.setTextColor(GREEN);
  M5.Lcd.setCursor(5, 42);
  M5.Lcd.println("Bien");
  M5.Lcd.drawLine(0, 82, 240, 82, 0x2945);

  // WiFi + MQTT
  M5.Lcd.setTextSize(1);
  M5.Lcd.setCursor(5, 90);
  M5.Lcd.setTextColor(WHITE);
  M5.Lcd.print("WiFi: ");
  M5.Lcd.setTextColor(WiFi.status() == WL_CONNECTED ? GREEN : RED);
  M5.Lcd.print(WiFi.status() == WL_CONNECTED ? "OK" : "--");
  M5.Lcd.setTextColor(WHITE);
  M5.Lcd.setCursor(120, 90);
  M5.Lcd.print("MQTT: ");
  M5.Lcd.setTextColor(mqttClient.connected() ? GREEN : RED);
  M5.Lcd.println(mqttClient.connected() ? "OK" : "--");
  M5.Lcd.drawLine(0, 104, 240, 104, 0x2945);

  // Batterie
  float bat = M5.Axp.GetBatVoltage();
  int   pct = constrain((int)((bat - 3.0) / (4.2 - 3.0) * 100), 0, 100);
  M5.Lcd.setTextColor(WHITE);
  M5.Lcd.setCursor(5, 112);
  M5.Lcd.print("Batterie: ");
  M5.Lcd.setTextColor(pct > 50 ? GREEN : pct > 20 ? YELLOW : RED);
  M5.Lcd.print(pct);
  M5.Lcd.println("%");
  int bw = (pct * 230) / 100;
  M5.Lcd.drawRect(5, 126, 230, 8, WHITE);
  if (bw > 0) M5.Lcd.fillRect(6, 127, bw, 6,
    pct > 50 ? GREEN : pct > 20 ? YELLOW : RED);
}

// ══════════════════════════════════════════
// AFFICHAGE — ALERTE
// ══════════════════════════════════════════
void displayAlert() {
  static bool toggle = false;
  toggle = !toggle;
  M5.Lcd.fillScreen(toggle ? 0xA000 : 0xC000);
  M5.Lcd.fillRect(0, 0, 240, 4, 0x8000);
  M5.Lcd.setTextColor(WHITE);

  M5.Lcd.setTextSize(2);
  M5.Lcd.setCursor(30, 8);
  M5.Lcd.println("ALERTE !");
  M5.Lcd.drawLine(0, 32, 240, 32, WHITE);

  M5.Lcd.setTextSize(1);
  M5.Lcd.setCursor(5, 38);
  M5.Lcd.println(PATIENT);
  M5.Lcd.setCursor(5, 52);
  M5.Lcd.println("Activite inhabituelle");
  M5.Lcd.setCursor(5, 64);
  M5.Lcd.println("detectee.");
  M5.Lcd.drawLine(0, 78, 240, 78, WHITE);

  M5.Lcd.setTextColor(CYAN);
  M5.Lcd.setCursor(5, 86);
  M5.Lcd.println("Restez calme.");
  M5.Lcd.setCursor(5, 98);
  M5.Lcd.println("Aide en route.");
  M5.Lcd.setCursor(5, 110);
  M5.Lcd.println("Tout ira bien.");

  M5.Lcd.setTextColor(WHITE);
  int sec = (millis() - alertTime) / 1000;
  M5.Lcd.setCursor(150, 98);
  M5.Lcd.print("Il y a:");
  M5.Lcd.setCursor(150, 110);
  M5.Lcd.print(sec); M5.Lcd.print("s");

  M5.Beep.tone(600, 80); delay(120); M5.Beep.mute();
}

// ══════════════════════════════════════════
// SETUP
// ══════════════════════════════════════════
void setup() {
  M5.begin();
  M5.Lcd.setRotation(3);
  M5.Lcd.fillScreen(BLACK);
  M5.Axp.ScreenBreath(80);
  M5.Beep.begin();
  Serial.begin(115200);

  // GPIO36 = entrée analogique uniquement
  // GPIO26 = entrée digitale LO-
  pinMode(PIN_LO_MINUS, INPUT);
  analogSetAttenuation(ADC_11db);  // ← plage 0-3.3V pour ADC

  // Écran démarrage
  M5.Lcd.fillRect(0, 0, 240, 4, CYAN);
  M5.Lcd.setTextColor(CYAN);
  M5.Lcd.setTextSize(2);
  M5.Lcd.setCursor(10, 30);
  M5.Lcd.println("Surveillance");
  M5.Lcd.setCursor(10, 55);
  M5.Lcd.println("Cardiaque");
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(WHITE);
  M5.Lcd.setCursor(10, 90);
  M5.Lcd.println(PATIENT);
  delay(1500);

  connectWiFi();
  connectMQTT();

  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setTextColor(GREEN);
  M5.Lcd.setTextSize(2);
  M5.Lcd.setCursor(5, 45);
  M5.Lcd.println("Surveillance");
  M5.Lcd.setCursor(5, 70);
  M5.Lcd.println("active !");
  delay(1000);
}

// ══════════════════════════════════════════
// LOOP
// ══════════════════════════════════════════
void loop() {
  M5.update();

  // MQTT loop toutes les 50ms
  unsigned long now_ms = millis();
  if (now_ms - lastMqttLoop >= 50) {
    lastMqttLoop = now_ms;
    if (mqttClient.connected()) {
      mqttClient.loop();
    } else {
      if (now_ms - lastReconnect > 5000) {
        lastReconnect = now_ms;
        connectMQTT();
      }
    }
  }

  // Acquisition 250 Hz
  unsigned long now_us = micros();
  if (now_us - lastSample < INTERVAL_US) return;
  lastSample = now_us;

  // Lecture ADC
  int ecgVal = analogRead(PIN_ECG);

  // ← Détection électrode par valeur ADC
  electrodeOK = checkElectrode(ecgVal);

  // Envoi MQTT seulement si électrode branchée
  if (electrodeOK) {
    // Filtre médian 3 points
    static int prev[2] = {0, 0};
    int vals[3] = {prev[0], prev[1], ecgVal};
    for (int i = 0; i < 2; i++) {
      if (vals[i] > vals[i+1]) {
        int t = vals[i]; vals[i] = vals[i+1]; vals[i+1] = t;
      }
    }
    prev[0] = prev[1]; prev[1] = ecgVal;

    batch[batchIdx++] = vals[1];

    if (batchIdx >= BATCH_SIZE) {
      batchIdx = 0;
      if (mqttClient.connected()) {
        String payload = "{\"b\":[";
        for (int i = 0; i < BATCH_SIZE; i++) {
          payload += String(batch[i]);
          if (i < BATCH_SIZE - 1) payload += ",";
        }
        payload += "],\"code\":\"ISSRA2026\",\"t\":" + String(millis()) + "}";
        mqttClient.publish(TOPIC_ECG, payload.c_str());
        Serial.print(".");
      }
    }
  } else {
    batchIdx = 0; // reset batch si électrode déconnectée
  }

  // Affichage toutes les secondes
  if (millis() - lastDisplay > 1000) {
    lastDisplay = millis();

    // Debug Serial
    Serial.print("ADC="); Serial.print(ecgVal);
    Serial.print(" electrodeOK="); Serial.println(electrodeOK);

    if (alertActive) {
      displayAlert();            // 🔴 Alerte Azure
    } else if (!electrodeOK) {
      displayWaiting();          // ⏳ Électrode non branchée
    } else {
      displayNormal();           // 💚 Tout va bien
    }
  }

  // Bouton A : annuler alerte / toggle écran
  if (M5.BtnA.wasPressed()) {
    if (alertActive) {
      alertActive = false;
      M5.Beep.mute();
      M5.Lcd.fillScreen(BLACK);
      M5.Lcd.setTextColor(GREEN);
      M5.Lcd.setTextSize(2);
      M5.Lcd.setCursor(10, 50);
      M5.Lcd.println("Compris.");
      M5.Lcd.setTextSize(1);
      M5.Lcd.setCursor(10, 80);
      M5.Lcd.println("Retour surveillance...");
      delay(1500);
    } else {
      static bool screenOn = true;
      screenOn = !screenOn;
      M5.Axp.SetLDO2(screenOn);
    }
  }

  // Bouton B : reconnexion
  if (M5.BtnB.wasPressed()) {
    M5.Lcd.fillScreen(BLACK);
    M5.Lcd.setTextColor(YELLOW);
    M5.Lcd.setTextSize(2);
    M5.Lcd.setCursor(5, 55);
    M5.Lcd.println("Reconnexion...");
    connectWiFi();
    connectMQTT();
  }
  
}