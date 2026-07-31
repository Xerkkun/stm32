/*
 * ina226_capture.cpp — portado de ATmega328P a Arduino Uno R4 (Renesas RA4M1)
 *
 * Cambios respecto al original AVR:
 * - Eliminado: #include <avr/interrupt.h>
 * - Eliminado: ISR(PCINT2_vect), PIND, PCICR, PCMSK2, PCIFR, _BV(), digitalPinToBitMask()
 * - Añadido:   attachInterrupt(digitalPinToInterrupt(pin), ..., CHANGE) para cada pin marcador
 *              con funciones ISR individuales que comparten la cola de eventos.
 * - WIRE_HAS_TIMEOUT: Wire.setWireTimeout() sigue siendo compatible con Uno R4.
 * - Todo lo demás (protocolo PWRCTL/1, INA14/1, framing binario, CRC-8) permanece intacto.
 */

#include "ina226_capture.h"

#include <Arduino.h>
#include <Wire.h>
#include <stdint.h>
#include <string.h>

#include "controller_config.h"

namespace {

constexpr uint8_t INA226_REGISTER_CONFIG       = 0x00U;
constexpr uint8_t INA226_REGISTER_SHUNT        = 0x01U;
constexpr uint8_t INA226_REGISTER_BUS          = 0x02U;
constexpr uint8_t INA226_REGISTER_MASK_ENABLE  = 0x06U;
constexpr uint8_t INA226_REGISTER_MANUFACTURER = 0xFEU;
constexpr uint8_t INA226_REGISTER_DIE          = 0xFFU;
constexpr uint16_t INA226_EXPECTED_MANUFACTURER = 0x5449U;
constexpr uint16_t INA226_EXPECTED_DIE          = 0x2260U;

/*
 * AVG=1, VBUSCT=140 us, VSHCT=140 us, continuous shunt+bus.
 * A complete conversion pair takes 280 us; the audited stream reads at 2 ms.
 */
constexpr uint16_t INA226_CONFIG_140US_CONTINUOUS = 0x0007U;
constexpr uint16_t INA226_MASK_CONVERSION_READY   = 1U << 3U;
constexpr uint16_t INA226_MASK_MATH_OVERFLOW      = 1U << 2U;

constexpr uint8_t FRAME_SYNC_0       = 0xA5U;
constexpr uint8_t FRAME_SYNC_1       = 0x5AU;
constexpr uint8_t FRAME_SIZE         = 14U;
constexpr uint8_t FRAME_TYPE_SAMPLE  = 0x40U;
constexpr uint8_t FRAME_TYPE_EDGE    = 0x80U;
constexpr uint8_t FRAME_TYPE_END     = 0xC0U;

constexpr uint8_t SAMPLE_FLAG_CONVERSION_READY = 1U << 0U;
constexpr uint8_t SAMPLE_FLAG_MATH_OVERFLOW    = 1U << 1U;
constexpr uint8_t SAMPLE_FLAG_ENERGY_HIGH      = 1U << 2U;
constexpr uint8_t SAMPLE_FLAG_I2C_OK           = 1U << 3U;

constexpr uint8_t EDGE_FLAG_LEVEL           = 1U << 0U;
constexpr uint8_t EDGE_FLAG_CLOCK_REFERENCE = 1U << 1U;

constexpr uint8_t END_FLAG_COMPLETE               = 1U << 0U;
constexpr uint8_t END_FLAG_STOPPED                = 1U << 1U;
constexpr uint8_t END_FLAG_TIMEOUT                = 1U << 2U;
constexpr uint8_t END_FLAG_I2C_ERROR              = 1U << 3U;
constexpr uint8_t END_FLAG_EDGE_OVERFLOW          = 1U << 4U;
constexpr uint8_t END_FLAG_TIMING_OR_EDGE_ANOMALY = 1U << 5U;

constexpr uint8_t MARKER_KIND_ENERGY          = 0U;
constexpr uint8_t MARKER_KIND_CLOCK           = 1U;
constexpr uint8_t MARKER_QUEUE_CAPACITY       = 8U;
constexpr uint8_t CAPTURE_CONTROL_BUFFER_SIZE = 80U;

struct ChannelConfig {
    const char *name;
    uint8_t     address;
    uint16_t    shunt_milliohms;
    uint8_t     energy_pin;
    uint8_t     clock_pin;
};

struct MarkerEvent {
    uint32_t timestamp_us;
    uint16_t sequence;
    uint8_t  kind;
    uint8_t  level;
};

const ChannelConfig CHANNELS[2] = {
    {
        "F746",
        PC_INA_CH1_ADDRESS,
        PC_INA_CH1_SHUNT_MILLIOHMS,
        PC_ENERGY_MARKER_CH1_PIN,
        PC_CLOCK_MARKER_CH1_PIN,
    },
    {
        "H755",
        PC_INA_CH2_ADDRESS,
        PC_INA_CH2_SHUNT_MILLIOHMS,
        PC_ENERGY_MARKER_CH2_PIN,
        PC_CLOCK_MARKER_CH2_PIN,
    },
};

volatile MarkerEvent marker_queue[MARKER_QUEUE_CAPACITY];
volatile uint8_t  marker_queue_head     = 0U;
volatile uint8_t  marker_queue_tail     = 0U;
volatile bool     marker_queue_overflow = false;
volatile uint16_t capture_sequence      = 0U;
volatile bool     capture_active        = false;

/*
 * Uno R4 port: almacena los numeros de pin activos en lugar de mascaras de
 * bit AVR. El ISR lee el estado del pin con digitalRead(), que es seguro en
 * el RA4M1 (no se accede directamente a PIND ni a registros de puerto).
 */
volatile uint8_t marker_energy_pin  = 0U;
volatile uint8_t marker_clock_pin   = 0U;
volatile bool    markers_attached   = false;

const ChannelConfig *capture_channel        = nullptr;
char     capture_request_id[65]             = {0};
uint16_t capture_pre_target                 = 0U;
uint16_t capture_post_target                = 0U;
uint32_t capture_timeout_ms                 = 0UL;
uint32_t capture_started_ms                 = 0UL;
uint32_t next_sample_us                     = 0UL;
uint32_t total_sample_count                 = 0UL;
uint32_t post_sample_count                  = 0UL;
uint16_t i2c_error_count                    = 0U;
uint16_t late_slot_count                    = 0U;
bool     energy_rise_seen                   = false;
bool     energy_fall_seen                   = false;
uint32_t energy_fall_timestamp_us           = 0UL;
bool     capture_stop_requested             = false;
bool     capture_protocol_anomaly           = false;
char     capture_control_buffer[CAPTURE_CONTROL_BUFFER_SIZE];
uint8_t  capture_control_length             = 0U;
bool     capture_control_overflow           = false;

// ---------------------------------------------------------------------------
// Helper compartido por los dos ISR de marcador
// ---------------------------------------------------------------------------
void pushMarkerEvent(uint8_t kind, uint8_t level, uint32_t timestamp_us)
{
    if (!capture_active) {
        return;
    }
    const uint8_t next_head = static_cast<uint8_t>(
        (marker_queue_head + 1U) % MARKER_QUEUE_CAPACITY);
    if (next_head == marker_queue_tail) {
        marker_queue_overflow = true;
        return;
    }
    volatile MarkerEvent &event = marker_queue[marker_queue_head];
    event.timestamp_us          = timestamp_us;
    event.sequence              = capture_sequence;
    event.kind                  = kind;
    event.level                 = level;
    marker_queue_head           = next_head;
}

// ---------------------------------------------------------------------------
// Utilidades de framing
// ---------------------------------------------------------------------------
uint8_t crc8(const uint8_t *bytes, uint8_t length)
{
    uint8_t crc = 0U;
    for (uint8_t index = 0U; index < length; ++index) {
        crc ^= bytes[index];
        for (uint8_t bit = 0U; bit < 8U; ++bit) {
            crc = (crc & 0x80U)
                ? static_cast<uint8_t>((crc << 1U) ^ 0x07U)
                : static_cast<uint8_t>(crc << 1U);
        }
    }
    return crc;
}

void putUint16Le(uint8_t *destination, uint16_t value)
{
    destination[0] = static_cast<uint8_t>(value & 0xFFU);
    destination[1] = static_cast<uint8_t>((value >> 8U) & 0xFFU);
}

void putUint32Le(uint8_t *destination, uint32_t value)
{
    destination[0] = static_cast<uint8_t>(value & 0xFFUL);
    destination[1] = static_cast<uint8_t>((value >> 8U) & 0xFFUL);
    destination[2] = static_cast<uint8_t>((value >> 16U) & 0xFFUL);
    destination[3] = static_cast<uint8_t>((value >> 24U) & 0xFFUL);
}

void emitFrame(
    uint8_t  kind_and_flags,
    uint16_t sequence,
    uint32_t timestamp_us,
    uint16_t value_0,
    uint16_t value_1)
{
    uint8_t frame[FRAME_SIZE];
    frame[0]  = FRAME_SYNC_0;
    frame[1]  = FRAME_SYNC_1;
    frame[2]  = kind_and_flags;
    putUint16Le(&frame[3], sequence);
    putUint32Le(&frame[5], timestamp_us);
    putUint16Le(&frame[9], value_0);
    putUint16Le(&frame[11], value_1);
    frame[13] = crc8(frame, FRAME_SIZE - 1U);
    Serial.write(frame, FRAME_SIZE);
}

// ---------------------------------------------------------------------------
// Validacion de entrada ASCII
// ---------------------------------------------------------------------------
bool validRequestId(const char *text)
{
    if ((text == nullptr) || (*text == '\0')) {
        return false;
    }
    uint8_t length = 0U;
    for (const char *cursor = text; *cursor != '\0'; ++cursor) {
        const char value = *cursor;
        const bool valid =
            ((value >= 'A') && (value <= 'Z')) ||
            ((value >= 'a') && (value <= 'z')) ||
            ((value >= '0') && (value <= '9')) ||
            (value == '_') ||
            (value == '-');
        if (!valid || (length >= 64U)) {
            return false;
        }
        ++length;
    }
    return length > 0U;
}

bool parseUint32(const char *text, uint32_t *value)
{
    if ((text == nullptr) || (*text == '\0')) {
        return false;
    }
    uint32_t parsed = 0UL;
    for (const char *cursor = text; *cursor != '\0'; ++cursor) {
        if ((*cursor < '0') || (*cursor > '9')) {
            return false;
        }
        const uint8_t digit = static_cast<uint8_t>(*cursor - '0');
        if (parsed > ((UINT32_MAX - digit) / 10UL)) {
            return false;
        }
        parsed = parsed * 10UL + digit;
    }
    *value = parsed;
    return true;
}

const ChannelConfig *findChannel(const char *text)
{
    if ((strcmp(text, "F746") == 0) || (strcmp(text, "CH1") == 0)) {
        return &CHANNELS[0];
    }
    if ((strcmp(text, "H755") == 0) || (strcmp(text, "CH2") == 0)) {
        return &CHANNELS[1];
    }
    return nullptr;
}

void emitInaError(
    const char *request_id,
    const char *channel,
    const __FlashStringHelper *code)
{
    Serial.print(F("ERR "));
    Serial.print(request_id);
    Serial.print(' ');
    Serial.print(channel);
    Serial.print(' ');
    Serial.println(code);
}

// ---------------------------------------------------------------------------
// I2C
// ---------------------------------------------------------------------------
bool readRegister(uint8_t address, uint8_t reg, uint16_t *value)
{
    Wire.beginTransmission(address);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0U) {
        return false;
    }
    if (Wire.requestFrom(address, static_cast<uint8_t>(2U)) != 2U) {
        return false;
    }
    const uint16_t high = static_cast<uint8_t>(Wire.read());
    const uint16_t low  = static_cast<uint8_t>(Wire.read());
    *value = static_cast<uint16_t>((high << 8U) | low);
    return true;
}

