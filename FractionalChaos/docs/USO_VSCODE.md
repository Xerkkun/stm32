# Compilación y programación desde Visual Studio Code

## Preparación

Se abre `STM32/FractionalChaos` como carpeta raíz en Visual Studio Code. Con
ello `${workspaceFolder}` coincide con la raíz que contiene
`CMakePresets.json`, `tools` y `.vscode`.

Se recomiendan las extensiones:

- STM32 VS Code Extension, `stmicroelectronics.stm32-vscode-extension`;
- CMake Tools, `ms-vscode.cmake-tools`;
- Cortex-Debug, `marus25.cortex-debug`;
- Python, `ms-python.python`, para decodificar FCC1.

Los scripts localizan CMake, Ninja, GNU Tools for STM32 y STM32CubeProgrammer
en los paquetes instalados por STM32Cube para Visual Studio Code o por
STM32CubeIDE. Durante la primera compilación se verifica la revisión de las
bibliotecas. Si falta STM32CubeH7, se obtiene la revisión fijada por el
proyecto; por ello se requiere acceso a red solo en esa preparación inicial.

Las tareas usan:

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass
```

Así se evita que una política local de PowerShell impida ejecutar los scripts
del proyecto.

## Compilación

Desde **Terminal > Run Task** se selecciona una de las tareas:

| Tarea | Resultado |
|---|---|
| `build:host:release` | Núcleo y pruebas para el equipo |
| `build:f746:release` | Seis ELF para NUCLEO-F746ZG |
| `build:h755:release` | Seis ELF CM7 y un ELF CM4 para NUCLEO-H755ZI-Q |
| `build:all:release` | Las tres compilaciones anteriores |

`build:all:release` se registra como tarea de compilación predeterminada y
también se ejecuta con **Ctrl+Shift+B**.

Los resultados se colocan en:

```text
build/
├── host-release/
├── f746-release/
│   ├── f746_lorenz_efork3.elf
│   ├── f746_lorenz_gl.elf
│   ├── f746_rossler_efork3.elf
│   ├── f746_rossler_gl.elf
│   ├── f746_chen_efork3.elf
│   └── f746_chen_gl.elf
└── h755-release/
    ├── h755_m7_lorenz_efork3.elf
    ├── h755_m7_lorenz_gl.elf
    ├── h755_m7_rossler_efork3.elf
    ├── h755_m7_rossler_gl.elf
    ├── h755_m7_chen_efork3.elf
    ├── h755_m7_chen_gl.elf
    └── h755_m4_uart.elf
```

No se utiliza `-ffast-math`. En los binarios embebidos se exigen tablas
precomputadas, por lo que el paso numérico no evalúa potencias, logaritmos ni
funciones gamma.

## Pruebas del núcleo

Se ejecuta `test:host:release`. La tarea primero actualiza la compilación host
y luego ejecuta CTest con salida completa cuando aparece un fallo.

Las pruebas host se utilizan para:

- comprobar los seis manifiestos método-sistema;
- comparar el búfer circular con una implementación lineal;
- revisar pesos y coeficientes congelados;
- contrastar horizontes cortos con una referencia independiente;
- verificar el protocolo y los límites de memoria cubiertos por las pruebas.

Esta validación no reemplaza la ejecución en la placa ni demuestra por sí sola
caos o aleatoriedad.

## Programación de la NUCLEO-F746ZG

Se compila primero `build:f746:release`. Se conecta el puerto ST-LINK y se
ejecuta `flash:seleccionar`. En los selectores se elige:

```text
board  = f746
system = lorenz | rossler | chen
method = efork3 | gl
```

STM32CubeProgrammer escribe, verifica y reinicia el microcontrolador. Desde
terminal se obtiene el mismo resultado:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\flash.ps1 `
  -Board f746 -System rossler -Method gl
```

Cuando se conectan varias sondas se indica su número de serie:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\flash.ps1 `
  -Board f746 -System chen -Method efork3 `
  -ProbeSerial 123456789ABC
```

## Programación de la NUCLEO-H755ZI-Q

Se conserva la placa de fábrica en su ruta `DIRECT_SMPS` y se compila
`build:h755:release`. El perfil predeterminado utiliza 400 MHz para el CM7 y
200 MHz para el CM4.

