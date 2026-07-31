# Arquitectura del firmware

## Objetivo

Se comparte un mismo núcleo numérico entre la NUCLEO-F746ZG y la
NUCLEO-H755ZI-Q. Se evita mantener copias divergentes del algoritmo y se
seleccionan en compilación el sistema, el método y la tabla de coeficientes.
Cada ejecutable incorpora una sola configuración experimental.

Se implementan las ecuaciones:

\[
\begin{aligned}
\text{Lorenz:}\quad
D^q x &= \sigma(y-x),\\
D^q y &= x(\rho-z)-y,\\
D^q z &= xy-\beta z;
\end{aligned}
\]

\[
\begin{aligned}
\text{Rössler:}\quad
D^q x &= -y-z,\\
D^q y &= x+ay,\\
D^q z &= b+z(x-c);
\end{aligned}
\]

\[
\begin{aligned}
\text{Chen:}\quad
D^q x &= a(y-x),\\
D^q y &= (c-a)x-xz+cy,\\
D^q z &= xy-bz.
\end{aligned}
\]

Los parámetros, condiciones iniciales, órdenes, pasos y longitudes de memoria
se centralizan en el manifiesto de `common/fractional_chaos.c`.

## Capas

```text
Manifiesto experimental y tablas float32 generadas
                         |
                         v
            common/fractional_chaos.c
              EFORK3 | GL-Caputo
                         |
           +-------------+-------------+
           |                           |
           v                           v
 platform/f746                   platform/h755
 Cortex-M7: cálculo              Cortex-M7: cálculo
 USART3 TX por DMA               cola SPSC en SRAM4
                                       |
                                       v
                                Cortex-M4: FCC1 y
                                USART3 TX por DMA
```

`transport/fc_protocol.c` se utiliza para formar y validar las tramas FCC1.
`transport/fc_shared_queue.c` se utiliza en la H755 para la transferencia
unidireccional CM7→CM4.

## Núcleo numérico común

La vía flotante utiliza IEEE-754 binario de 32 bits en estados, coeficientes y
pesos. La vía fija primaria utiliza Q1.14.14 para estados y parámetros, y
Q1.30 para \(h^q\), coeficientes y pesos. Esta precisión mixta evita anular los
términos pequeños de la memoria fraccionaria. Los productos intermedios usan
`int64_t`, redondeo al más cercano con empates alejándose de cero y saturación
contabilizada. Las dos representaciones conservan el orden de operaciones
entre placas y se compilan como objetivos separados.

Se aplican las siguientes decisiones:

- Se generan fuera de la placa \(h^q\), los coeficientes y los pesos.
- Se enlaza únicamente la tabla de la configuración seleccionada.
- Se copia la tabla activa a DTCM durante la inicialización.
- Se recorre el historial mediante un búfer circular, sin desplazamientos.
- En EFORK3 se acumulan las tres convoluciones en un solo recorrido.
- En GL se almacenan las desviaciones respecto del estado inicial para
  conservar la formulación alineada con Caputo.
- Se agrupan los términos y se aplica compensación de Neumaier a las sumas.
- Se utiliza `fmaf` de forma explícita en las expresiones seleccionadas.
- Se deshabilita `-ffast-math`; no se permite reasociación indiscriminada.
- No se reserva memoria dinámica y no se invoca HAL dentro del paso numérico.
- Se comprueba que el estado permanezca finito y se emite un estado de error
  cuando se detecta una salida no finita.

Estas medidas reducen el error evitable y hacen reproducibles las palabras
`float32`. No convierten el resultado en una referencia de precisión
arbitraria; la evaluación de error se realiza contra una referencia externa
de mayor precisión para cada método.

## Comparación entre EFORK3 y GL-Caputo

No se presupone que dos discretizaciones derivadas de operadores distintos
sean matemáticamente intercambiables. Se separan dos niveles de comparación:

1. Para cada método se cuantifica su error frente a su propia referencia de
   alta precisión, con la misma formulación, el mismo estado inicial y el
   mismo horizonte de memoria.
2. Entre métodos se comparan observables comunes: clasificación dinámica,
   métricas de trayectoria, tiempo por paso, memoria enlazada y activa,
   rendimiento de generación de bits y resultados de las pruebas
   estadísticas.

Se utilizan el mismo manifiesto físico y la misma ventana de memoria de diez
segundos en ambas placas. Cuando \(h\) cambia entre sistemas también cambia
\(M\), de modo que no se confunden diez segundos de memoria con un número
arbitrario de muestras.

La comparación dinámica se interpreta mediante métricas y tolerancias
predefinidas, no mediante igualdad muestra a muestra durante horizontes
caóticos largos. La comparación de rendimiento se normaliza tanto por paso
como por bit aceptado.

## NUCLEO-F746ZG

En la F746 se utiliza un único Cortex-M7 a 216 MHz:

- Se habilitan las cachés de instrucciones y datos.
- Se coloca el solucionador y su historial en DTCM.
- Se utiliza DWT/CYCCNT para medir exclusivamente la llamada al solucionador.
- Se utiliza USART3 TX a 921600 bit/s.
- Se coloca el búfer de transmisión en SRAM accesible por DMA.
- Se limpia la línea de caché correspondiente antes de iniciar el DMA.
- Se transmite una muestra según la decimación de salida configurada.