bool writeRegister(uint8_t address, uint8_t reg, uint16_t value)
{
    Wire.beginTransmission(address);
    Wire.write(reg);
    Wire.write(static_cast<uint8_t>((value >> 8U) & 0xFFU));
    Wire.write(static_cast<uint8_t>(value & 0xFFU));
    return Wire.endTransmission() == 0U;
}

bool readIdentity(
    const ChannelConfig &channel,
    uint16_t *manufacturer,
    uint16_t *die)
{
    return
        readRegister(channel.address, INA226_REGISTER_MANUFACTURER, manufacturer) &&
        readRegister(channel.address, INA226_REGISTER_DIE, die);
}

bool configureSensor(
    const ChannelConfig &channel,
    uint16_t *manufacturer,
    uint16_t *die)
{
    uint16_t observed_config = 0U;
    return
        readIdentity(channel, manufacturer, die) &&
        (*manufacturer == INA226_EXPECTED_MANUFACTURER) &&
        (*die == INA226_EXPECTED_DIE) &&
        writeRegister(
            channel.address,
            INA226_REGISTER_CONFIG,
            INA226_CONFIG_140US_CONTINUOUS) &&
        readRegister(
            channel.address,
            INA226_REGISTER_CONFIG,
            &observed_config) &&
        (observed_config == INA226_CONFIG_140US_CONTINUOUS);
}

