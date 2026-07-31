#include <Arduino.h>
#include <stdint.h>
#include <string.h>

#include "controller_config.h"
#include "ina226_capture.h"

/*
 * Standalone two-channel power controller for the physical STM32 campaign.
 *
 * Wire protocol PWRCTL/1 is intentionally independent from FCC1. A successful
 * host build or parser test is not evidence that either Nucleo lost power.
 */

enum TargetMask : uint8_t {
    TARGET_NONE = 0U,
    TARGET_CH1 = 1U,
    TARGET_CH2 = 2U,
    TARGET_ALL = TARGET_CH1 | TARGET_CH2
};

struct SenseSnapshot {
    uint16_t millivolts[2];
};

static const uint8_t RELAY_PINS[2] = {
    PC_RELAY_CH1_PIN,
    PC_RELAY_CH2_PIN
};
static const uint8_t SENSE_PINS[2] = {
    PC_SENSE_CH1_PIN,
    PC_SENSE_CH2_PIN
};

static bool relay_powered[2] = {false, false};
static char command_buffer[PC_COMMAND_BUFFER_SIZE];
static uint8_t command_length = 0U;
static bool command_overflow = false;

static void initializeFailOffOutputs();
static void setRelayPower(uint8_t channel_index, bool powered);
static void failOff(TargetMask target);
static bool targetIncludes(TargetMask target, uint8_t channel_index);
static bool parseChannel(
    const char *text,
    TargetMask *target,
    const char **canonical_channel);
static bool validRequestId(const char *text);
static bool parseUint32(const char *text, uint32_t *value);
static uint16_t readRailMillivolts(uint8_t channel_index);
static SenseSnapshot readSenseSnapshot();
static const char *senseState(uint16_t millivolts);
static bool selectedRailsOff(TargetMask target, const SenseSnapshot &snapshot);
static bool selectedRailsOn(TargetMask target, const SenseSnapshot &snapshot);
static uint16_t selectedOffMillivolts(
    TargetMask target,
    const SenseSnapshot &snapshot);
static uint16_t selectedOnMillivolts(
    TargetMask target,
    const SenseSnapshot &snapshot);
static bool waitForStableRailState(
    TargetMask target,
    bool want_on,
    uint32_t stable_ms,
    uint32_t timeout_ms,
    SenseSnapshot *last_snapshot);
static bool holdConfirmedOff(
    TargetMask target,
    uint32_t confirmed_off_started_ms,
    uint32_t requested_off_ms,
    SenseSnapshot *last_snapshot);
static void printSnapshot(const SenseSnapshot &snapshot);
static void emitStatus(const __FlashStringHelper *state);
static void emitError(
    const char *request_id,
    const char *channel,
    const __FlashStringHelper *code);
static void emitAck(
    const char *request_id,
    const char *channel,
    bool off_ok,
    bool on_ok,
    uint16_t off_mv,
    uint16_t on_mv);
static void executeCycle(
    const char *request_id,
    const char *channel,
    TargetMask target,
    uint32_t off_ms);
static void processCommand(char *line);
static void pollSerial();

void setup()
{
    initializeFailOffOutputs();
    pinMode(PC_SENSE_CH1_PIN, INPUT);
    pinMode(PC_SENSE_CH2_PIN, INPUT);

    Serial.begin(PC_SERIAL_BAUD);
    ina226CaptureInitialize();
    delay(20U);
    emitStatus(F("BOOT_FAIL_OFF"));
}

void loop()
{
    if (ina226CaptureActive()) {
        ina226CapturePoll();
    } else {
        pollSerial();
    }
}

static void initializeFailOffOutputs()
{
#if PC_RELAY_ACTIVE_LOW
    const uint8_t inactive_level = HIGH;
#else
    const uint8_t inactive_level = LOW;
#endif

    /*
     * Load the inactive output latch before changing DDR. The external bias
     * documented in README.md covers reset and bootloader time before setup().
     */
    digitalWrite(PC_RELAY_CH1_PIN, inactive_level);
    digitalWrite(PC_RELAY_CH2_PIN, inactive_level);
    pinMode(PC_RELAY_CH1_PIN, OUTPUT);
    pinMode(PC_RELAY_CH2_PIN, OUTPUT);
    relay_powered[0] = false;
    relay_powered[1] = false;
}

static void setRelayPower(uint8_t channel_index, bool powered)
{
#if PC_RELAY_ACTIVE_LOW
    const uint8_t active_level = LOW;
    const uint8_t inactive_level = HIGH;
#else
    const uint8_t active_level = HIGH;
    const uint8_t inactive_level = LOW;
#endif

    digitalWrite(
        RELAY_PINS[channel_index],
        powered ? active_level : inactive_level);
    relay_powered[channel_index] = powered;
}

