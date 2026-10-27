#include <Arduino.h>
#include <Preferences.h>
#include <RadioLib.h>
#include <SPI.h>
#include <esp_random.h>

// Heltec Wireless Tracker V2. Same on-air packet as the T9 radios:
//   sender u32 | target u32 | type u8 | message id u32 | length u16 | text
// Type 0 is a message (15 byte header + text). Type 1 is "delivered" (9 bytes).
// Phone numbers are the device id. 0xFFFFFFFF is everyone.
//
// The T9 firmware calls RadioLib begin() with 906875000. RadioLib treats that
// as MHz, rejects it, and leaves the frequency register untouched. Bandwidth,
// spreading, coding, and sync are still applied. This bridge makes that same
// call so it sits on the channel the T9 radios are actually using. Tracker V2
// has a GC1109 amplifier, so the SX1262 is driven at 11 dBm.

static const int kCs = 8;
static const int kDio1 = 14;
static const int kReset = 12;
static const int kBusy = 13;
static const int kSck = 9;
static const int kMiso = 11;
static const int kMosi = 10;
static const int kPaPower = 7;
static const int kPaEn = 4;
static const int kPaTx = 46;
static const int kLed = 18;
static const int kVext = 3;
static const int kGpsRx = 33;
static const int kGpsTx = 34;
static const int kGpsReset = 35;

// Same number LoRaComm.cpp passes to RadioLib. It is not a MHz value.
static const float kPhoneBeginFreq = 906875000.0f;
static const float kBandwidth = 250.0f;
static const uint8_t kSpread = 12;
static const uint8_t kCoding = 8;
static const uint8_t kSync = 0x12;
static const int8_t kPower = 11;
static const uint16_t kPreamble = 8;
static const float kTcxo = 1.8f;
static const uint32_t kBroadcast = 0xFFFFFFFFu;
static const uint8_t kTypeMessage = 0;
static const uint8_t kTypeDelivered = 1;
static const size_t kMaxText = 200;

static SPIClass SpiLoRa(HSPI);
static ArduinoHal *hal = nullptr;
static Module *mod = nullptr;
static SX1262 *radio = nullptr;
static Preferences prefs;
static uint32_t phone = 0;
static float freqMhz = 906.875f;
static bool radioOk = false;
static bool spiReady = false;
static char line[240];
static size_t lineLen = 0;
static uint32_t seenId[8];
static uint32_t seenFrom[8];
static uint8_t seenAt = 0;
static char gpsLine[160];
static size_t gpsLen = 0;
static bool haveFix = false;
static double gpsLat = 0;
static double gpsLon = 0;
static double gpsAlt = 0;
static int gpsSats = 0;
static int gpsQuality = 0;
static uint32_t lastGpsReport = 0;

static void reportGps() {
    if (!haveFix) {
        Serial.printf("GPS none %d\n", gpsSats);
        return;
    }
    Serial.print("GPS ");
    Serial.print(gpsLat, 6);
    Serial.print(' ');
    Serial.print(gpsLon, 6);
    Serial.print(' ');
    Serial.print(gpsAlt, 1);
    Serial.print(' ');
    Serial.print(gpsSats);
    Serial.print(' ');
    Serial.println(gpsQuality);
}

static double nmeaDegrees(const char *field, char hemisphere) {
    double raw = atof(field);
    int degrees = (int)(raw / 100.0);
    double minutes = raw - (degrees * 100.0);
    double value = degrees + (minutes / 60.0);
    if (hemisphere == 'S' || hemisphere == 'W') {
        value = -value;
    }
    return value;
}

static bool nmeaChecksum(const char *sentence) {
    if (sentence[0] != '$') {
        return false;
    }
    const char *star = strchr(sentence, '*');
    if (star == nullptr || star[1] == '\0' || star[2] == '\0') {
        return false;
    }
    uint8_t sum = 0;
    for (const char *cursor = sentence + 1; cursor < star; cursor++) {
        sum ^= (uint8_t)(*cursor);
    }
    char *end = nullptr;
    long given = strtol(star + 1, &end, 16);
    return end != star + 1 && (uint8_t)given == sum;
}

static void acceptFix(double lat, double lon, double alt, int sats, int quality) {
    gpsLat = lat;
    gpsLon = lon;
    gpsAlt = alt;
    gpsSats = sats;
    gpsQuality = quality;
    haveFix = quality > 0;
    uint32_t now = millis();
    if (now - lastGpsReport < 1000) {
        return;
    }
    lastGpsReport = now;
    reportGps();
}