// ---------------------------------------------------------------------------
// Cola de marcadores
// ---------------------------------------------------------------------------
void clearMarkerQueue()
{
    noInterrupts();
    marker_queue_head     = 0U;
    marker_queue_tail     = 0U;
    marker_queue_overflow = false;
    interrupts();
}

bool popMarkerEvent(MarkerEvent *event)
{
    bool present = false;
    noInterrupts();
    if (marker_queue_tail != marker_queue_head) {
        const volatile MarkerEvent &source = marker_queue[marker_queue_tail];
        event->timestamp_us = source.timestamp_us;
        event->sequence     = source.sequence;
        event->kind         = source.kind;
        event->level        = source.level;
        marker_queue_tail = static_cast<uint8_t>(
            (marker_queue_tail + 1U) % MARKER_QUEUE_CAPACITY);
        present = true;
    }
    interrupts();
    return present;
}

// ---------------------------------------------------------------------------
// Interrupciones de marcador — Uno R4 (Renesas RA4M1)
//
// El ATmega328P usaba ISR(PCINT2_vect) que leía PIND y PCICR/PCMSK2 para
// detectar cambios en un grupo de pines. El RA4M1 no tiene Pin-Change
// Interrupts por puerto; todos sus pines GPIO admiten interrupciones CHANGE
// individuales a través de attachInterrupt().
//
// Se instalan dos handlers independientes — uno por pin marcador — que
// comparten la cola circular de eventos y el contador de secuencia. La
// semántica es equivalente al ISR original.
// ---------------------------------------------------------------------------
void energyISR()
{
    const uint32_t ts    = micros();
    const uint8_t  level = static_cast<uint8_t>(digitalRead(marker_energy_pin));
    pushMarkerEvent(MARKER_KIND_ENERGY, level, ts);
}

