#pragma once

#include <Arduino.h>

/*
 * Controller-side wiring. Pins 0 and 1 are intentionally left to the UART.
 *
 * Ported to Arduino Uno R4 (Renesas RA4M1):
 * - Relay and sense pins are unchanged.
 * - Marker pins verified against pruebas.ino diagnostics on the actual wiring.
 * - AVR-specific PCINT registers removed in ina226_capture.cpp.
 * - The relay board is active-low. Each relay input needs an external 10 kohm
 *   pull-up to +5 V so that reset, bootloader, and unplugged-UNO states
 *   de-energize the coil.
 */
#define PC_RELAY_CH1_PIN     7
#define PC_RELAY_CH2_PIN     9   // Hardware: D9 (verified with pruebas.ino)
#define PC_RELAY_ACTIVE_LOW  1

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
 * Marker pins — verified against pruebas.ino on the actual Uno R4 wiring.
 * Energy markers: D2 (F746) and D3 (H755) — unchanged from original.
 * Clock markers:  D6 (F746) and D8 (H755) — corrected from D4/D5.
 *
 * All four pins use external 10 kohm pull-downs to GND. Do NOT enable
 * internal pull-ups; the Nucleo GPIOs are push-pull and a floating input
 * while unpowered would produce spurious edges.
 *
 * On the Uno R4 (Renesas RA4M1) all digital pins support CHANGE interrupts
 * via attachInterrupt(digitalPinToInterrupt(pin), ..., CHANGE), which
 * replaces the AVR PCINT2_vect / PIND / PCICR / PCMSK2 mechanism.
 */
#define PC_ENERGY_MARKER_CH1_PIN  2
#define PC_ENERGY_MARKER_CH2_PIN  3
#define PC_CLOCK_MARKER_CH1_PIN   6   // D6 (verified; was D4 in AVR version)
#define PC_CLOCK_MARKER_CH2_PIN   8   // D8 (verified; was D5 in AVR version)

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