static void failOff(TargetMask target)
{
    for (uint8_t index = 0U; index < 2U; ++index) {
        if (targetIncludes(target, index)) {
            setRelayPower(index, false);
        }
    }
}

static bool targetIncludes(TargetMask target, uint8_t channel_index)
{
    const uint8_t mask = (channel_index == 0U) ? TARGET_CH1 : TARGET_CH2;
    return (static_cast<uint8_t>(target) & mask) != 0U;
}

static bool parseChannel(
    const char *text,
    TargetMask *target,
    const char **canonical_channel)
{
    if ((strcmp(text, "F746") == 0) ||
        (strcmp(text, "CH1") == 0)) {
        *target = TARGET_CH1;
        *canonical_channel = "F746";
        return true;
    }
    if ((strcmp(text, "H755") == 0) ||
        (strcmp(text, "CH2") == 0)) {
        *target = TARGET_CH2;
        *canonical_channel = "H755";
        return true;
    }
    if (strcmp(text, "ALL") == 0) {
        *target = TARGET_ALL;
        *canonical_channel = "ALL";
        return true;
    }
    *target = TARGET_NONE;
    *canonical_channel = "0";
    return false;
}

static bool validRequestId(const char *text)
{
    if ((text == NULL) || (*text == '\0')) {
        return false;
    }

    uint8_t length = 0U;
    for (const char *cursor = text; *cursor != '\0'; ++cursor) {
        const char value = *cursor;
        const bool valid_character =
            ((value >= 'A') && (value <= 'Z')) ||
            ((value >= 'a') && (value <= 'z')) ||
            ((value >= '0') && (value <= '9')) ||
            (value == '_') ||
            (value == '-');
        if (!valid_character || (length >= 64U)) {
            return false;
        }
        ++length;
    }
    return length > 0U;
}

static bool parseUint32(const char *text, uint32_t *value)
{
    if ((text == NULL) || (*text == '\0')) {
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
        parsed = (parsed * 10UL) + digit;
    }

    *value = parsed;
    return true;
}

static uint16_t readRailMillivolts(uint8_t channel_index)
{
    /*
     * Discard one conversion after selecting a channel, then average a fixed
     * count. The divider conversion returns millivolts at the Nucleo rail.
     */
    (void)analogRead(SENSE_PINS[channel_index]);
    uint32_t raw_sum = 0UL;
    for (uint8_t sample = 0U; sample < PC_ADC_AVERAGE_SAMPLES; ++sample) {
        raw_sum += static_cast<uint16_t>(
            analogRead(SENSE_PINS[channel_index]));
    }
    const uint32_t averaged_raw =
        (raw_sum + (PC_ADC_AVERAGE_SAMPLES / 2U)) /
        PC_ADC_AVERAGE_SAMPLES;
    const uint32_t adc_pin_mv =
        ((averaged_raw * PC_ADC_REFERENCE_MV) + 511UL) / 1023UL;
    const uint32_t rail_mv =
        ((adc_pin_mv * PC_SENSE_DIVIDER_NUMERATOR) +
         (PC_SENSE_DIVIDER_DENOMINATOR / 2UL)) /
        PC_SENSE_DIVIDER_DENOMINATOR;
    return static_cast<uint16_t>(rail_mv);
}

static SenseSnapshot readSenseSnapshot()
{
    SenseSnapshot snapshot;
    snapshot.millivolts[0] = readRailMillivolts(0U);
    snapshot.millivolts[1] = readRailMillivolts(1U);
    return snapshot;
}

static const char *senseState(uint16_t millivolts)
{
    if (millivolts <= PC_OFF_MAX_MV) {
        return "OFF";
    }
    if (millivolts >= PC_ON_MIN_MV) {
        return "ON";
    }
    return "UNSAFE";
}

static bool selectedRailsOff(
    TargetMask target,
    const SenseSnapshot &snapshot)
{
    for (uint8_t index = 0U; index < 2U; ++index) {
        if (targetIncludes(target, index) &&
            (snapshot.millivolts[index] > PC_OFF_MAX_MV)) {
            return false;
        }
    }
    return true;
}

static bool selectedRailsOn(
    TargetMask target,
    const SenseSnapshot &snapshot)
{
    for (uint8_t index = 0U; index < 2U; ++index) {
        if (targetIncludes(target, index) &&
            (snapshot.millivolts[index] < PC_ON_MIN_MV)) {
            return false;
        }
    }
    return true;
}

static uint16_t selectedOffMillivolts(
    TargetMask target,
    const SenseSnapshot &snapshot)
{
    uint16_t worst_mv = 0U;
    for (uint8_t index = 0U; index < 2U; ++index) {
        if (targetIncludes(target, index) &&
            (snapshot.millivolts[index] > worst_mv)) {
            worst_mv = snapshot.millivolts[index];
        }
    }
    return worst_mv;
}