void clockISR()
{
    const uint32_t ts    = micros();
    const uint8_t  level = static_cast<uint8_t>(digitalRead(marker_clock_pin));
    pushMarkerEvent(MARKER_KIND_CLOCK, level, ts);
}

void enableMarkerInterrupts(const ChannelConfig &channel)
{
    noInterrupts();
    marker_energy_pin = channel.energy_pin;
    marker_clock_pin  = channel.clock_pin;
    interrupts();

    attachInterrupt(digitalPinToInterrupt(channel.energy_pin), energyISR, CHANGE);
    attachInterrupt(digitalPinToInterrupt(channel.clock_pin),  clockISR,  CHANGE);
    markers_attached = true;
}

void disableMarkerInterrupts()
{
    if (!markers_attached) {
        return;
    }
    detachInterrupt(digitalPinToInterrupt(marker_energy_pin));
    detachInterrupt(digitalPinToInterrupt(marker_clock_pin));
    noInterrupts();
    marker_energy_pin = 0U;
    marker_clock_pin  = 0U;
    markers_attached  = false;
    interrupts();
}

// ---------------------------------------------------------------------------
// Drenaje de cola y muestreo
// ---------------------------------------------------------------------------
void drainMarkerEvents()
{
    MarkerEvent event;
    while (popMarkerEvent(&event)) {
        uint8_t flags = (event.level != 0U) ? EDGE_FLAG_LEVEL : 0U;
        if (event.kind == MARKER_KIND_CLOCK) {
            flags |= EDGE_FLAG_CLOCK_REFERENCE;
        }
        emitFrame(
            static_cast<uint8_t>(FRAME_TYPE_EDGE | flags),
            event.sequence,
            event.timestamp_us,
            0U,
            0U);

        if (event.kind != MARKER_KIND_ENERGY) {
            continue;
        }
        if (event.level != 0U) {
            if (energy_rise_seen || energy_fall_seen) {
                capture_protocol_anomaly = true;
                continue;
            }
            energy_rise_seen = true;
            if (total_sample_count < capture_pre_target) {
                capture_protocol_anomaly = true;
            }
        } else {
            if (!energy_rise_seen || energy_fall_seen) {
                capture_protocol_anomaly = true;
                continue;
            }
            energy_fall_seen         = true;
            energy_fall_timestamp_us = event.timestamp_us;
        }
    }
}

