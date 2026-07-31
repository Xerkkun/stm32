#pragma once

#include <Arduino.h>

/*
 * Controller-side wiring. Pins 0 and 1 are intentionally left to the UART.
 *
 * The default relay board is active-low. Each relay input therefore needs an
 * external 10 kohm pull-up to +5 V so that reset, bootloader, and unplugged-UNO
 * states de-energize the coil. If the actual board is active-high, set
 * PC_RELAY_ACTIVE_LOW to 0 and replace those pull-ups with pull-downs.
 */
#define PC_RELAY_CH1_PIN 7
#define PC_RELAY_CH2_PIN 8
#define PC_RELAY_ACTIVE_LOW 1

/*
 * Sense the Nucleo 3V3 rails through:
 *
 *     Nucleo 3V3 ----- 1 kohm ----+---- A0 or A1
 *                                  |
 *                                 2 kohm
 *                                  |
 *                                 GND
 *
 * The divider presents 2/3 of the rail to the ADC, draws 1.1 mA at 3.3 V, and
 * provides a defined zero when a board is off. PC_ADC_REFERENCE_MV should be
 * calibrated against the measured UNO 5 V rail if tighter voltage reporting
 * is needed.
 */
#define PC_SENSE_CH1_PIN A0
#define PC_SENSE_CH2_PIN A1
#define PC_ADC_REFERENCE_MV 5000UL
#define PC_SENSE_DIVIDER_NUMERATOR 3UL
#define PC_SENSE_DIVIDER_DENOMINATOR 2UL
#define PC_ADC_AVERAGE_SAMPLES 4U

/*
 * Rail classification and timing contract.
 *
 * The defaults require a measured 3V3 rail at or below 0.20 V for OFF and at
 * or above 3.10 V for ON. Values in between are explicitly UNSAFE.
 */
#define PC_OFF_MAX_MV 200U
#define PC_ON_MIN_MV 3100U
#define PC_OFF_STABLE_MS 100UL
#define PC_ON_STABLE_MS 500UL
#define PC_OFF_CONFIRM_TIMEOUT_MS 1800UL
#define PC_ON_CONFIRM_TIMEOUT_MS 10000UL
#define PC_SAMPLE_INTERVAL_MS 10UL

/*
 * A command may not request a shorter interruption than the validation
 * envelope, nor leave a board off indefinitely through an accidental value.
 */
#define PC_MIN_OFF_MS 2000UL
#define PC_MAX_OFF_MS 30000UL

#define PC_SERIAL_BAUD 115200UL
#define PC_COMMAND_BUFFER_SIZE 128U

/*
 * INA226 acquisition channels.  The sensor modules and the UNO remain powered
 * while a Nucleo VBUS is interrupted.  Only one sensor is streamed at a time:
 * 14 bytes/sample at 500 Hz occupies about 61 % of a 115200-bit/s 8N1 link,
 * leaving explicit margin for edge and terminal frames.
 *
 * These constants describe the intended hardware.  They cannot verify the
 * marking on a module: ARM additionally requires the operator/host to assert
 * R100 after physically inspecting the shunt.
 */
#define PC_INA_CH1_ADDRESS 0x40U
#define PC_INA_CH2_ADDRESS 0x41U
#define PC_INA_CH1_SHUNT_MILLIOHMS 100U
#define PC_INA_CH2_SHUNT_MILLIOHMS 100U
#define PC_INA_I2C_CLOCK_HZ 400000UL
#define PC_INA_SAMPLE_PERIOD_US 2000UL
#define PC_INA_MIN_IDLE_SAMPLES 100U
#define PC_INA_MAX_IDLE_SAMPLES 1000U
#define PC_INA_MIN_TIMEOUT_MS 1000UL
#define PC_INA_MAX_TIMEOUT_MS 60000UL

/*
 * PE0/D34 is the energy-window marker.  PA0/D32 is a separate clock-reference
 * pulse.  D2/D3 and D4/D5 are all observed through the ATmega328P PORTD pin
 * change interrupt so the raw record can label both marker kinds.
 *
 * Add an external 10 kohm pull-down from every marker input to UNO GND.  This
 * gives a defined LOW while a Nucleo is unpowered; do not enable UNO pull-ups.
 */
#define PC_ENERGY_MARKER_CH1_PIN 2
#define PC_ENERGY_MARKER_CH2_PIN 3
#define PC_CLOCK_MARKER_CH1_PIN 4
#define PC_CLOCK_MARKER_CH2_PIN 5

#if (PC_RELAY_ACTIVE_LOW != 0) && (PC_RELAY_ACTIVE_LOW != 1)
#error "PC_RELAY_ACTIVE_LOW must be 0 or 1."
#endif

#if PC_SENSE_DIVIDER_DENOMINATOR == 0
#error "PC_SENSE_DIVIDER_DENOMINATOR must be non-zero."
#endif

#if PC_ADC_AVERAGE_SAMPLES == 0
#error "PC_ADC_AVERAGE_SAMPLES must be non-zero."
#endif

#if PC_OFF_MAX_MV >= PC_ON_MIN_MV
#error "PC_OFF_MAX_MV must be lower than PC_ON_MIN_MV."
#endif

#if PC_MIN_OFF_MS < PC_OFF_CONFIRM_TIMEOUT_MS
#error "PC_MIN_OFF_MS must cover PC_OFF_CONFIRM_TIMEOUT_MS."
#endif

#if PC_MIN_OFF_MS > PC_MAX_OFF_MS
#error "PC_MIN_OFF_MS must not exceed PC_MAX_OFF_MS."
#endif

#if PC_INA_SAMPLE_PERIOD_US < 2000UL
#error "The audited UNO/115200 transport contract does not support >500 Hz."
#endif

#if PC_INA_MIN_IDLE_SAMPLES == 0
#error "PC_INA_MIN_IDLE_SAMPLES must be non-zero."
#endif

#if PC_INA_MIN_IDLE_SAMPLES > PC_INA_MAX_IDLE_SAMPLES
#error "INA idle sample bounds are inconsistent."
#endif

#if PC_INA_MIN_TIMEOUT_MS > PC_INA_MAX_TIMEOUT_MS
#error "INA timeout bounds are inconsistent."
#endif

#if (PC_INA_CH1_SHUNT_MILLIOHMS != 100U) || \
    (PC_INA_CH2_SHUNT_MILLIOHMS != 100U)
#error "The current publication contract only accepts physically verified R100 shunts."
#endif
