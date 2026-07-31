# Protocolo `PWRCTL/1`

Este protocolo ASCII pertenece al controlador externo de alimentación. No
forma parte de FCC1 ni del firmware STM32. Su validación en host no demuestra
que una Nucleo haya perdido alimentación.

## Transporte y versión

- UART USB del Arduino UNO: 115200 bit/s, 8N1.
- Un comando o mensaje por línea, terminado en `LF`; el controlador tolera
  `CRLF`.
- Los comandos y canales canónicos son sensibles a mayúsculas.
- No se canalizan comandos. El host espera el `ACK` o `ERR` de la petición
  activa antes de enviar otra.
- El comando `STATUS` descubre la versión mediante `PWRCTL=1`. El ACK de ciclo
  conserva sin campos adicionales la gramática fijada por el runner.

Los canales canónicos son:

| Canal wire | Hardware |
|---|---|
| `F746` | relé 1, sensado A0 |
| `H755` | relé 2, sensado A1 |
| `ALL` | ambos canales, reservado para prepruebas manuales |

El firmware también acepta `CH1` y `CH2` como alias de entrada, pero siempre
responde con `F746` o `H755`.

`request_id` identifica el ciclo y debe satisfacer
`[A-Za-z0-9_-]{1,64}`. El host lo genera; el controlador lo repite sin
modificar. Así, reiniciar el Arduino no puede reasignar silenciosamente un
identificador de campaña.

## `STATUS`

No recibe argumentos:

```text
STATUS
```

Respuesta:

```text
STATUS PWRCTL=1 STATE=IDLE RELAY1=OFF RELAY2=OFF SENSE1=OFF SENSE2=OFF SENSE1_MV=12 SENSE2_MV=9 OFF_MAX_MV=200 ON_MIN_MV=3100 MIN_OFF_MS=2000 MAX_OFF_MS=30000
```

`RELAYN` es el estado ordenado a la bobina; `SENSEN` es la clasificación
independiente del voltaje. Una lectura entre los umbrales OFF y ON se informa
como `UNSAFE`. Al arrancar se emite una línea equivalente con
`STATE=BOOT_FAIL_OFF`; el runner puede descartarla como banner.

## `CYCLE`

La petición canónica es exactamente:

```text
CYCLE <request_id> <F746|H755|ALL> <off_ms>
```

Ejemplos:

```text
CYCLE run_0007 F746 2500
CYCLE run_0008 H755 2500
CYCLE preflight-pair ALL 3000
```

El intervalo predeterminado es 2000--30000 ms. Cada petición válida es
síncrona:

1. desenergiza el relé seleccionado;
2. exige OFF continuo durante 100 ms, con timeout de 1800 ms;
3. después de confirmar OFF estable, conserva y supervisa OFF durante
   `off_ms` completos; el tiempo de descarga y confirmación no se descuenta;
4. energiza el relé;
5. exige ON continuo durante 500 ms, con timeout de 10 s;
6. emite una sola línea ACK terminal.

La respuesta terminal conserva exactamente este orden:

```text
ACK <request_id> <channel> OFF_OK=<0|1> ON_OK=<0|1> OFF_MV=<int> ON_MV=<int>
```

Ejemplo correcto:

```text
ACK run_0007 F746 OFF_OK=1 ON_OK=1 OFF_MV=14 ON_MV=3294
```

Significado de los campos:

- `OFF_OK=1`: OFF se confirmó estable y, desde esa confirmación, se mantuvo
  durante todo `off_ms`.
- `ON_OK=1`: después de un OFF válido se confirmó ON estable.
- `OFF_OK=0 ON_OK=0`: timeout de OFF o pérdida de OFF antes de cumplir
  `off_ms`; el canal queda desenergizado.
- `OFF_OK=1 ON_OK=0`: timeout de ON; el canal vuelve a quedar
  desenergizado.
- `OFF_MV`: lectura del canal al final de OFF; ante fallo es la última lectura.
- `ON_MV`: lectura al validar ON o la última lectura antes del timeout; vale
  cero cuando ON no se intentó.

Para `ALL`, `OFF_MV` es la mayor lectura de los dos canales y `ON_MV` la
menor. Son los valores conservadores para comprobar respectivamente
`<= OFF_MAX_MV` y `>= ON_MIN_MV`.

Un ACK con cualquier bandera cero es un resultado físico fallido, no una
ejecución aceptada para la campaña.

