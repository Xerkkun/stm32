# Controlador externo de ciclos de alimentación

Este componente prepara un Arduino UNO clásico para gobernar dos relés y
comprobar, mediante dos entradas analógicas, que las líneas 3V3 realmente
alcanzan estados OFF y ON estables. Está separado del ejecutor de la campaña:
compilarlo, probar su parser o recibir mensajes simulados no constituye
evidencia física.

## Asignación de canales y pines

| Canal wire | Placa | Relé | Sensado | INA226 | Ventana PE0 | Reloj PA0 |
|---|---|---:|---:|---:|---:|---:|
| `F746` | NUCLEO-F746ZG | D7 | A0 | `0x40` | D2 | D4 |
| `H755` | NUCLEO-H755ZI-Q | D8 | A1 | `0x41` | D3 | D5 |

El Arduino y la tarjeta de relés permanecen alimentados por una fuente USB
independiente. El cable de cada Nucleo conserva D+, D−, GND y blindaje; sólo
VBUS/+5 V pasa por el contacto:

```text
VBUS de la fuente USB ---- COM del relé
NO del relé -------------- entrada de la cadena INA226/Nucleo
NC ----------------------- sin conexión
```

El contacto **NO** es obligatorio para que una bobina desenergizada corte la
alimentación. No se deben intervenir los cables originales de las placas:
conviene usar extensiones USB identificadas y comprobar continuidad y ausencia
de cortos antes de conectarlas.

El valor por defecto supone una tarjeta de relés con entrada activa en bajo.
Se conecta D7/D8 a IN1/IN2 y se añade un pull-up externo de 10 kohm desde cada
entrada a +5 V. Ese sesgo mantiene la bobina desenergizada mientras el UNO
está en reset o ejecuta el bootloader. Para una tarjeta activa en alto se
cambia `PC_RELAY_ACTIVE_LOW` a `0` en
`firmware/power_cycle_controller_uno/controller_config.h` y se usan
pull-downs externos.

No se debe alimentar una bobina directamente desde un GPIO. La tarjeta debe
incluir su etapa de transistor y diodo de rueda libre; si se usan relés
discretos, esa etapa es obligatoria.

## Sensado 3V3

Cada entrada usa este divisor, con tierra común:

```text
3V3 de la Nucleo ----- 1 kohm ----+---- A0/A1
                                   |
                                  2 kohm
                                   |
                                  GND
```

El ADC recibe dos tercios de 3V3, el divisor consume 1.1 mA por canal y el
resistor de 2 kohm fija cero cuando la Nucleo se apaga. Los umbrales
predeterminados, referidos a la línea antes del divisor, son:

- OFF: `<= 200 mV`;
- ON: `>= 3100 mV`;
- UNSAFE: cualquier valor intermedio.

Antes de una campaña se mide la referencia de 5 V del UNO y, si es necesario,
se ajusta `PC_ADC_REFERENCE_MV`. También se pueden cambiar umbrales, tiempos y
pines en `controller_config.h`. La preprueba física debe verificar con
multímetro que cada 3V3 cae por debajo del umbral; el mensaje del controlador
por sí solo no calibra ni certifica la cadena de medida.

## INA226 y marcadores

Los dos INA226 comparten `SDA=A4`, `SCL=A5` y GND del UNO, pero se programan
con direcciones distintas:

- módulo F746: `0x40`;
- módulo H755: `0x41` mediante el puente de dirección de su tarjeta.

Ambos módulos y el UNO se alimentan desde una fuente independiente que
permanece encendida durante el corte. Para cada canal, la ruta VBUS de potencia
es:

```text
VBUS fuente -> contacto NO del relé -> INA226 IN+ -> shunt -> INA226 IN-
           -> VBUS de la Nucleo
```

El registro de bus del INA226 mide el lado `IN-`, es decir, la tensión que
realmente llega a la carga. Mantener una tierra común entre fuente, UNO,
INA226 y Nucleo. Antes de conectar una placa:

1. comprobar continuidad y polaridad `IN+`/`IN-`;
2. inspeccionar que el resistor diga exactamente **R100**;
3. rechazar `R002`, `R010` u otra inscripción para este contrato;
4. medir con multímetro que no haya corto entre VBUS y GND;
5. confirmar `0x40` y `0x41` por separado con `INA_STATUS`;
6. colocar al UNO una etiqueta física estable, por ejemplo `UNO_PWR_01`.

La etiqueta se registra como `timebase.controller_id` y debe coincidir con la
calibración temporal posterior. El nombre `COM12` no es una identidad: puede
cambiar al reconectar el dispositivo y el host lo rechaza como
`controller_id`.

La configuración declara 100 mΩ, pero ningún comando puede verificar la
inscripción física. El token `R100` de `ARM` sólo conserva la confirmación del
operador en la procedencia.

Los marcadores se conectan con tierra común:

```text
F746 PE0/D34 ---- UNO D2     ventana de energía
H755 PE0/D34 ---- UNO D3     ventana de energía
F746 PA0/D32 ---- UNO D4     referencia de reloj
H755 PA0/D32 ---- UNO D5     referencia de reloj
```

Añadir un pull-down externo de 10 kohm desde cada D2--D5 a GND. Los cuatro
pines se registran por interrupción de cambio de PORTD. Los flancos PE0 se
etiquetan `energy_window` y son los únicos que delimitan la integración; PA0
se conserva aparte como `clock_reference`.

La adquisición usa una sola sonda por ejecución, 500 Hz, y frames binarios
con CRC. El firmware conserva el `CYCLE/ACK` ASCII existente. La especificación
completa está en [`PROTOCOL.md`](PROTOCOL.md).

## Seguridad temporal

Los valores predeterminados aceptan interrupciones de 2000 a 30000 ms. Cada
ciclo:

