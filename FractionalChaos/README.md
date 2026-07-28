# Firmware fraccionario para STM32F746 y STM32H755

En este proyecto se implementan los sistemas de Lorenz, Rössler y Chen con
los métodos EFORK de tres etapas, Grünwald–Letnikov alineado con Caputo y
M2sFRK. Se conserva un único núcleo numérico en C11 y se generan binarios
específicos para la NUCLEO-F746ZG y la NUCLEO-H755ZI-Q.

El firmware se prepara para compilarse, probarse y programarse directamente
desde Visual Studio Code. No se inicializan Ethernet, USB, I2C, DAC, ADC ni
temporizadores de propósito general. Solo se habilitan los recursos necesarios
para el cálculo, la medición de ciclos, la comunicación UART y, en la H755, la
coordinación de arranque entre núcleos.

## Configuraciones experimentales

En los tres métodos se utiliza la misma condición inicial y el mismo contrato
temporal para cada sistema. La memoria corta representa diez segundos:
\(M=L_m/h\).

| Sistema | Parámetros | Estado inicial | \(q\) | \(h\) | \(M\) |
|---|---|---:|---:|---:|---:|
| Lorenz | \(\sigma=10,\ \rho=28,\ \beta=8/3\) | \((0.1,0.1,0.1)\) | 0.995 | 0.005 | 2000 |
| Rössler | \(a=0.2,\ b=0.2,\ c=6\) | \((0.5,1.5,0.1)\) | 0.97 | 0.01 | 1000 |
| Chen | \(a=35,\ b=3,\ c=28\) | \((0.1,0.1,0.1)\) | 0.9 | 0.005 | 2000 |

La matriz primaria produce 36 imágenes de cálculo: 18 `float32` y 18 de
punto fijo mixto `fixed_q14_q30`. En la segunda representación, el sufijo
`_fixed` se añade al nombre mostrado en la tabla:

| Sistema | Método | NUCLEO-F746ZG | NUCLEO-H755ZI-Q, CM7 |
|---|---|---|---|
| Lorenz | EFORK3 | `f746_lorenz_efork3.elf` | `h755_m7_lorenz_efork3.elf` |
| Lorenz | GL-Caputo | `f746_lorenz_gl.elf` | `h755_m7_lorenz_gl.elf` |
| Lorenz | M2sFRK | `f746_lorenz_m2sfrk.elf` | `h755_m7_lorenz_m2sfrk.elf` |
| Rössler | EFORK3 | `f746_rossler_efork3.elf` | `h755_m7_rossler_efork3.elf` |
| Rössler | GL-Caputo | `f746_rossler_gl.elf` | `h755_m7_rossler_gl.elf` |
| Rössler | M2sFRK | `f746_rossler_m2sfrk.elf` | `h755_m7_rossler_m2sfrk.elf` |
| Chen | EFORK3 | `f746_chen_efork3.elf` | `h755_m7_chen_efork3.elf` |
| Chen | GL-Caputo | `f746_chen_gl.elf` | `h755_m7_chen_gl.elf` |
| Chen | M2sFRK | `f746_chen_m2sfrk.elf` | `h755_m7_chen_m2sfrk.elf` |

Por ejemplo, la pareja fija correspondiente a Lorenz/M2sFRK es
`f746_lorenz_m2sfrk_fixed.elf` y
`h755_m7_lorenz_m2sfrk_fixed.elf`. Las nueve combinaciones
sistema--método de cada placa siguen la misma convención. Las 18 imágenes
fijas y sus archivos HEX compilan en Release; esta comprobación de build no
equivale a haber ejecutado ni validado físicamente las 18 condiciones.

El inventario reproducible de las 36 imágenes Release no destinadas a
benchmark está en
[`validation/results/resource_usage/`](validation/results/resource_usage/).
Registra SHA-256 de ELF/mapa y Flash/RAM estática mediante
`arm-none-eabi-size -B`. Es evidencia de enlace, no una medición de pila
máxima, heap, tiempo o energía.

En la H755 se acompaña cualquiera de los 18 binarios primarios del CM7 con
`h755_m4_uart.elf`. El CM7 se dedica al cálculo y el CM4 se dedica a la
transmisión UART mediante DMA.

## Uso rápido desde Visual Studio Code

Se abre **esta carpeta** (`STM32/FractionalChaos`) como carpeta de trabajo y se
selecciona **Terminal > Run Task**:

- `build:host:release` compila las pruebas del núcleo en el equipo.
- `test:host:release` ejecuta las pruebas deterministas y la referencia
  independiente.
- `build:f746:release` genera nueve binarios `float32` y nueve
  `fixed_q14_q30` de la F746.
- `build:h755:release` genera nueve binarios `float32`, nueve
  `fixed_q14_q30` del CM7 y el binario UART del CM4.