static uint16_t selectedOnMillivolts(
    TargetMask target,
    const SenseSnapshot &snapshot)
{
    uint16_t worst_mv = UINT16_MAX;
    for (uint8_t index = 0U; index < 2U; ++index) {
        if (targetIncludes(target, index) &&
            (snapshot.millivolts[index] < worst_mv)) {
            worst_mv = snapshot.millivolts[index];
        }
    }
    return (worst_mv == UINT16_MAX) ? 0U : worst_mv;
}

static bool waitForStableRailState(
    TargetMask target,
    bool want_on,
    uint32_t stable_ms,
    uint32_t timeout_ms,
    SenseSnapshot *last_snapshot)
{
    const uint32_t started_ms = millis();
    uint32_t stable_started_ms = started_ms;
    bool stability_started = false;

    for (;;) {
        const uint32_t now_ms = millis();
        *last_snapshot = readSenseSnapshot();
        const bool matches = want_on
            ? selectedRailsOn(target, *last_snapshot)
            : selectedRailsOff(target, *last_snapshot);

        if (matches) {
            if (!stability_started) {
                stable_started_ms = now_ms;
                stability_started = true;
            }
            if ((uint32_t)(now_ms - stable_started_ms) >= stable_ms) {
                return true;
            }
        } else {
            stability_started = false;
        }

        if ((uint32_t)(now_ms - started_ms) >= timeout_ms) {
            return false;
        }
        delay(PC_SAMPLE_INTERVAL_MS);
    }
}

static bool holdConfirmedOff(
    TargetMask target,
    uint32_t confirmed_off_started_ms,
    uint32_t requested_off_ms,
    SenseSnapshot *last_snapshot)
{
    for (;;) {
        const uint32_t now_ms = millis();
        if ((uint32_t)(now_ms - confirmed_off_started_ms) >=
            requested_off_ms) {
            *last_snapshot = readSenseSnapshot();
            return selectedRailsOff(target, *last_snapshot);
        }

        *last_snapshot = readSenseSnapshot();
        if (!selectedRailsOff(target, *last_snapshot)) {
            return false;
        }
        delay(PC_SAMPLE_INTERVAL_MS);
    }
}

static void printSnapshot(const SenseSnapshot &snapshot)
{
    Serial.print(F(" RELAY1="));
    Serial.print(relay_powered[0] ? F("ON") : F("OFF"));
    Serial.print(F(" RELAY2="));
    Serial.print(relay_powered[1] ? F("ON") : F("OFF"));
    Serial.print(F(" SENSE1="));
    Serial.print(senseState(snapshot.millivolts[0]));
    Serial.print(F(" SENSE2="));
    Serial.print(senseState(snapshot.millivolts[1]));
    Serial.print(F(" SENSE1_MV="));
    Serial.print(snapshot.millivolts[0]);
    Serial.print(F(" SENSE2_MV="));
    Serial.print(snapshot.millivolts[1]);
}

static void emitStatus(const __FlashStringHelper *state)
{
    const SenseSnapshot snapshot = readSenseSnapshot();
    Serial.print(F("STATUS PWRCTL=1 STATE="));
    Serial.print(state);
    printSnapshot(snapshot);
    Serial.print(F(" OFF_MAX_MV="));
    Serial.print(PC_OFF_MAX_MV);
    Serial.print(F(" ON_MIN_MV="));
    Serial.print(PC_ON_MIN_MV);
    Serial.print(F(" MIN_OFF_MS="));
    Serial.print(PC_MIN_OFF_MS);
    Serial.print(F(" MAX_OFF_MS="));
    Serial.println(PC_MAX_OFF_MS);
}

static void emitError(
    const char *request_id,
    const char *channel,
    const __FlashStringHelper *code)
{
    /*
     * Canonical runner error. request_id/channel are either already validated
     * tokens or the literal placeholder "0".
     */
    Serial.print(F("ERR "));
    Serial.print(request_id);
    Serial.print(' ');
    Serial.print(channel);
    Serial.print(' ');
    Serial.println(code);
}

static void emitAck(
    const char *request_id,
    const char *channel,
    bool off_ok,
    bool on_ok,
    uint16_t off_mv,
    uint16_t on_mv)
{
    /*
     * Keep this exact field order: tools/run_physical_campaign.py consumes the
     * line as the terminal result of one accepted request.
     */
    Serial.print(F("ACK "));
    Serial.print(request_id);
    Serial.print(' ');
    Serial.print(channel);
    Serial.print(F(" OFF_OK="));
    Serial.print(off_ok ? 1 : 0);
    Serial.print(F(" ON_OK="));
    Serial.print(on_ok ? 1 : 0);
    Serial.print(F(" OFF_MV="));
    Serial.print(off_mv);
    Serial.print(F(" ON_MV="));
    Serial.println(on_mv);
}