static void handleNmea(char *sentence) {
    if (!nmeaChecksum(sentence)) {
        return;
    }
    bool gga = strstr(sentence, "GGA,") != nullptr;
    bool rmc = strstr(sentence, "RMC,") != nullptr;
    if (!gga && !rmc) {
        return;
    }
    // Do not print every NMEA line to USB. That flood blocked the radio
    // poll and Mesh Chat never saw RX lines.
    char scratch[160];
    strncpy(scratch, sentence, sizeof(scratch) - 1);
    scratch[sizeof(scratch) - 1] = '\0';
    char *star = strchr(scratch, '*');
    if (star != nullptr) {
        *star = '\0';
    }
    char *fields[16];
    int count = 0;
    char *cursor = scratch + 1;
    fields[count++] = cursor;
    while (*cursor != '\0' && count < 16) {
        if (*cursor == ',') {
            *cursor = '\0';
            fields[count++] = cursor + 1;
        }
        cursor++;
    }
    if (gga && count >= 10 && fields[2][0] != '\0' && fields[4][0] != '\0') {
        int quality = atoi(fields[6]);
        int sats = atoi(fields[7]);
        gpsSats = sats;
        if (quality > 0) {
            acceptFix(nmeaDegrees(fields[2], fields[3][0]), nmeaDegrees(fields[4], fields[5][0]), atof(fields[9]), sats, quality);
        } else if (!haveFix) {
            acceptFix(0, 0, 0, sats, 0);
        }
        return;
    }
    if (rmc && count >= 7 && fields[2][0] == 'A' && fields[3][0] != '\0' && fields[5][0] != '\0') {
        acceptFix(nmeaDegrees(fields[3], fields[4][0]), nmeaDegrees(fields[5], fields[6][0]), gpsAlt, gpsSats, gpsQuality > 0 ? gpsQuality : 1);
    }
}

static void pollGps() {
    // A few bytes per loop so LoRa polling stays responsive.
    for (int n = 0; n < 64 && Serial1.available() > 0; n++) {
        char c = (char)Serial1.read();
        if (c == '\r') {
            continue;
        }
        if (c == '\n') {
            gpsLine[gpsLen] = '\0';
            if (gpsLen > 6) {
                handleNmea(gpsLine);
            }
            gpsLen = 0;
            continue;
        }
        if (gpsLen + 1 < sizeof(gpsLine)) {
            gpsLine[gpsLen++] = c;
        } else {
            gpsLen = 0;
        }
    }
}

static void startGps() {
    pinMode(kVext, OUTPUT);
    digitalWrite(kVext, HIGH);
    pinMode(kGpsReset, OUTPUT);
    digitalWrite(kGpsReset, LOW);
    delay(20);
    digitalWrite(kGpsReset, HIGH);
    delay(100);
    Serial1.begin(115200, SERIAL_8N1, kGpsRx, kGpsTx);
    Serial1.print("$CFGSYS,h35155\r\n");
    Serial1.print("$CFGMSG,0,3,0\r\n");
    Serial1.print("$CFGMSG,0,2,0\r\n");
    Serial1.print("$CFGMSG,6,0,0\r\n");
    Serial1.print("$CFGMSG,6,1,0\r\n");
}

static void paReceive() {
    digitalWrite(kPaPower, HIGH);
    digitalWrite(kPaEn, HIGH);
    digitalWrite(kPaTx, LOW);
}

static void paTransmit() {
    digitalWrite(kPaPower, HIGH);
    digitalWrite(kPaEn, HIGH);
    digitalWrite(kPaTx, HIGH);
}

static bool listen();

static void blink() {
    digitalWrite(kLed, HIGH);
    delay(40);
    digitalWrite(kLed, LOW);
}