## Errores de petición

Una petición que no llega a ejecutar un ciclo recibe:

```text
ERR <request_id|0> <channel|0> <CODE>
```

Códigos:

- `BAD_ARITY`
- `BAD_REQUEST_ID`
- `BAD_CHANNEL`
- `BAD_OFF_MS`
- `OFF_MS_RANGE`
- `UNKNOWN_COMMAND`
- `LINE_TOO_LONG`

`LINE_TOO_LONG` desenergiza ambos canales antes de responder. Los
placeholders `0` indican que el token correspondiente no pudo validarse.

## Parser host

El módulo `host/controller_protocol.py` codifica comandos y valida el orden,
los rangos, el identificador y los canales:

```powershell
python .\hardware\power_cycle_controller_uno\host\controller_protocol.py `
  cycle run_0007 F746 2500

python .\hardware\power_cycle_controller_uno\host\controller_protocol.py `
  parse "ACK run_0007 F746 OFF_OK=1 ON_OK=1 OFF_MV=14 ON_MV=3294"
```

El módulo no abre puertos seriales, acciona relés ni escribe evidencia de
campaña.

## Extensión de adquisición `INA14/1`

`INA14/1` extiende el mismo controlador sin modificar la gramática de
`CYCLE/ACK`. El puerto tiene siempre **un solo propietario**. En modo ASCII se
pueden ejecutar `STATUS`, `CYCLE`, `INA_STATUS` e `INA_READ`; `ARM` cambia de
forma determinista al flujo binario y el frame terminal `END` devuelve el
puerto al modo ASCII. No se debe abrir el mismo COM desde un segundo proceso.

La consulta, que no inicia una captura, es:

```text
INA_STATUS <F746|H755>
```

Ejemplo de respuesta:

```text
INASTATUS INACAP=1 CHANNEL=F746 ADDR=0x40 I2C_OK=1 MFG=0x5449 DIE=0x2260 SHUNT_MOHM=100 SHUNT_PHYSICALLY_VERIFIED=0 ENERGY_MARKER=0 CLOCK_MARKER=0 PERIOD_US=2000 FRAME=INA14/1 BAUD=115200
```

Los registros `MFG` y `DIE` prueban compatibilidad de protocolo, no
autenticidad del módulo. `SHUNT_PHYSICALLY_VERIFIED=0` es deliberado: el
firmware no puede leer la inscripción del resistor.

Una lectura puntual cruda para construir una tabla de calibración se solicita
con:

```text
INA_READ <F746|H755>
```

Respuesta:

```text
INAREAD CHANNEL=F746 ADDR=0x40 TIMESTAMP_US=12345 BUS_RAW=4000 SHUNT_RAW=1200 CNVR=1 OVF=0
```

No aplica por sí sola una referencia ni produce amperes o volts calibrados.
El CLI `read` conserva estos valores como
`measured_raw_unreferenced`; la carga o instrumento de referencia se registra
por separado.

### `ARM` y transición binaria

```text
ARM <request_id> <F746|H755> <pre_samples> <post_samples> <timeout_ms> R100
```

- `pre_samples` y `post_samples`: 100--1000;
- `timeout_ms`: 1000--60000;
- `R100`: afirmación explícita del operador después de inspeccionar el shunt;
  no es una medición ni una detección automática.

Si identidad, configuración, marcador inicial y rangos son válidos, el
controlador responde una sola línea:

```text
ARMED cap-001 F746 INACAP=1 ADDR=0x40 MFG=0x5449 DIE=0x2260 PERIOD_US=2000 PRE=128 POST=128 TIMEOUT_MS=60000 SHUNT_MOHM=100 SHUNT_MARKING=R100 FRAME=INA14/1
```

El byte siguiente al `LF` ya pertenece a frames binarios. Una vez armado, el
host inicia el `START/READY` de la Nucleo por su puerto separado. Si el flanco
de subida de PE0 ocurre antes de reunir `pre_samples`, la captura termina como
inválida.

Durante el flujo sólo se acepta:

```text
STOP <request_id>
```

No hay respuesta ASCII a `STOP`: se emite un `END` binario con la bandera
`stopped`. No se permiten `CYCLE`, `STATUS` ni un segundo `ARM` antes de ese
`END`.

### Frame fijo

Todos los frames tienen 14 bytes y orden little-endian:

