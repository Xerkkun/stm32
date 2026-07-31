# Esquema SKiDL/KiCad del controlador UNO R4

Este directorio contiene la fuente reproducible del esquema de conexión entre:

- Arduino UNO R4 WiFi;
- NUCLEO-F746ZG y NUCLEO-H755ZI-Q;
- módulo de cuatro relevadores de 5 V con entradas activas en bajo;
- dos módulos INA226 con shunt físico `R100`;
- cuatro etapas de adaptación con 2N2222A.

El esquema es una **especificación de construcción**. Generarlo, abrirlo en
KiCad o pasar ERC no demuestra que el montaje físico exista, que los módulos
INA226 estén calibrados ni que una campaña de medición sea válida.

## Generación

Se requiere Python 3.11 o 3.12, KiCad 10 y SKiDL 2.3.0:

```powershell
py -3.11 -m venv .venv-skidl
.\.venv-skidl\Scripts\python.exe -m pip install -r .\requirements.txt

$env:KICAD10_SYMBOL_DIR = `
  'C:\Program Files\KiCad\10.0\share\kicad\symbols'

.\.venv-skidl\Scripts\python.exe `
  .\src\generate_uno_r4_power_controller.py
```

El script también detecta la instalación estándar de KiCad 10 y establece
internamente tanto `KICAD10_SYMBOL_DIR` como la variable heredada
`KICAD_SYMBOL_DIR`. Los archivos producidos quedan en:

```text
kicad/uno_r4_power_controller.kicad_sch
exports/
build/schematics/
```

Los archivos de `kicad/` son editables con Eeschema. SKiDL sigue siendo la
fuente canónica: los cambios hechos manualmente en KiCad no regresan
automáticamente al programa Python.

## Contrato de pines UNO R4

| Función | NUCLEO-F746ZG | NUCLEO-H755ZI-Q |
|---|---|---|
| Entrada del relevador | `D7 -> IN1` | `D9 -> IN2` |
| Ventana de energía | `PE0/D34 -> Q1 -> D2` | `PE0/D34 -> Q2 -> D3` |
| Referencia de reloj | `PA0/D32 -> Q3 -> D6` | `PA0/D32 -> Q4 -> D8` |
| Presencia de 3V3 | divisor `10k/20k -> A0` | divisor `10k/20k -> A1` |
| INA226 | `0x40`, `A1=A0=GND` | `0x41`, `A1=GND`, `A0=VS` |

Las salidas de los cuatro 2N2222A son de colector abierto, activas en bajo.
Cada etapa usa:

- base: `10k`;
- base a GND: `100k`;
- colector a `+5V_ARDUINO`: `10k`;
- emisor a GND común.

El orden físico `E-B-C` del 2N2222A **no es universal**. Antes de insertarlo en
la protoboard se debe revisar la hoja de datos del fabricante o medirlo con un
probador. Los footprints del esquema se marcan deliberadamente como
`Schematic_Only:Do_Not_Use_For_PCB`.

## Relevadores y alimentación

En el cabezal de control del módulo:

```text
VCC  -> +5V2_RELES
GND  -> GND común
IN1  -> UNO D7
IN2  -> UNO D9
IN3  -> sin conexión
IN4  -> sin conexión
```

`IN1` e `IN2` llevan un pull-up externo de `10k` a `+5V2_RELES`. Los contactos
de tornillo usados son:

```text
VBUS de la fuente USB -> COM
NO                    -> INA226 IN+
NC                    -> sin conexión
```

No se debe deducir el orden izquierda-centro-derecha a partir del dibujo: se
usan las marcas `NC/COM/NO` de la placa o una prueba de continuidad sin
alimentación.

El esquema mantiene separados `+5V_ARDUINO` y `+5V2_RELES`; sólo comparten
GND. El valor `5V2` conserva la medición informada del módulo de protoboard,
pero para el montaje final es preferible ajustar el LM2596 a `5.00 V` antes de
conectar el módulo. No se unen los dos positivos.

## Ruta USB e INA226

Sólo se interrumpe el conductor rojo VBUS:

```text
USB fuente VBUS -> COM -> NO -> INA226 IN+ -> R100 -> INA226 IN-
               -> VBUS de la Nucleo
```

`D+`, `D-`, GND y blindaje permanecen continuos. El pin `VBUS` de cada INA226
se une al nodo `IN-`, lado de la carga. `VS`, en cambio, recibe
`+5V_ARDUINO`, que permanece encendido durante el corte.

Antes de energizar una Nucleo:

1. verificar que el shunt esté marcado `R100`;
2. comprobar polaridad `IN+`/`IN-`;
3. confirmar ausencia de corto VBUS-GND;
4. comprobar por separado las direcciones `0x40` y `0x41`;
5. revisar si ambos módulos ya incorporan pull-ups I2C.

## Discrepancia pendiente del firmware

Este esquema sigue el contrato UNO R4 del artículo. El firmware que existe
actualmente en `firmware/power_cycle_controller_uno` todavía es para UNO AVR:
usa relevadores `D7/D8`, marcadores `D2/D3/D4/D5` e interrupciones de
`PORTD`. Debe portarse a ArduinoCore-renesas y a `D7/D9`,
`D2/D3/D6/D8` antes de realizar la prueba física. El esquema no certifica ese
port.