No se habilita recepción UART. Tampoco se inicializan Ethernet, USB, I2C, DAC,
ADC ni temporizadores periféricos. DWT pertenece al núcleo y se usa como
contador de ciclos sin introducir una interrupción periódica.

## NUCLEO-H755ZI-Q

En la H755 se dividen las responsabilidades:

### Cortex-M7

Se ejecuta el solucionador, se mide el paso mediante DWT/CYCCNT y se deposita
la muestra exportada en una cola SPSC. El historial se conserva en DTCM. La
región compartida de SRAM4 se configura sin caché para que la visibilidad
entre núcleos no dependa de operaciones de mantenimiento por cada registro.

### Cortex-M4

Se extrae la muestra de la cola, se forma FCC1 y se transmite por USART3 TX
mediante DMA. No se ejecuta el solucionador en este núcleo y no se habilita
USART RX.

### Arranque y comunicación

Se coloca la cola en la misma dirección de SRAM4 mediante las dos
descripciones de enlace. Se utiliza HSEM0 para liberar y sincronizar el
arranque del CM4. Una vez iniciados ambos núcleos, no se toma un semáforo por
muestra: la cola SPSC utiliza secuencias monotónicas y barreras de memoria.
Cuando el productor alcanza al consumidor se incrementa el contador
`dropped`; no se bloquea el cálculo.

En una placa de fábrica se utiliza `DIRECT_SMPS` con 400 MHz en el CM7 y
200 MHz en el CM4. La configuración 480/240 MHz se considera únicamente
cuando se confirma la modificación física a LDO establecida por ST. Se evita
presentar la frecuencia máxima del dispositivo como si fuera la frecuencia
de la prueba.

## Protocolo FCC1

Cada trama ocupa 40 bytes y se codifica en orden *little-endian*:

| Desplazamiento | Tamaño | Campo | Descripción |
|---:|---:|---|---|
| 0 | 4 | `sync` | `FCC1`, palabra `0x31434346` |
| 4 | 1 | `version` | Versión 1 |
| 5 | 1 | `kind` | Estado `float32` = 1; estado Q1.14.14 = 3; bloque DWT = 4 |
| 6 | 1 | `board_id` | F746 = 1, H755 = 2 |
| 7 | 1 | `system_id` | Lorenz = 0, Rössler = 1, Chen = 2 |
| 8 | 1 | `method_id` | EFORK3 = 0, GL-Caputo = 1, M2sFRK = 2 |
| 9 | 1 | `status` | Correcto, no finito o cola |
| 10 | 2 | `payload_bytes` | 24 |
| 12 | 4 | `sequence` | Índice de paso |
| 16 | 4 | `cycles` | Ciclos del núcleo numérico |
| 20 | 4 | `dropped` | Muestras no transmitidas |
| 24 | 4 | `x_bits` | Palabra IEEE-754 o entero Q1.14.14 crudo de \(x\) |
| 28 | 4 | `y_bits` | Palabra IEEE-754 o entero Q1.14.14 crudo de \(y\) |
| 32 | 4 | `z_bits` | Palabra IEEE-754 o entero Q1.14.14 crudo de \(z\) |
| 36 | 4 | `crc32` | CRC-32/ISO-HDLC de los bytes 0–35 |

Se conservan simultáneamente el valor real reconstruido y la palabra
hexadecimal al convertir una captura a CSV. De esta forma se permite revisar
la trayectoria y reproducir exactamente la extracción posterior de bits.

En `kind=4`, `sequence` es el índice base cero del primer paso medido y
`cycles`, `x_bits`, `y_bits`, `z_bits` son cuatro conteos DWT `uint32_t`
consecutivos. El firmware acumula primero 10 000 valores con UART inactiva y
emite 2500 bloques sólo después de cerrar la ventana. Este modo conserva la
distribución cruda de timing; no transporta estados ni alimenta el extractor
LSB.

## Recursos inicializados

| Recurso | F746 | H755 CM7 | H755 CM4 |
|---|---:|---:|---:|
| Reloj del núcleo y alimentación | Sí | Sí | Sí |
| I-cache/D-cache | Sí | Sí | Según el núcleo y la región |
| FPU `float32` | Sí | Sí | No se usa para el cálculo |
| DWT/CYCCNT | Sí | Sí | No |
| DTCM para historial | Sí | Sí | No |
| SRAM4 compartida | No | Sí | Sí |
| HSEM de arranque | No | Sí | Sí |
| USART3 TX | Sí | No | Sí |
| DMA de USART3 TX | Sí | No | Sí |
| USART3 RX | No | No | No |
| Ethernet/USB/I2C/DAC/ADC | No | No | No |

## Límite de la evidencia

Se distinguen cuatro verificaciones:

1. La compilación y el enlace demuestran que se resuelven dependencias, que
   las regiones caben y que se generan los ejecutables.
2. Las pruebas del equipo demuestran propiedades del núcleo, de los búferes y
   de referencias cortas deterministas.
3. La adquisición física demuestra frecuencia real, ciclos, continuidad UART,
   descartes y consumo de recursos sobre cada placa.
4. El análisis externo demuestra las métricas caóticas y las propiedades
   estadísticas de las secuencias de bits.

No se sustituye el tercer o cuarto nivel por los dos primeros. El código se
encuentra dispuesto para las dos placas, mientras que las cifras publicables
se aceptan cuando se completa la ejecución física bajo el protocolo
experimental.