Se ejecuta `flash:seleccionar` con:

```text
board  = h755
system = lorenz | rossler | chen
method = efork3 | gl
```

Se programan en una sola operación:

- `h755_m7_<sistema>_<método>.elf`, que contiene el cálculo;
- `h755_m4_uart.elf`, que contiene FCC1 y USART3 TX por DMA.

No se selecciona 480/240 MHz sobre una NUCLEO-H755ZI-Q de fábrica. Ese perfil
solo se utiliza después de confirmarse la modificación física de alimentación
a LDO documentada por ST. La selección de una macro de compilación no
reemplaza esa modificación.

Ejemplo equivalente en terminal:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\flash.ps1 `
  -Board h755 -System lorenz -Method efork3
```

## Captura UART y FCC1

Se utiliza el puerto virtual de ST-LINK a 921600 bit/s, 8 bits, sin paridad y
un bit de parada. La recepción de la placa no se inicializa; la comunicación
es únicamente de salida.

Para capturar y convertir directamente a CSV se ejecuta
`uart:capturar-csv`. Se solicitan el puerto COM, la duración y la ruta de
salida. Para leer el puerto se instala una vez:

```powershell
python -m pip install pyserial
```

La misma operación se expresa como:

```powershell
python .\tools\decode_uart.py `
  --port COM7 --baud 921600 --seconds 60 `
  --output .\captura_fcc1.csv
```

Si se dispone de una captura binaria se ejecuta
`uart:decodificar-binario`. En este caso no se requiere `pyserial`:

```powershell
python .\tools\decode_uart.py `
  --input .\captura_fcc1.bin `
  --output .\captura_fcc1.csv
```

El decodificador busca `FCC1`, descarta bytes hasta resincronizar, verifica
CRC-32 y solo escribe tramas válidas. En el CSV se conservan `x`, `y`, `z` y
sus palabras hexadecimales `x_bits`, `y_bits`, `z_bits`.

## Secuencia de adquisición

Para conservar resultados comparables se aplica la misma secuencia:

1. Se compila en `Release` y se registra la identificación exacta del
   ejecutable.
2. Se programa la configuración elegida y se reinicia la placa.
3. Se inicia la captura FCC1 sin aplicaciones que compartan el puerto COM.
4. Se conserva el CSV original sin editar.
5. Se comprueba `status=0`, la monotonicidad de `sequence`, el CRC y
   `dropped`.
6. Se separa el transitorio con una regla fijada antes del análisis.
7. Se extraen los bits desde las palabras `*_bits`, no desde el texto decimal.
8. Se registran ciclos por paso, tiempo por bit aceptado y resultados
   estadísticos junto con placa, sistema y método.

Se repite la adquisición en las dos placas para las seis combinaciones. No se
mezclan frecuencias nominales del catálogo con frecuencias realmente
configuradas.

## Diagnóstico

### No se encuentra una herramienta

Se ejecuta:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\bootstrap.ps1
```

Se informa la ruta resuelta para GCC, Ninja, STM32CubeProgrammer y las
bibliotecas. Se instala o se activa el paquete indicado por STM32Cube para
Visual Studio Code cuando falta alguno.

### No existe el ELF solicitado

Se ejecuta primero `build:f746:release` o `build:h755:release`. El script de
programación no sustituye automáticamente una compilación anterior y no
programa un binario de otra configuración.

### Se conectan varias placas

Se consulta el número de serie con STM32CubeProgrammer y se pasa
`-ProbeSerial` desde el terminal. De este modo se evita seleccionar una sonda
por posición.

### No aparecen tramas

Se verifica que se utiliza el puerto COM del ST-LINK de la placa programada,
que ningún monitor serie mantiene el puerto abierto y que se seleccionan
921600 bit/s y 8-N-1. También se comprueba que se programan ambos núcleos en
la H755.

### Se observan muestras descartadas

Se revisa `dropped`. El cálculo no se bloquea cuando el transporte queda atrás;
por ello una captura destinada a estimar tasa efectiva de bits contabiliza
los descartes y no asume continuidad implícita.