static bool beginRadio() {
    paReceive();
    pinMode(kCs, OUTPUT);
    digitalWrite(kCs, HIGH);
    if (!spiReady) {
        SpiLoRa.begin(kSck, kMiso, kMosi);
        spiReady = true;
        delay(20);
    }
    if (radio == nullptr) {
        hal = new ArduinoHal(SpiLoRa, SPISettings(4000000, MSBFIRST, SPI_MODE0));
        mod = new Module(hal, kCs, kDio1, kReset, kBusy);
        radio = new SX1262(mod);
    }
    // Always the same begin() the phone uses. A saved 906.875 MHz would put
    // this board on a real channel while the phone stays on the unset register.
    if (prefs.isKey("freq")) {
        prefs.remove("freq");
    }
    int state = radio->begin(kPhoneBeginFreq, kBandwidth, kSpread, kCoding, kSync, kPower, kPreamble, kTcxo, false);
    if (state == RADIOLIB_ERR_CHIP_NOT_FOUND || state == RADIOLIB_ERR_SPI_CMD_FAILED) {
        radioOk = false;
        return false;
    }
    radio->setCurrentLimit(140);
    radio->setDio2AsRfSwitch(true);
    radio->setOutputPower(kPower);
    radio->setRxBoostedGainMode(true);
    radioOk = listen();
    return radioOk;
}

static bool listen() {
    if (radio == nullptr) {
        return false;
    }
    // Amp first, then the modem. Meshtastic does the same for the GC1109.
    paReceive();
    delay(10);
    radio->standby();
    paReceive();
    delay(5);
    int state = radio->startReceive();
    if (state != RADIOLIB_ERR_NONE) {
        delay(20);
        paReceive();
        state = radio->startReceive();
    }
    return state == RADIOLIB_ERR_NONE;
}

static bool transmit(const uint8_t *data, size_t len) {
    if (!radioOk || radio == nullptr || len == 0 || len > 255) {
        return false;
    }
    uint32_t airUs = radio->getTimeOnAir(len);
    uint32_t airMs = airUs / 1000u;
    if (airMs < 200 || airMs > 8000) {
        airMs = 1500;
    }
    paTransmit();
    delay(2);
    // startTransmit waits forever if BUSY stays high. The packet is already on
    // the air by then, so Mesh Chat sits on "sending" and the radio never
    // goes back to listening. Set the modem up and start TX without that wait.
    int state = radio->standby();
    if (state != RADIOLIB_ERR_NONE) {
        listen();
        return false;
    }
    state = radio->setPacketParams(
        radio->preambleLengthLoRa,
        radio->crcTypeLoRa,
        (uint8_t)len,
        radio->headerType,
        radio->invertIQEnabled);
    if (state != RADIOLIB_ERR_NONE) {
        listen();
        return false;
    }
    radio->setDioIrqParams(RADIOLIB_SX126X_IRQ_TX_DONE | RADIOLIB_SX126X_IRQ_TIMEOUT, RADIOLIB_SX126X_IRQ_TX_DONE);
    radio->setBufferBaseAddress();
    state = radio->writeBuffer(data, (uint8_t)len);
    if (state != RADIOLIB_ERR_NONE) {
        listen();
        return false;
    }
    radio->clearIrqStatus();
    paTransmit();
    radio->setTx(0);
    // Do not SPI-poll during the air time. That blocked the USB task long
    // enough for the Pi write to error out and tear down the Mesh Chat window.
    delay(airMs + 300);
    paReceive();
    delay(10);
    listen();
    return true;
}

static bool sendMessage(uint32_t target, uint32_t messageId, const char *text) {
    size_t textLen = strlen(text);
    if (textLen == 0 || textLen > kMaxText) {
        return false;
    }
    uint8_t packet[15 + kMaxText];
    size_t len = 15 + textLen;
    packet[0] = (phone >> 24) & 0xFF;
    packet[1] = (phone >> 16) & 0xFF;
    packet[2] = (phone >> 8) & 0xFF;
    packet[3] = phone & 0xFF;
    packet[4] = (target >> 24) & 0xFF;
    packet[5] = (target >> 16) & 0xFF;
    packet[6] = (target >> 8) & 0xFF;
    packet[7] = target & 0xFF;
    packet[8] = kTypeMessage;
    packet[9] = (messageId >> 24) & 0xFF;
    packet[10] = (messageId >> 16) & 0xFF;
    packet[11] = (messageId >> 8) & 0xFF;
    packet[12] = messageId & 0xFF;
    packet[13] = (textLen >> 8) & 0xFF;
    packet[14] = textLen & 0xFF;
    memcpy(packet + 15, text, textLen);
    return transmit(packet, len);
}

static void sendDelivered(uint32_t to) {
    uint8_t packet[9];
    packet[0] = (phone >> 24) & 0xFF;
    packet[1] = (phone >> 16) & 0xFF;
    packet[2] = (phone >> 8) & 0xFF;
    packet[3] = phone & 0xFF;
    packet[4] = (to >> 24) & 0xFF;
    packet[5] = (to >> 16) & 0xFF;
    packet[6] = (to >> 8) & 0xFF;
    packet[7] = to & 0xFF;
    packet[8] = kTypeDelivered;
    transmit(packet, sizeof(packet));
}