static void executeCycle(
    const char *request_id,
    const char *channel,
    TargetMask target,
    uint32_t off_ms)
{
    failOff(target);
    SenseSnapshot snapshot = readSenseSnapshot();

    if (!waitForStableRailState(
            target,
            false,
            PC_OFF_STABLE_MS,
            PC_OFF_CONFIRM_TIMEOUT_MS,
            &snapshot)) {
        failOff(target);
        emitAck(
            request_id,
            channel,
            false,
            false,
            selectedOffMillivolts(target, snapshot),
            0U);
        return;
    }

    /*
     * off_ms is the guaranteed dwell after OFF has already remained below the
     * threshold for PC_OFF_STABLE_MS. Discharge/confirmation time is never
     * subtracted from the requested cold-start interval.
     */
    const uint32_t confirmed_off_started_ms = millis();
    if (!holdConfirmedOff(
            target,
            confirmed_off_started_ms,
            off_ms,
            &snapshot)) {
        failOff(target);
        emitAck(
            request_id,
            channel,
            false,
            false,
            selectedOffMillivolts(target, snapshot),
            0U);
        return;
    }

    const uint16_t confirmed_off_mv =
        selectedOffMillivolts(target, snapshot);
    for (uint8_t index = 0U; index < 2U; ++index) {
        if (targetIncludes(target, index)) {
            setRelayPower(index, true);
        }
    }

    if (!waitForStableRailState(
            target,
            true,
            PC_ON_STABLE_MS,
            PC_ON_CONFIRM_TIMEOUT_MS,
            &snapshot)) {
        const uint16_t failed_on_mv =
            selectedOnMillivolts(target, snapshot);
        failOff(target);
        emitAck(
            request_id,
            channel,
            true,
            false,
            confirmed_off_mv,
            failed_on_mv);
        return;
    }

    emitAck(
        request_id,
        channel,
        true,
        true,
        confirmed_off_mv,
        selectedOnMillivolts(target, snapshot));
}

static void processCommand(char *line)
{
    char *save_pointer = NULL;
    char *command = strtok_r(line, " ", &save_pointer);
    if (command == NULL) {
        return;
    }

    if (ina226HandleAsciiCommand(command, &save_pointer)) {
        return;
    }

    if (strcmp(command, "STATUS") == 0) {
        if (strtok_r(NULL, " ", &save_pointer) != NULL) {
            emitError("0", "0", F("BAD_ARITY"));
            return;
        }
        emitStatus(F("IDLE"));
        return;
    }

    if (strcmp(command, "CYCLE") == 0) {
        char *request_id = strtok_r(NULL, " ", &save_pointer);
        char *channel_text = strtok_r(NULL, " ", &save_pointer);
        char *off_ms_text = strtok_r(NULL, " ", &save_pointer);
        char *extra = strtok_r(NULL, " ", &save_pointer);
        if ((request_id == NULL) || (channel_text == NULL) ||
            (off_ms_text == NULL) || (extra != NULL)) {
            emitError("0", "0", F("BAD_ARITY"));
            return;
        }
        if (!validRequestId(request_id)) {
            emitError("0", "0", F("BAD_REQUEST_ID"));
            return;
        }

        TargetMask target = TARGET_NONE;
        const char *canonical_channel = "0";
        if (!parseChannel(
                channel_text,
                &target,
                &canonical_channel)) {
            emitError(request_id, "0", F("BAD_CHANNEL"));
            return;
        }

        uint32_t off_ms = 0UL;
        if (!parseUint32(off_ms_text, &off_ms)) {
            emitError(request_id, canonical_channel, F("BAD_OFF_MS"));
            return;
        }
        if ((off_ms < PC_MIN_OFF_MS) || (off_ms > PC_MAX_OFF_MS)) {
            emitError(request_id, canonical_channel, F("OFF_MS_RANGE"));
            return;
        }

        executeCycle(
            request_id,
            canonical_channel,
            target,
            off_ms);
        return;
    }

    emitError("0", "0", F("UNKNOWN_COMMAND"));
}

static void pollSerial()
{
    while (Serial.available() > 0) {
        const char received = static_cast<char>(Serial.read());
        if (received == '\r') {
            continue;
        }
        if (received == '\n') {
            if (command_overflow) {
                failOff(TARGET_ALL);
                emitError("0", "0", F("LINE_TOO_LONG"));
            } else if (command_length > 0U) {
                command_buffer[command_length] = '\0';
                processCommand(command_buffer);
            }
            command_length = 0U;
            command_overflow = false;
            continue;
        }

        if (command_overflow) {
            continue;
        }
        if (command_length >= (PC_COMMAND_BUFFER_SIZE - 1U)) {
            command_overflow = true;
            continue;
        }
        command_buffer[command_length] = received;
        ++command_length;
    }
}