- `build:all:release` ejecuta las tres compilaciones anteriores.
- `flash:seleccionar` solicita placa, sistema, método y representación, y
  programa mediante ST-LINK.
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
  -Board f746 -System lorenz -Method efork3 `
  -Representation float32 `
  -ProbeSerial 00112233445566778899AABB
```

`-ProbeSerial` es obligatorio. Se obtiene con
`STM32_Programmer_CLI.exe -l stlink-only`; antes de escribir, el script valida
que ese serial pertenezca al `Board Name` esperado para `-Board`. La vía mixta
se selecciona con `-Representation fixed`. Para la H755 se usa `-Board h755`;
una misma tarea programa primero el HEX de cálculo del CM7 y después
`h755_m4_uart.hex`, mediante dos invocaciones verificables de
STM32CubeProgrammer.

## Arquitectura de ejecución

En la F746 se ejecuta el solucionador en el Cortex-M7 y se transmite una trama
seleccionada por USART3 mediante DMA. En la H755 se mantiene el solucionador y
el contador DWT en el Cortex-M7; se deposita cada muestra exportada en una cola
SPSC de SRAM4 y se forma y transmite FCC1 desde el Cortex-M4. Se utiliza HSEM
para la secuencia de arranque, no para cada muestra.

Los objetivos de firmware disponibles tienen dos vías. La vía `float32`
corresponde a la FPU de precisión simple de ambos Cortex-M7. En ella se
precomputan los pesos, se conserva el historial en un búfer circular, se
agrupan las sumas y se aplica compensación de Neumaier. Se emplea `fmaf`
explícito donde se requiere una sola ronda y no se habilita `-ffast-math`.

El diseño experimental incluye además estados y parámetros Q1.14.14: signo,
14 bits enteros y 14 fraccionarios. Para que la cuantización no borre la cola
de memoria de EFORK3 y GL, \(h^q\), los coeficientes y los pesos se almacenan
en Q1.30. Los productos se resuelven en `int64_t`, con redondeo al más cercano
—empates alejándose de cero— y saturación contabilizada. El
oráculo independiente y sus gráficas están en
`validation/fixed_point_comparison.py`. Esta referencia de host congela el
contrato aritmético. El kernel fijo ya está integrado en los objetivos de
ambas plataformas y sus 18 imágenes compilan, pero ni el oráculo ni la
compilación se presentan como mediciones STM32 o como validación de la
campaña física completa.

La comparación de corto horizonte
[`embedded_vs_abm_short_horizon`](validation/results/embedded_vs_abm_short_horizon/)
ejecuta los kernels C portables reales `float32` y `fixed_q14_q30` frente al
ABM `float64` de memoria completa a \(h/4\). Las 18 combinaciones
sistema--método--representación terminaron sin saturaciones, saturaciones de
coeficientes ni coeficientes no nulos cuantizados a cero. Es una comparación
numérica de host durante un segundo; no acredita temporización de placa,
equivalencia entre operadores ni dinámica a largo plazo.

La descripción completa se encuentra en
[`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md), y la revisión de los proyectos
heredados y de las decisiones numéricas se documenta en
[`docs/AUDITORIA_NUMERICA.md`](docs/AUDITORIA_NUMERICA.md).

## Oráculo numérico en el anfitrión

La referencia independiente de Caputo se implementa mediante ABM PECE de
memoria completa en [`validation/abm_oracle.py`](validation/abm_oracle.py).
Se ejecuta y documenta con:

```powershell
python .\validation\validate_abm_oracle.py
```

El resultado reproducible queda en
`validation/results/abm_oracle_validation.json`. La conservación de una
constante y la convergencia de una potencia manufacturada validan la
implementación del oráculo. El refinamiento corto de Lorenz, Rössler y Chen es
solo una prueba de integración.

La calificación ABM posterior, con criterios congelados, memoria completa,
\(h/2\), \(h/4\), 50 s de horizonte y 10 s de transitorio, terminó para los
tres manifiestos. Sólo Chen obtuvo
`qualified_observed_long_horizon_screen`; Lorenz y Rössler no superaron todas
las pantallas predeclaradas. Dos contratos bibliográficos exploratorios,
uno por cada sistema rechazado, tampoco resultaron elegibles (0/2), por lo que
no se congeló ningún reemplazo ni se modificaron umbrales. La decisión,
diagnósticos y límites están en
[`validation/results/abm_long_horizon/`](validation/results/abm_long_horizon/)
y
[`validation/results/abm_replacement_exploration_v1/`](validation/results/abm_replacement_exploration_v1/).
Esta calificación observacional no demuestra caos, atractor oculto ni
equivalencia con la memoria finita del firmware.

## Protocolo UART FCC1