static bool alreadySaw(uint32_t from, uint32_t messageId) {
    for (uint8_t i = 0; i < 8; i++) {
        if (seenId[i] == messageId && seenFrom[i] == from) {
            return true;
        }
    }
    seenId[seenAt] = messageId;
    seenFrom[seenAt] = from;
    seenAt = (seenAt + 1) % 8;
    return false;
}

static void onPacket(uint8_t *packet, size_t packetLen) {
    if (packetLen < 9) {
        return;
    }
    uint32_t sender = ((uint32_t)packet[0] << 24) | ((uint32_t)packet[1] << 16) |
                      ((uint32_t)packet[2] << 8) | packet[3];
    uint32_t target = ((uint32_t)packet[4] << 24) | ((uint32_t)packet[5] << 16) |
                      ((uint32_t)packet[6] << 8) | packet[7];
    uint8_t kind = packet[8];
    if (kind == kTypeDelivered) {
        if (target == phone && sender != phone) {
            Serial.printf("ACK %lu\n", (unsigned long)sender);
            blink();
        }
        return;
    }
    if (kind != kTypeMessage || packetLen < 15) {
        Serial.printf("HEARD %u bad-type\n", (unsigned)packetLen);
        return;
    }
    // The number we send as is not a reason to hide a packet. Replies often
    // arrive addressed to a different number than the one saved here.
    uint32_t messageId = ((uint32_t)packet[9] << 24) | ((uint32_t)packet[10] << 16) |
                         ((uint32_t)packet[11] << 8) | packet[12];
    uint16_t textLen = ((uint16_t)packet[13] << 8) | packet[14];
    if (textLen == 0 || textLen > kMaxText || (size_t)(15 + textLen) > packetLen) {
        Serial.printf("HEARD %u bad-len\n", (unsigned)packetLen);
        return;
    }
    if (alreadySaw(sender, messageId)) {
        return;
    }
    Serial.printf("RX %lu %lu %lu ", (unsigned long)sender, (unsigned long)target, (unsigned long)messageId);
    for (uint16_t i = 0; i < textLen; i++) {
        char c = (char)packet[15 + i];
        if (c == '\n' || c == '\r') {
            c = ' ';
        }
        Serial.write(c);
    }
    Serial.println();
    Serial.flush();
    blink();
    if (target == phone) {
        sendDelivered(sender);
    }
}

static void takePacket() {
    delayMicroseconds(500);
    size_t packetLen = radio->getPacketLength();
    if (packetLen == 0 || packetLen > 255) {
        Serial.println("HEARD 0 empty");
        listen();
        return;
    }
    uint8_t packet[256];
    int state = radio->readData(packet, packetLen);
    if (state == RADIOLIB_ERR_NONE || state == RADIOLIB_ERR_CRC_MISMATCH) {
        onPacket(packet, packetLen);
    } else {
        Serial.printf("HEARD %u read-%d\n", (unsigned)packetLen, (int)state);
    }
    listen();
}

static void pollRadio() {
    if (!radioOk || radio == nullptr) {
        return;
    }
    static uint32_t lastListen = 0;
    static uint32_t lastPa = 0;
    uint32_t now = millis();
    // Keep the GC1109 in RX even if a send left TX_EN high.
    if (now - lastPa > 1000) {
        lastPa = now;
        paReceive();
    }
    uint32_t irq = radio->getIrqFlags();
    const uint32_t rxBits = RADIOLIB_SX126X_IRQ_RX_DONE | RADIOLIB_SX126X_IRQ_CRC_ERR;
    bool dio = digitalRead(kDio1) == HIGH;
    // Phone watches DIO1. This board's TX_DONE pin is quiet, but RX_DONE can
    // still raise it — accept either the pin or the SPI flag.
    if ((irq & rxBits) != 0 || dio) {
        size_t pending = radio->getPacketLength();
        if (pending == 0 && (irq & rxBits) == 0) {
            // Pin high with no payload yet — wait, do not restart RX.
            return;
        }
        takePacket();
        lastListen = now;
        return;
    }
    if ((irq & (RADIOLIB_SX126X_IRQ_HEADER_VALID | RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED)) != 0) {
        lastListen = now;
        return;
    }
    if (now - lastListen > 8000) {
        lastListen = now;
        listen();
    }
}