void emitSample()
{
    const uint32_t scheduled_us = next_sample_us;
    const uint32_t now_us       = micros();
    if (static_cast<int32_t>(now_us - scheduled_us) < 0) {
        return;
    }

    const uint32_t periods_late =
        static_cast<uint32_t>(now_us - scheduled_us) /
        PC_INA_SAMPLE_PERIOD_US;
    if (periods_late > 0UL) {
        const uint32_t remaining =
            static_cast<uint32_t>(UINT16_MAX - late_slot_count);
        late_slot_count = static_cast<uint16_t>(
            late_slot_count +
            ((periods_late > remaining) ? remaining : periods_late));
        noInterrupts();
        capture_sequence = static_cast<uint16_t>(
            capture_sequence + static_cast<uint16_t>(periods_late));
        interrupts();
        next_sample_us += periods_late * PC_INA_SAMPLE_PERIOD_US;
    }

    const uint16_t sequence        = capture_sequence;
    const uint32_t read_started_us = micros();
    uint16_t shunt_raw   = 0U;
    uint16_t bus_raw     = 0U;
    uint16_t mask_enable = 0U;
    const bool i2c_ok =
        readRegister(capture_channel->address, INA226_REGISTER_SHUNT,       &shunt_raw) &&
        readRegister(capture_channel->address, INA226_REGISTER_BUS,         &bus_raw) &&
        readRegister(capture_channel->address, INA226_REGISTER_MASK_ENABLE, &mask_enable);

    uint8_t flags = 0U;
    if (i2c_ok) {
        flags |= SAMPLE_FLAG_I2C_OK;
        if ((mask_enable & INA226_MASK_CONVERSION_READY) != 0U) {
            flags |= SAMPLE_FLAG_CONVERSION_READY;
        }
        if ((mask_enable & INA226_MASK_MATH_OVERFLOW) != 0U) {
            flags |= SAMPLE_FLAG_MATH_OVERFLOW;
        }
    } else if (i2c_error_count < UINT16_MAX) {
        ++i2c_error_count;
    }

    /*
     * Uno R4 port: en el original se leía PIND directamente para el nivel
     * instantáneo del pin de energía. Aquí se usa digitalRead(), que es la
     * API portable correcta para el RA4M1.
     */
    if (digitalRead(marker_energy_pin) == HIGH) {
        flags |= SAMPLE_FLAG_ENERGY_HIGH;
    }

    emitFrame(
        static_cast<uint8_t>(FRAME_TYPE_SAMPLE | flags),
        sequence,
        read_started_us,
        bus_raw,
        shunt_raw);

    noInterrupts();
    ++capture_sequence;
    interrupts();
    ++total_sample_count;
    next_sample_us += PC_INA_SAMPLE_PERIOD_US;
    if (energy_fall_seen &&
        (static_cast<int32_t>(read_started_us - energy_fall_timestamp_us) > 0)) {
        ++post_sample_count;
    }
}

// ---------------------------------------------------------------------------
// Control de captura ASCII
// ---------------------------------------------------------------------------
void processCaptureControlLine(char *line)
{
    char *save_pointer = nullptr;
    char *command    = strtok_r(line, " ", &save_pointer);
    char *request_id = strtok_r(nullptr, " ", &save_pointer);
    char *extra      = strtok_r(nullptr, " ", &save_pointer);
    if ((command == nullptr) ||
        (strcmp(command, "STOP") != 0) ||
        (request_id == nullptr) ||
        (strcmp(request_id, capture_request_id) != 0) ||
        (extra != nullptr)) {
        capture_protocol_anomaly = true;
        return;
    }
    capture_stop_requested = true;
}