Se transmite una trama binaria de 40 bytes, en orden *little-endian*, a
921600 bit/s, 8-N-1. Se incluyen identificadores de placa, sistema y método,
secuencia, ciclos del paso numérico, muestras descartadas, las tres palabras
de estado y un CRC-32/ISO-HDLC. `kind=1` conserva las palabras IEEE-754
`float32`; `kind=3` conserva directamente los enteros con signo Q1.14.14.
Al conservarse las palabras crudas se evita la pérdida introducida por una
conversión decimal y se dispone de la materia prima para formar las secuencias
de bits del experimento.

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

La evidencia física disponible es todavía de piloto. Además de una captura
Lorenz/EFORK3 `float32` diagnóstica en la F746, el checkpoint
Lorenz/M2sFRK conserva una adquisición por placa y representación. Los conteos
siguientes se obtienen después de descartar 64 muestras válidas:

| Placa | Representación | Decimación | Muestras | Bits LSB | Ciclos/paso mediana / p95 |
|---|---|---:|---:|---:|---:|
| F746 | `float32` | 512 | 44,275 | 1,062,600 | 325 / 325 |
| F746 | `fixed_q14_q30` | 512 | 43,456 | 1,042,944 | 618 / 783 |
| H755 | `float32` | 1024 | 43,865 | 1,052,760 | 266 / 266 |
| H755 | `fixed_q14_q30` | 512 | 46,630 | 1,119,120 | 463 / 521 |

Las cuatro adquisiciones tienen cero gaps, `dropped=0`, `status=0` y todas
las banderas de diagnóstico en cero. La captura H755 `float32` con decimación
512 perdió tramas a 921600 bit/s; la adquisición aceptada usa la decimación
calibrada 1024. Los ciclos DWT corresponden únicamente a un paso del
integrador y excluyen UART y decimación. Las razones descriptivas entre las
medianas fija/flotante son 1.9015 en la F746 y 1.7406 en la H755, pero proceden
de una sola adquisición por vía: no son estimaciones de la campaña de 30
arranques en frío ni factores de aceleración con incertidumbre.

Las capturas fijas comparten 3,117 secuencias. Las palabras Q1.14.14 crudas
coinciden en todas ellas, con cero discrepancias y SHA-256 común del solapamiento
`026b0f444bbfbb808e4a9fe483b4d0259a4f3f7e2bfd6ac89eecf8abce8511bd`.
Esta comprobación establece paridad física sólo en ese intervalo solapado.

Estas cuatro adquisiciones Lorenz se conservan como diagnósticos UART de
transporte, series temporales, proyecciones de estado y tramas LSB. La
calificación ABM vigente no promueve Lorenz a condición dinámica primaria.

Los conteos, hashes y límites se conservan en el
[`resumen físico float/fixed`](validation/results/hardware_smoke/m2sfrk_float_fixed_hardware_pilot_summary.json)
y en la
[`comparación de paridad fija`](validation/results/hardware_smoke/lorenz_m2sfrk_fixed_dec512_physical_parity.json).

La integridad de transporte y la elegibilidad estadística son decisiones
distintas. Una captura puede contener tramas FCC1 válidas, identidad coherente,
estado `status=0` y CRC correcto, y aun así quedar excluida de
NIST/TestU01/PractRand por discontinuidades de secuencia o por un contador
`dropped` no nulo. Los pilotos anteriores con pérdidas permanecen como
diagnósticos de hardware y transporte. Las cuatro adquisiciones de la tabla
cumplen la integridad de transporte y el umbral configurado de un millón de
bits. Sobre esas cuatro tramas se ejecutaron NIST SP 800-22 STS 2.1.2,
PractRand 0.96 y las baterías de archivo Rabbit/Alphabit de TestU01 1.2.3.
Los resultados, comandos, hashes, colas no evaluadas y límites de
interpretación se conservan en la
[`auditoría estadística del piloto`](validation/results/statistical_batteries_m2sfrk_pilot/summary.md).
Son salidas externas auditables sobre una secuencia por configuración, no una
declaración de validación estadística formal: se mantienen separadas de los
diagnósticos básicos y de la mera elegibilidad por transporte y longitud.

Además se aceptó el endpoint solver-only de 10,000 conteos DWT en las 12
celdas Chen (tres métodos, dos representaciones y dos placas): 12/12
capturas, 120,000 conteos crudos en total. Cada celda tiene únicamente
\(N=1\) repetición después de reset hardware por ST-LINK. No se retiró la
alimentación, de modo que esas ejecuciones no son arranques en frío ni
observaciones de la campaña primaria. El resumen trazable y su figura están
en
[`validation/results/physical_timing_reset_pilot_chen/`](validation/results/physical_timing_reset_pilot_chen/).

Por ello no se infieren resultados científicos a partir de una compilación ni
de una trayectoria corta. Se registran en placa los ciclos por paso y por bit,
las tramas descartadas, las series completas y los resultados de las pruebas
estadísticas. Siguen pendientes los 30 ciclos físicos de alimentación por
celda, el consumo y la energía, el watermark de pila, la ablación H0-E/H1
frente a H2 y las baterías largas con sus tamaños predeclarados.