| Offset | Bytes | Campo |
|---:|---:|---|
| 0 | 2 | sincronía `A5 5A` |
| 2 | 1 | tipo en bits 7--6 y banderas en bits 5--0 |
| 3 | 2 | secuencia asociada |
| 5 | 4 | `micros()` del UNO |
| 9 | 2 | palabra 0 |
| 11 | 2 | palabra 1 |
| 13 | 1 | CRC-8/ATM de bytes 0--12, polinomio `0x07`, init `0` |

Tipos:

- `01` (`SAMPLE`): palabra 0 = bus raw `uint16`; palabra 1 = shunt raw
  `int16`. Banderas: `conversion_ready`, `math_overflow`, nivel actual de
  PE0 e `i2c_ok`.
- `10` (`EDGE`): palabras cero. Las banderas identifican nivel y
  `energy_window` (PE0/D34) o `clock_reference` (PA0/D32).
- `11` (`END`): palabra 0 = número de errores I2C; palabra 1 = ranuras de
  muestreo tardías. Las banderas indican `complete`, `stopped`, `timeout`,
  error I2C, overflow de cola y anomalía temporal/de flancos.

Cada muestra fallida sigue ocupando una secuencia con `i2c_ok=0`; cada ranura
perdida produce un salto explícito de secuencia. El decodificador también
cuenta bytes descartados y CRC inválidos. Ninguno se rellena ni se interpola
en el registro crudo. Una muestra con el mismo tick `micros()` que el flanco
de bajada de PE0 permanece dentro de la ventana inclusiva; sólo una muestra
estrictamente posterior cuenta para `post_samples`.

### Tasa defendible

El INA226 se configura con una conversión shunt de 140 µs y una de bus de
140 µs, promedio 1, modo continuo. El par requiere 280 µs y se lee cada
2000 µs (500 Hz). A esa tasa, `14 bytes × 500 × 10 bits` consume
70 kbit/s, 60.77 % del enlace de 115200 bit/s. Se captura una sola placa por
vez. Subir a 1 kHz en este UNO/enlace no pertenece al contrato porque dejaría
sin margen al UART y a las tres transacciones I2C.

Los tiempos, modos y registros proceden de la
[hoja de datos oficial INA226 de Texas Instruments](https://www.ti.com/lit/ds/symlink/ina226.pdf).
La tasa física de 500 Hz todavía debe comprobarse mañana observando cero
ranuras tardías, cero saltos de secuencia y cero CRC inválidos; el cálculo de
ancho de banda no sustituye esa prueba.

El host exige además que el pulso PA0 suba, baje y termine antes de que suba
PE0. Una pareja de flancos PA0 espuria en otra parte de la captura no satisface
el transporte crudo.

### API de sesión reutilizable

`host/ina226_capture.py` expone `Ina226Session(serial_port)`. El objeto recibe
el serial **ya abierto** y nunca lo abre ni lo cierra:

```python
ina = Ina226Session(controller_serial)
# controller_serial ya se usó aquí para CYCLE/ACK.
armed = ina.arm(
    request_id,
    "F746",
    physically_confirmed_shunt_marking="R100",
)
# Enviar START por el COM de la Nucleo.
raw_stream = ina.read_capture()
```

Así el runner puede conservar un solo propietario del COM del controlador. El
CLI independiente es útil para banco, pero abrir el puerto de un UNO clásico
puede reiniciarlo y llevar ambos relés a OFF.

### Identidad física y base temporal

Cada JSONL debe recibir un `controller_id` tomado de una etiqueta física
estable del UNO, por ejemplo `UNO_PWR_01`. El host lo escribe en
`header.timebase.controller_id` y rechaza nombres como `COM12`, que no son una
identidad persistente. Esa etiqueta permite exigir que una calibración
temporal trazable corresponda al mismo oscilador/controlador. Sin dicha
calibración, la referencia PA0 continúa siendo diagnóstica y no publicable.

La creación del JSONL exige además `firmware_clock_profile` compatible con el
canal: `f746_216mhz` para F746 o `h755_400mhz` para la cohorte H755 actual. El
header copia sus valores en `clock_reference_contract`. Ese bloque declara
`source=host_selected_frozen_campaign_profile` y
`profile_is_measurement=false`: registra la selección del operador, pero el
INA226/UNO no lee ni atesta la identidad del binario STM32.