1. desenergiza el relé seleccionado;
2. exige OFF estable durante 100 ms, con timeout de 1800 ms;
3. una vez confirmado OFF estable, lo conserva durante todo el `off_ms`
   solicitado; la descarga y la confirmación ocurren antes de ese intervalo;
4. energiza el relé;
5. exige ON estable durante 500 ms, con timeout de 10 s.

Ante `OFF_TIMEOUT`, `OFF_LOST` u `ON_TIMEOUT`, el canal seleccionado queda
desenergizado. `ALL` aplica la condición simultáneamente a ambos canales.

## Compilar y cargar

Con Arduino CLI y el core AVR instalado:

```powershell
arduino-cli core install arduino:avr

arduino-cli compile --fqbn arduino:avr:uno `
  .\hardware\power_cycle_controller_uno\firmware\power_cycle_controller_uno

arduino-cli upload --fqbn arduino:avr:uno --port COM12 `
  .\hardware\power_cycle_controller_uno\firmware\power_cycle_controller_uno
```

En Arduino IDE se abre
`firmware/power_cycle_controller_uno/power_cycle_controller_uno.ino`, se
selecciona **Arduino Uno** y el puerto correcto, y se carga. El monitor serial
se configura a 115200 bit/s y terminación de línea `LF` o `CRLF`.

Comandos de prueba manual:

```text
STATUS
CYCLE manual_001 F746 2500
CYCLE manual_002 H755 2500
CYCLE manual_pair ALL 3000
INA_STATUS F746
INA_STATUS H755
INA_READ F746
INA_READ H755
```

La gramática y las respuestas terminales se especifican en
[`PROTOCOL.md`](PROTOCOL.md).

## Pruebas host

```powershell
python -m pytest `
  .\hardware\power_cycle_controller_uno\tests `
  -q
```

Estas pruebas sólo cubren codificación y parseo. Para que una ejecución sea
evidencia de ciclo real hacen falta el montaje, la validación de voltajes, el
registro serial íntegro y la procedencia limpia exigida por la campaña.

## Captura y calibración

El comando de banco abre el UNO, lo arma y espera los flancos de la Nucleo:

```powershell
python .\hardware\power_cycle_controller_uno\host\ina226_capture.py `
  capture `
  --port COM12 `
  --channel F746 `
  --firmware-clock-profile f746_216mhz `
  --request-id run_0007 `
  --capture-id energy_run_0007 `
  --run-id run_0007 `
  --controller-id UNO_PWR_01 `
  --work-units 10000 `
  --output .\validation\results\energy\run_0007.raw.jsonl `
  --confirm-r100
```

Después de leer `ARMED`, se inicia el `START/READY` de la Nucleo. Para la
campaña no se deben usar dos procesos sobre COM12: el runner debe reutilizar
`Ina226Session` con el mismo objeto serial que ya ejecutó `CYCLE/ACK`. Abrir un
UNO clásico desde este CLI suele activar DTR, reiniciar el controlador y
desenergizar ambos relés.

El JSONL resultante sigue siendo `measured_raw_unvalidated` y contiene
`publication_ready=false`. El header conserva
`clock_reference_contract` con el perfil congelado elegido en el host; sus
campos `source=host_selected_frozen_campaign_profile` y
`profile_is_measurement=false` dejan explícito que no se midió ni atestó el
binario cargado. Después se ajusta cada calibración con puntos realmente
medidos:

```powershell
python .\validation\ina226_energy.py fit-calibration `
  --input .\validation\ina226_calibration_f746_input.json `
  --output .\validation\ina226_calibration_f746.json

python .\validation\ina226_energy.py integrate `
  --capture .\validation\results\energy\run_0007.raw.jsonl `
  --calibration .\validation\ina226_calibration_f746.json `
  --output .\validation\results\energy\run_0007.energy.json
```

La calibración requiere puntos de corriente y tensión de referencia,
presupuesto de incertidumbre y comprobación posterior a la campaña. Compilar
el sketch, leer IDs o producir un JSONL sin calibrar no es una medición
publicable.

## Integración con la campaña

El runner ejecuta `CYCLE/ACK`, arma el INA226 y captura FCC1/INA14 usando un
solo propietario del COM del UNO. También selecciona automáticamente el
multiplicador congelado de
`validation/energy_workload_contract_v1.json`; no se acepta un multiplicador
manual. Este dry-run verifica el contrato sin tocar hardware:

```powershell
python .\tools\run_physical_campaign.py `
  --manifest .\validation\physical_campaign_selected_v1.json `
  run `
  --endpoint benchmark_reset_pilot `
  --cell chen_m2sfrk_h755_float32 `
  --reset-repetition 1 `
  --max-runs 1 `
  --power-controller-port COM12 `
  --power-controller-id UNO_PWR_01 `
  --ina226-energy `
  --confirm-ina226-r100
```

Para la ejecución física se agregan `--execute`,
`--confirm-campaign-id stm32_selected_36x30_v1` y `--allow-pilot-only`.
`--power-cycle-timeout` debe cubrir `off_ms`, los watchdogs OFF/ON del
firmware y un segundo de margen host; con `off_ms=30000`, debe ser mayor que
42.8 s.

El runner exige 1000 muestras y al menos 2 s dentro de la ventana PE0, 128
muestras de reposo antes y después, pulso PA0 de 100 ms terminado antes de
PE0, secuencias contiguas, CRC limpio y cero errores/ranuras tardías. El
artefacto `energy_capture.raw.jsonl` conserva aun así
`measured_raw_unvalidated` y `publication_ready=false`: todavía requiere
calibración INA226, calibración trazable del reloj del mismo
`controller_id`, preflight físico y comprobación posterior a la campaña.