void pollCaptureControl()
{
    while (Serial.available() > 0) {
        const char received = static_cast<char>(Serial.read());
        if (received == '\r') {
            continue;
        }
        if (received == '\n') {
            if (capture_control_overflow) {
                capture_protocol_anomaly = true;
            } else if (capture_control_length > 0U) {
                capture_control_buffer[capture_control_length] = '\0';
                processCaptureControlLine(capture_control_buffer);
            }
            capture_control_length   = 0U;
            capture_control_overflow = false;
            continue;
        }
        if (capture_control_overflow) {
            continue;
        }
        if (capture_control_length >= (CAPTURE_CONTROL_BUFFER_SIZE - 1U)) {
            capture_control_overflow = true;
            continue;
        }
        capture_control_buffer[capture_control_length++] = received;
    }
}

void finishCapture(bool complete, bool timed_out)
{
    disableMarkerInterrupts();
    drainMarkerEvents();

    uint8_t flags = complete ? END_FLAG_COMPLETE : 0U;
    if (capture_stop_requested)   { flags |= END_FLAG_STOPPED; }
    if (timed_out)                { flags |= END_FLAG_TIMEOUT; }
    if (i2c_error_count > 0U)     { flags |= END_FLAG_I2C_ERROR; }
    if (marker_queue_overflow)    { flags |= END_FLAG_EDGE_OVERFLOW; }
    if ((late_slot_count > 0U) || capture_protocol_anomaly) {
        flags |= END_FLAG_TIMING_OR_EDGE_ANOMALY;
    }
    emitFrame(
        static_cast<uint8_t>(FRAME_TYPE_END | flags),
        capture_sequence,
        micros(),
        i2c_error_count,
        late_slot_count);
    Serial.flush();
    capture_active           = false;
    capture_control_length   = 0U;
    capture_control_overflow = false;
}

// ---------------------------------------------------------------------------
// Comandos INA_STATUS e INA_READ
// ---------------------------------------------------------------------------
void emitInaStatus(const ChannelConfig &channel)
{
    uint16_t manufacturer = 0U;
    uint16_t die          = 0U;
    const bool i2c_ok = readIdentity(channel, &manufacturer, &die);
    Serial.print(F("INASTATUS INACAP=1 CHANNEL="));
    Serial.print(channel.name);
    Serial.print(F(" ADDR=0x"));
    Serial.print(channel.address, HEX);
    Serial.print(F(" I2C_OK="));
    Serial.print(i2c_ok ? 1 : 0);
    Serial.print(F(" MFG=0x"));
    Serial.print(manufacturer, HEX);
    Serial.print(F(" DIE=0x"));
    Serial.print(die, HEX);
    Serial.print(F(" SHUNT_MOHM="));
    Serial.print(channel.shunt_milliohms);
    Serial.print(F(" SHUNT_PHYSICALLY_VERIFIED=0"));
    Serial.print(F(" ENERGY_MARKER="));
    Serial.print(digitalRead(channel.energy_pin));
    Serial.print(F(" CLOCK_MARKER="));
    Serial.print(digitalRead(channel.clock_pin));
    Serial.print(F(" PERIOD_US="));
    Serial.print(PC_INA_SAMPLE_PERIOD_US);
    Serial.print(F(" FRAME=INA14/1 BAUD="));
    Serial.println(PC_SERIAL_BAUD);
}

void emitInaRead(const ChannelConfig &channel)
{
    uint16_t manufacturer = 0U;
    uint16_t die          = 0U;
    if (!configureSensor(channel, &manufacturer, &die)) {
        emitInaError("0", channel.name, F("INA_ID_OR_CONFIG"));
        return;
    }
    delay(1U);
    const uint32_t timestamp_us = micros();
    uint16_t shunt_raw   = 0U;
    uint16_t bus_raw     = 0U;
    uint16_t mask_enable = 0U;
    const bool i2c_ok =
        readRegister(channel.address, INA226_REGISTER_SHUNT,       &shunt_raw) &&
        readRegister(channel.address, INA226_REGISTER_BUS,         &bus_raw) &&
        readRegister(channel.address, INA226_REGISTER_MASK_ENABLE, &mask_enable);
    if (!i2c_ok) {
        emitInaError("0", channel.name, F("INA_READ"));
        return;
    }
    Serial.print(F("INAREAD CHANNEL="));
    Serial.print(channel.name);
    Serial.print(F(" ADDR=0x"));
    Serial.print(channel.address, HEX);
    Serial.print(F(" TIMESTAMP_US="));
    Serial.print(timestamp_us);
    Serial.print(F(" BUS_RAW="));
    Serial.print(bus_raw);
    Serial.print(F(" SHUNT_RAW="));
    Serial.print(static_cast<int16_t>(shunt_raw));
    Serial.print(F(" CNVR="));
    Serial.print((mask_enable & INA226_MASK_CONVERSION_READY) != 0U ? 1 : 0);
    Serial.print(F(" OVF="));
    Serial.println((mask_enable & INA226_MASK_MATH_OVERFLOW) != 0U ? 1 : 0);
}

