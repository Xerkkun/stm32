# Firmware fraccionario para STM32F746 y STM32H755

En este proyecto se implementan los sistemas de Lorenz, Rössler y Chen con
los métodos EFORK de tres etapas y Grünwald–Letnikov alineado con Caputo. Se
conserva un único núcleo numérico en C11 y se generan binarios específicos
para la NUCLEO-F746ZG y la NUCLEO-H755ZI-Q.

El firmware se prepara para compilarse, probarse y programarse directamente
desde Visual Studio Code. No se inicializan Ethernet, USB, I2C, DAC, ADC ni
temporizadores de propósito general. Solo se habilitan los recursos necesarios
para el cálculo, la medición de ciclos, la comunicación UART y, en la H755, la
coordinación de arranque entre núcleos.

## Configuraciones experimentales

En los dos métodos se utiliza la misma condición inicial y el mismo contrato
temporal para cada sistema. La memoria corta representa diez segundos:
\(M=L_m/h\).

| Sistema | Parámetros | Estado inicial | \(q\) | \(h\) | \(M\) |
|---|---|---:|---:|---:|---:|
| Lorenz | \(\sigma=10,\ \rho=28,\ \beta=8/3\) | \((0.1,0.1,0.1)\) | 0.995 | 0.005 | 2000 |
| Rössler | \(a=0.2,\ b=0.2,\ c=6\) | \((0.5,1.5,0.1)\) | 0.97 | 0.01 | 1000 |
| Chen | \(a=35,\ b=3,\ c=28\) | \((0.1,0.1,0.1)\) | 0.9 | 0.005 | 2000 |

Se producen doce configuraciones de cálculo:

| Sistema | Método | NUCLEO-F746ZG | NUCLEO-H755ZI-Q, CM7 |
|---|---|---|---|
| Lorenz | EFORK3 | `f746_lorenz_efork3.elf` | `h755_m7_lorenz_efork3.elf` |
| Lorenz | GL-Caputo | `f746_lorenz_gl.elf` | `h755_m7_lorenz_gl.elf` |
| Rössler | EFORK3 | `f746_rossler_efork3.elf` | `h755_m7_rossler_efork3.elf` |
| Rössler | GL-Caputo | `f746_rossler_gl.elf` | `h755_m7_rossler_gl.elf` |
| Chen | EFORK3 | `f746_chen_efork3.elf` | `h755_m7_chen_efork3.elf` |
| Chen | GL-Caputo | `f746_chen_gl.elf` | `h755_m7_chen_gl.elf` |

En la H755 se acompaña cualquiera de los seis binarios del CM7 con
`h755_m4_uart.elf`. El CM7 se dedica al cálculo y el CM4 se dedica a la
transmisión UART mediante DMA.

## Uso rápido desde Visual Studio Code

Se abre **esta carpeta** (`STM32/FractionalChaos`) como carpeta de trabajo y se
selecciona **Terminal > Run Task**:

- `build:host:release` compila las pruebas del núcleo en el equipo.
- `test:host:release` ejecuta las pruebas deterministas y la referencia
  independiente.
- `build:f746:release` genera los seis binarios de la F746.
- `build:h755:release` genera los seis binarios del CM7 y el binario UART del
  CM4.
- `build:all:release` ejecuta las tres compilaciones anteriores.
- `flash:seleccionar` solicita placa, sistema y método, y programa mediante
  ST-LINK.
- `uart:capturar-csv` lee el puerto virtual, valida FCC1 y conserva los datos en
  CSV.
- `uart:decodificar-binario` convierte una captura binaria FCC1 existente a
  CSV.

Las tareas invocan PowerShell con `-NoProfile -ExecutionPolicy Bypass`, por lo
que no se depende de la política local de ejecución de scripts. El flujo
detallado se describe en [`docs/USO_VSCODE.md`](docs/USO_VSCODE.md).

También se dispone del flujo equivalente en terminal:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\build.ps1 -Board all -Configuration Release

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\flash.ps1 `
  -Board f746 -System lorenz -Method efork3
```

Para la H755 se selecciona `-Board h755`. En ese caso, una misma tarea programa
primero el binario de cálculo del CM7 y después `h755_m4_uart.elf`, mediante dos
invocaciones verificables de STM32CubeProgrammer.

## Arquitectura de ejecución

En la F746 se ejecuta el solucionador en el Cortex-M7 y se transmite una trama
seleccionada por USART3 mediante DMA. En la H755 se mantiene el solucionador y
el contador DWT en el Cortex-M7; se deposita cada muestra exportada en una cola
SPSC de SRAM4 y se forma y transmite FCC1 desde el Cortex-M4. Se utiliza HSEM
para la secuencia de arranque, no para cada muestra.

Se utilizan exclusivamente operaciones `float32`, que corresponden a la FPU
de precisión simple de ambos Cortex-M7. Se precomputan los pesos, se conserva
el historial en un búfer circular, se agrupan las sumas y se aplica
compensación de Neumaier. Se emplea `fmaf` explícito donde se requiere una sola
ronda y no se habilita `-ffast-math`. Con ello se evitan conversiones
innecesarias y se controla la pérdida numérica sin atribuir exactitud absoluta
a la aritmética binaria de 32 bits.

La descripción completa se encuentra en
[`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md), y la revisión de los proyectos
heredados y de las decisiones numéricas se documenta en
[`docs/AUDITORIA_NUMERICA.md`](docs/AUDITORIA_NUMERICA.md).

## Protocolo UART FCC1

Se transmite una trama binaria de 40 bytes, en orden *little-endian*, a
921600 bit/s, 8-N-1. Se incluyen identificadores de placa, sistema y método,
secuencia, ciclos del paso numérico, muestras descartadas, las tres palabras
IEEE-754 `float32` y un CRC-32/ISO-HDLC. Al conservarse las palabras crudas se
evita la pérdida introducida por una conversión decimal y se dispone de la
materia prima para formar las secuencias de bits del experimento.

Ejemplo de captura y decodificación directa:

```powershell
python .\tools\decode_uart.py `
  --port COM7 --baud 921600 --seconds 60 `
  --output .\captura_fcc1.csv
```

Para la lectura del puerto se requiere `pyserial`. La decodificación de un
archivo binario no añade esa dependencia:

```powershell
python .\tools\decode_uart.py `
  --input .\captura_fcc1.bin `
  --output .\captura_fcc1.csv
```

## Reloj y alimentación de la H755

En una NUCLEO-H755ZI-Q sin modificaciones se selecciona el perfil seguro
`DIRECT_SMPS`: 400 MHz para el Cortex-M7 y 200 MHz para el Cortex-M4. No se
presupone una operación a 480/240 MHz.

El perfil de 480/240 MHz solo se habilita cuando se confirma la modificación
física de la ruta de alimentación a LDO indicada por ST. No se selecciona ese
perfil únicamente mediante software sobre una placa de fábrica, porque una
configuración de alimentación incompatible puede impedir el arranque.

## Alcance de la verificación

La compilación cruzada, las pruebas del núcleo en el equipo, la inspección de
los mapas de enlace y la validación de FCC1 permiten verificar la
implementación. Las cifras de tiempo, consumo de memoria observado,
comportamiento caótico y aleatoriedad se aceptan únicamente después de
ejecutarse el protocolo en las dos placas físicas.

Por ello no se infieren resultados científicos a partir de una compilación ni
de una trayectoria corta. Se registran en placa los ciclos por paso y por bit,
las tramas descartadas, las series completas y los resultados de las pruebas
estadísticas. El firmware queda preparado para esas adquisiciones; la
medición física constituye la etapa experimental, no una parte ausente del
código.
