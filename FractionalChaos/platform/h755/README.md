# Plataforma NUCLEO-H755ZI-Q

La plataforma separa estrictamente las funciones:

- CM7 ejecuta el integrador EFORK3 o GL, mide cada paso con DWT y almacena el
  historial en DTCM.
- Cada ejecutable CM7 enlaza únicamente la tabla `float32` generada para su
  combinación sistema--método; no se calculan `gamma`, potencias ni pesos
  fraccionarios durante el arranque o durante un paso.
- CM4 forma las tramas binarias y transmite únicamente por PD8/USART3-TX a
  921600 bit/s mediante DMA1 Stream 0, solicitud DMAMUX
  `DMA_REQUEST_USART3_TX`.
- SRAM4, desde `0x38000000`, contiene la cola SPSC. CM7 la configura como
  compartida y no cacheable mediante MPU. No se usa HSEM por muestra.
- HSEM0 se usa una sola vez con la secuencia oficial de arranque: CM4 entra en
  STOP y CM7 lo libera después de configurar el reloj e inicializar la cola.

No se inicializan recepción UART, Ethernet, USB, I2C, DAC, temporizadores ni
RTOS.

## Memoria y carga

| Núcleo | Flash | RAM principal | Región especial |
|---|---:|---:|---:|
| CM7 | `0x08000000` | AXI SRAM `0x24000000` | DTCM `0x20000000` para `.solver` |
| CM4 | `0x08100000` | alias D2 desde `0x10000100` | DMA en `0x30000000` |
| ambos | — | — | SRAM4 `.shared` en `0x38000000` |

Los dos archivos HEX se programan antes del reinicio:

```powershell
STM32_Programmer_CLI.exe -c port=SWD mode=UR reset=HWrst `
  -halt -w build\h755-release\h755_m7_lorenz_efork3.hex -v
STM32_Programmer_CLI.exe -c port=SWD mode=UR reset=HWrst `
  -halt -w build\h755-release\h755_m4_uart.hex -v -rst
```

No se deben pasar los ELF directamente a CubeProgrammer. El ELF de CM4
describe también segmentos RAM `NOLOAD`; su verificación puede fallar en la
RAM D2 antes de ejecutar `-rst`, dejando ambos núcleos detenidos y COM6 sin
bytes nuevos. Los HEX contienen únicamente las regiones programables y son el
formato usado por `tools/flash.ps1`.

La compilación usa `-fno-fast-math -ffp-contract=off`. Las instrucciones FMA
se generan sólo para las llamadas explícitas a `fmaf`, por lo que no se
reagrupan expresiones de forma implícita.

## Perfil de alimentación

El perfil predeterminado usa `DIRECT_SMPS`, 400 MHz para CM7 y 200 MHz para
HCLK/CM4. Es el perfil compatible con la NUCLEO-H755ZI-Q sin modificar.

El perfil 480/240 MHz no se habilita sólo mediante una opción de software. La
placa debe modificarse físicamente a LDO conforme a la documentación de ST y se
exigen simultáneamente estas dos opciones:

```powershell
cmake -S . -B build-h755 `
  -DFC_H755_CLOCK_480=ON `
  -DFC_H755_LDO_MODIFICATION_CONFIRMED=ON
```

Si falta la confirmación, CMake detiene la configuración.