// ---------------------------------------------------------------------------
// ARM — inicia captura binaria
// ---------------------------------------------------------------------------
void armCapture(
    const char *request_id,
    const ChannelConfig &channel,
    uint16_t pre_samples,
    uint16_t post_samples,
    uint32_t timeout_ms)
{
    if (digitalRead(channel.energy_pin) != LOW) {
        emitInaError(request_id, channel.name, F("MARKER_HIGH"));
        return;
    }

    uint16_t manufacturer = 0U;
    uint16_t die          = 0U;
    if (!configureSensor(channel, &manufacturer, &die)) {
        emitInaError(request_id, channel.name, F("INA_ID_OR_CONFIG"));
        return;
    }

    capture_channel  = &channel;
    strncpy(capture_request_id, request_id, sizeof(capture_request_id) - 1U);
    capture_request_id[sizeof(capture_request_id) - 1U] = '\0';
    capture_pre_target        = pre_samples;
    capture_post_target       = post_samples;
    capture_timeout_ms        = timeout_ms;
    capture_sequence          = 0U;
    total_sample_count        = 0UL;
    post_sample_count         = 0UL;
    i2c_error_count           = 0U;
    late_slot_count           = 0U;
    energy_rise_seen          = false;
    energy_fall_seen          = false;
    energy_fall_timestamp_us  = 0UL;
    capture_stop_requested    = false;
    capture_protocol_anomaly  = false;
    capture_control_length    = 0U;
    capture_control_overflow  = false;
    clearMarkerQueue();

    Serial.print(F("ARMED "));
    Serial.print(request_id);
    Serial.print(' ');
    Serial.print(channel.name);
    Serial.print(F(" INACAP=1 ADDR=0x"));
    Serial.print(channel.address, HEX);
    Serial.print(F(" MFG=0x"));
    Serial.print(manufacturer, HEX);
    Serial.print(F(" DIE=0x"));
    Serial.print(die, HEX);
    Serial.print(F(" PERIOD_US="));
    Serial.print(PC_INA_SAMPLE_PERIOD_US);
    Serial.print(F(" PRE="));
    Serial.print(pre_samples);
    Serial.print(F(" POST="));
    Serial.print(post_samples);
    Serial.print(F(" TIMEOUT_MS="));
    Serial.print(timeout_ms);
    Serial.print(F(" SHUNT_MOHM="));
    Serial.print(channel.shunt_milliohms);
    Serial.println(F(" SHUNT_MARKING=R100 FRAME=INA14/1"));
    Serial.flush();

    capture_active     = true;
    enableMarkerInterrupts(channel);
    capture_started_ms = millis();
    next_sample_us     = micros();
}

}  // namespace

// ---------------------------------------------------------------------------
// API publica
// ---------------------------------------------------------------------------
void ina226CaptureInitialize()
{
    pinMode(PC_ENERGY_MARKER_CH1_PIN, INPUT);
    pinMode(PC_ENERGY_MARKER_CH2_PIN, INPUT);
    pinMode(PC_CLOCK_MARKER_CH1_PIN,  INPUT);
    pinMode(PC_CLOCK_MARKER_CH2_PIN,  INPUT);
    Wire.begin();
    Wire.setClock(PC_INA_I2C_CLOCK_HZ);
#if defined(WIRE_HAS_TIMEOUT)
    Wire.setWireTimeout(2500UL, true);
#endif
}

bool ina226CaptureActive()
{
    return capture_active;
}

void ina226CapturePoll()
{
    if (!capture_active) {
        return;
    }

    pollCaptureControl();
    drainMarkerEvents();

    const bool timed_out =
        static_cast<uint32_t>(millis() - capture_started_ms) >=
        capture_timeout_ms;
    if (capture_stop_requested || timed_out || capture_protocol_anomaly ||
        marker_queue_overflow) {
        finishCapture(false, timed_out);
        return;
    }

    emitSample();
    drainMarkerEvents();

    if (energy_fall_seen && (post_sample_count >= capture_post_target)) {
        finishCapture(true, false);
    }
}