#ifndef MESHCHAT_FW
#define MESHCHAT_FW "0"
#endif

static void reportFirmware() {
    Serial.printf("FW %s\n", MESHCHAT_FW);
}

static void replyId() {
    Serial.printf("ID %lu\n", (unsigned long)phone);
}

static void handleLine(char *text) {
    while (*text == ' ') {
        text++;
    }
    if (*text == '\0') {
        return;
    }
    if (strcmp(text, "PING") == 0) {
        Serial.println("PONG");
        Serial.printf("RADIO %s\n", radioOk ? "ok" : "fail");
        reportFirmware();
        replyId();
        if (radio != nullptr) {
            Serial.printf("IRQ %lu DIO %d PA %d\n",
                          (unsigned long)radio->getIrqFlags(),
                          digitalRead(kDio1),
                          digitalRead(kPaTx));
        }
        reportGps();
        return;
    }
    if (strcmp(text, "ID?") == 0) {
        replyId();
        return;
    }
    if (strncmp(text, "ID ", 3) == 0) {
        char *end = nullptr;
        unsigned long value = strtoul(text + 3, &end, 10);
        if (end == text + 3 || value == 0 || value == kBroadcast) {
            Serial.println("ERR phone");
            return;
        }
        phone = (uint32_t)value;
        prefs.putUInt("phone", phone);
        replyId();
        return;
    }
    if (strncmp(text, "FREQ ", 5) == 0) {
        float next = strtof(text + 5, nullptr);
        if (next < 150.0f || next > 960.0f) {
            Serial.println("ERR freq");
            return;
        }
        freqMhz = next;
        prefs.putFloat("freq", freqMhz);
        bool ok = beginRadio();
        Serial.printf("FREQ %.3f %s\n", freqMhz, ok ? "ok" : "fail");
        return;
    }
    if (strncmp(text, "SEND ", 5) == 0) {
        char *end = nullptr;
        unsigned long target = strtoul(text + 5, &end, 10);
        if (end == text + 5 || *end != ' ') {
            Serial.println("ERR send");
            return;
        }
        char *body = end + 1;
        while (*body == ' ') {
            body++;
        }
        if (*body == '\0') {
            Serial.println("ERR empty");
            return;
        }
        if (phone == 0) {
            Serial.println("ERR nophone");
            Serial.printf("TX %lu 0 fail\n", target);
            return;
        }
        if (strlen(body) > kMaxText) {
            Serial.println("ERR long");
            Serial.printf("TX %lu 0 fail\n", target);
            return;
        }
        uint32_t messageId = esp_random();
        if (messageId == 0) {
            messageId = 1;
        }
        bool ok = sendMessage((uint32_t)target, messageId, body);
        Serial.printf("TX %lu %lu %s\n", target, (unsigned long)messageId, ok ? "ok" : "fail");
        Serial.flush();
        return;
    }
    Serial.println("ERR unknown");
}

static void pollSerial() {
    while (Serial.available() > 0) {
        char c = (char)Serial.read();
        if (c == '\r') {
            continue;
        }
        if (c == '\n') {
            line[lineLen] = '\0';
            handleLine(line);
            lineLen = 0;
            continue;
        }
        if (lineLen + 1 < sizeof(line)) {
            line[lineLen++] = c;
        }
    }
}

void setup() {
    pinMode(kLed, OUTPUT);
    digitalWrite(kLed, LOW);
    pinMode(kPaPower, OUTPUT);
    pinMode(kPaEn, OUTPUT);
    pinMode(kPaTx, OUTPUT);
    paReceive();
    pinMode(kDio1, INPUT);
    pinMode(kBusy, INPUT);

    prefs.begin("meshchat", false);
    phone = prefs.getUInt("phone", 0);
    freqMhz = prefs.getFloat("freq", 906.875f);
    if (freqMhz < 150.0f || freqMhz > 960.0f) {
        freqMhz = 906.875f;
    }

    Serial.begin(115200);
    startGps();
    bool ok = beginRadio();
    Serial.printf("READY %lu\n", (unsigned long)phone);
    reportFirmware();
    Serial.printf("RADIO %s\n", ok ? "ok" : "fail");
    Serial.printf("FREQ %.3f\n", freqMhz);
}

void loop() {
    pollRadio();
    pollSerial();
    pollGps();
}
