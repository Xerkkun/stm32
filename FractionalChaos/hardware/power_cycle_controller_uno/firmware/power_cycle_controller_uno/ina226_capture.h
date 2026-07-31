#pragma once

/*
 * Optional INA226 acquisition extension for PWRCTL/1.
 *
 * Normal STATUS/CYCLE/ACK lines remain ASCII.  ARM enters the fixed-size
 * INA14/1 binary stream; its terminal END frame returns the serial transport
 * to ASCII command mode.  The host must keep a single owner for this session.
 */

void ina226CaptureInitialize();
bool ina226CaptureActive();
void ina226CapturePoll();
bool ina226HandleAsciiCommand(char *command, char **save_pointer);