bool ina226HandleAsciiCommand(char *command, char **save_pointer)
{
    if (strcmp(command, "INA_STATUS") == 0) {
        char *channel_text = strtok_r(nullptr, " ", save_pointer);
        char *extra        = strtok_r(nullptr, " ", save_pointer);
        if ((channel_text == nullptr) || (extra != nullptr)) {
            emitInaError("0", "0", F("BAD_ARITY"));
            return true;
        }
        const ChannelConfig *channel = findChannel(channel_text);
        if (channel == nullptr) {
            emitInaError("0", "0", F("BAD_CHANNEL"));
            return true;
        }
        emitInaStatus(*channel);
        return true;
    }

    if (strcmp(command, "INA_READ") == 0) {
        char *channel_text = strtok_r(nullptr, " ", save_pointer);
        char *extra        = strtok_r(nullptr, " ", save_pointer);
        if ((channel_text == nullptr) || (extra != nullptr)) {
            emitInaError("0", "0", F("BAD_ARITY"));
            return true;
        }
        const ChannelConfig *channel = findChannel(channel_text);
        if (channel == nullptr) {
            emitInaError("0", "0", F("BAD_CHANNEL"));
            return true;
        }
        emitInaRead(*channel);
        return true;
    }

    if (strcmp(command, "STOP") == 0) {
        emitInaError("0", "0", F("NOT_ACTIVE"));
        return true;
    }

    if (strcmp(command, "ARM") != 0) {
        return false;
    }

    char *request_id    = strtok_r(nullptr, " ", save_pointer);
    char *channel_text  = strtok_r(nullptr, " ", save_pointer);
    char *pre_text      = strtok_r(nullptr, " ", save_pointer);
    char *post_text     = strtok_r(nullptr, " ", save_pointer);
    char *timeout_text  = strtok_r(nullptr, " ", save_pointer);
    char *shunt_marking = strtok_r(nullptr, " ", save_pointer);
    char *extra         = strtok_r(nullptr, " ", save_pointer);
    if ((request_id    == nullptr) ||
        (channel_text  == nullptr) ||
        (pre_text      == nullptr) ||
        (post_text     == nullptr) ||
        (timeout_text  == nullptr) ||
        (shunt_marking == nullptr) ||
        (extra         != nullptr)) {
        emitInaError("0", "0", F("BAD_ARITY"));
        return true;
    }
    if (!validRequestId(request_id)) {
        emitInaError("0", "0", F("BAD_REQUEST_ID"));
        return true;
    }

    const ChannelConfig *channel = findChannel(channel_text);
    if (channel == nullptr) {
        emitInaError(request_id, "0", F("BAD_CHANNEL"));
        return true;
    }
    if (strcmp(shunt_marking, "R100") != 0) {
        emitInaError(request_id, channel->name, F("R100_NOT_CONFIRMED"));
        return true;
    }

    uint32_t pre_samples  = 0UL;
    uint32_t post_samples = 0UL;
    uint32_t timeout_ms   = 0UL;
    if (!parseUint32(pre_text, &pre_samples) ||
        !parseUint32(post_text, &post_samples) ||
        !parseUint32(timeout_text, &timeout_ms)) {
        emitInaError(request_id, channel->name, F("BAD_CAPTURE_RANGE"));
        return true;
    }
    if ((pre_samples  < PC_INA_MIN_IDLE_SAMPLES) ||
        (pre_samples  > PC_INA_MAX_IDLE_SAMPLES) ||
        (post_samples < PC_INA_MIN_IDLE_SAMPLES) ||
        (post_samples > PC_INA_MAX_IDLE_SAMPLES) ||
        (timeout_ms   < PC_INA_MIN_TIMEOUT_MS)   ||
        (timeout_ms   > PC_INA_MAX_TIMEOUT_MS)) {
        emitInaError(request_id, channel->name, F("CAPTURE_RANGE"));
        return true;
    }

    armCapture(
        request_id,
        *channel,
        static_cast<uint16_t>(pre_samples),
        static_cast<uint16_t>(post_samples),
        timeout_ms);
    return true;
}
