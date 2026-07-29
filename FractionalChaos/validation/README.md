# Oráculo ABM Caputo de memoria completa

Este directorio contiene la referencia numérica independiente del firmware.
`abm_oracle.py` implementa el predictor-corrector
Adams--Bashforth--Moulton PECE de Diethelm para sistemas conmensurables de
Caputo, conserva toda la historia desde el terminal inferior y utiliza
`float64` en el anfitrión. No importa ni ejecuta los kernels EFORK3 o
GL-Caputo de `common/`.

## Validación reproducible

Desde la raíz `FractionalChaos`:

```powershell
python .\tests\test_abm_oracle.py
python .\validation\validate_abm_oracle.py
```

El segundo comando genera
`validation/results/abm_oracle_validation.json`. El informe incluye:

- conservación de una solución constante;
- convergencia de la solución manufacturada \(x(t)=t^4\), con un lado
  derecho dependiente del estado;
- refinamiento corto \(h\), \(h/2\), \(h/4\) para los manifiestos candidatos
  de Lorenz, Rössler y Chen;
- hashes SHA-256 del código del oráculo y de los manifiestos de entrada.

La prueba manufacturada exige reducción monótona del error y un orden
observado compatible con \(\min(2,1+q)\), con un margen numérico declarado en
el JSON. La misma prueba forma parte de CTest con el nombre
`fractional_abm_oracle`.

La dependencia del host se instala, cuando sea necesario, con:

```powershell
python -m pip install -r .\validation\requirements.txt
```

## Límite de la evidencia

El estado `passed_abm_implementation_validation` acredita la implementación
del algoritmo ABM frente a soluciones conocidas. El bloque
`candidate_smoke` solo comprueba finitud y refinamiento durante un segundo.
No acredita por sí mismo:

- acotamiento o no periodicidad a largo plazo;
- caos, exponentes de Lyapunov ni atractores ocultos;
- aceptación de los tres manifiestos;
- rendimiento, energía o aleatoriedad en STM32.

La aceptación de un manifiesto requiere ejecutar los horizontes
pre-registrados, revisar observables estables y, después, congelar manifiesto,
coeficientes y vectores dorados. Esa capa de horizonte largo ya fue ejecutada:
sólo Chen superó todas las pantallas congeladas. Lorenz y Rössler permanecen
rechazados y no deben promoverse a la campaña primaria.

## Evidencia de capturas y tramas LSB

`analyze_capture.py` conserva la procedencia de una captura UART decodificada
y genera:

- las tres series temporales;
- las proyecciones `xy`, `xz` y `yz` del atractor;
- una trama binaria formada al concatenar, por muestra, los LSB de `x`, `y`
  y `z`;
- una trama visual de esos bits;
- un JSON con hashes, orden de concatenación, descarte y representación.

La vía alineada con los trabajos de De la Fraga proyecta primero las variables
a punto fijo y exige declarar el número de bits fraccionarios:

```powershell
python validation/analyze_capture.py captura.csv `
  --output-dir validation/results/captura `
  --mode fixed-point --fractional-bits 14 --lsb-bits 8 `
  --expected-decimation 16
```

Cuando la captura procede de un kernel Q1.14.14, no se vuelve a cuantizar:

```powershell
python validation/analyze_capture.py captura_fija.csv `
  --output-dir validation/results/captura_fija `
  --mode fixed-raw --lsb-bits 8 --expected-decimation 16
```

En ese modo se extraen directamente los ocho LSB de los enteros crudos
transportados en una trama FCC1 `kind=3`.

El modo `float-word` extrae LSB de las palabras IEEE-754 recibidas y se conserva
sólo como diagnóstico: no es equivalente a los LSB de aritmética en punto fijo.
El JSON marca una trama como no apta para baterías estadísticas si encuentra
saltos de secuencia o paquetes descartados.

Para una adquisición densa que conserva un estado por paso, `--sample-interval`
permite expresar el eje horizontal en tiempo del modelo. El origen es la primera
muestra retenida; no se usan marcas de llegada UART y no se interpola ni
remuestrea:

```powershell
python validation/analyze_capture.py captura_densa.csv `
  --output-dir validation/results/captura_densa `
  --mode fixed-point --fractional-bits 14 --lsb-bits 8 `
  --discard 2000 --expected-decimation 1 --sample-interval 0.005
```

## Comparación flotante frente a punto fijo mixto

`fixed_point_comparison.py` ejecuta los tres sistemas con EFORK3, GL-Caputo y
M2sFRK mediante dos referencias independientes. Los estados y parámetros fijos
usan Q1.14.14; \(h^q\), coeficientes y pesos usan Q1.30 para conservar los
términos pequeños del historial. El punto fijo redondea después de cada
producto y suma, satura en vez de envolver y registra saturaciones y
coeficientes no nulos que se hayan cuantizado a cero. Una celda con cualquiera
de esos eventos queda fuera de la comparación aritmética.

```powershell
python validation/fixed_point_comparison.py --steps 256
```

Por celda se guardan CSV, series temporales, tres proyecciones de trayectoria,
una trama con 8 LSB de `x`, `y` y `z` por iteración y un JSON de métricas.
`validation/results/fixed_point_comparison/summary.json` declara expresamente
que estos resultados comparan una fórmula `float64` con la aritmética fija
mixta en el anfitrión: todavía no representan el kernel `float32` ni evidencia
de ejecución física en las placas.

### Kernels C portables frente al ABM

La comparación complementaria ejecuta el código C real compartido por los
targets, en sus vías `float32` y `fixed_q14_q30`, durante un segundo y lo
contrasta con ABM Caputo `float64` de memoria completa a \(h/4\):

```powershell
python .\validation\compare_embedded_to_abm.py `
  --dump-executable .\build\host-release\tests\fractional_embedded_trajectory_dump.exe
python -m pytest .\tests\test_embedded_vs_abm.py -q
```

Los artefactos de
[`results/embedded_vs_abm_short_horizon/`](results/embedded_vs_abm_short_horizon/)
registran 18/18 celdas
sistema--método--representación sin saturaciones de estado, saturaciones de
coeficientes ni coeficientes no nulos cuantizados a cero. Las métricas RMSE y
error máximo son resultados de corto horizonte del host; no establecen
temporización o energía de placa, dinámica a largo plazo ni equivalencia entre
operadores fraccionarios distintos.

### Inventario estático Release

`collect_resource_usage.py` aplica `arm-none-eabi-size -B` a las 36 imágenes
Release no destinadas a benchmark y conserva hashes de ELF y mapa:

```powershell
python .\validation\collect_resource_usage.py
python -m pytest .\tests\test_resource_usage.py -q
```

[`results/resource_usage/resource_usage.json`](results/resource_usage/resource_usage.json)
separa la imagen solver y, para H755, el acompañante CM4 común. Sus campos
Flash y RAM son un inventario estático de enlace; no miden watermark de pila,
heap, energía, tiempo ni fallos de memoria en ejecución.

### Checkpoint físico M2sFRK

El resumen
[`m2sfrk_float_fixed_hardware_pilot_summary.json`](results/hardware_smoke/m2sfrk_float_fixed_hardware_pilot_summary.json)
conserva una adquisición Lorenz/M2sFRK por placa y representación, después de
descartar 64 muestras válidas:

| Placa | Representación | Decimación | Muestras | Bits | Ciclos/paso mediana / p95 |
|---|---|---:|---:|---:|---:|
| F746 | `float32` | 512 | 44,275 | 1,062,600 | 325 / 325 |
| F746 | `fixed_q14_q30` | 512 | 43,456 | 1,042,944 | 618 / 783 |
| H755 | `float32` | 1024 | 43,865 | 1,052,760 | 266 / 266 |
| H755 | `fixed_q14_q30` | 512 | 46,630 | 1,119,120 | 463 / 521 |

Las cuatro capturas tienen cero gaps, `dropped=0`, `status=0` y banderas de
diagnóstico en cero. La primera prueba H755 `float32` con decimación 512 perdió
tramas a 921600 bit/s; la captura aceptada usa 1024. La ventana DWT excluye
UART y decimación. Las razones fija/flotante de las medianas son 1.9015 y
1.7406 para F746 y H755, respectivamente, pero son descriptores de piloto, no
estimaciones de 30 arranques en frío.

La
[`paridad física fija`](results/hardware_smoke/lorenz_m2sfrk_fixed_dec512_physical_parity.json)
compara 3,117 secuencias solapadas: no encuentra discrepancias y obtiene el
mismo SHA-256 crudo,
`026b0f444bbfbb808e4a9fe483b4d0259a4f3f7e2bfd6ac89eecf8abce8511bd`,
en ambas placas. El resultado queda limitado a ese solapamiento piloto. Los
cuatro archivos son elegibles para el cribado configurado de transporte y
longitud. Sobre ellos se ejecutaron NIST SP 800-22 STS 2.1.2, PractRand 0.96
y las baterías de archivo Rabbit/Alphabit de TestU01 1.2.3. La salida
reproducible, incluidos hashes, comandos y colas no evaluadas, está en
[`results/statistical_batteries_m2sfrk_pilot/summary.md`](results/statistical_batteries_m2sfrk_pilot/summary.md).
Se trata de resultados externos sobre una sola secuencia por configuración;
no se presentan como aprobación de una batería formal ni como validación de
aleatoriedad.

En concreto, NIST procesó cada secuencia física completa y señaló entre una y
cinco pruebas con \(p<0.01\), mientras que Random Excursions y su variante
quedaron no aplicables. PractRand sólo cubrió prefijos de 127--136 KiB y
produjo salidas `normal` o `normalish`. Rabbit y Alphabit de TestU01
informaron `all_tests_passed` sobre las palabras completas disponibles, con
colas de 0--24 bits no reutilizadas. La existencia de una sola secuencia por
configuración impide interpretar estos resultados cortos como proporciones de
aprobación, uniformidad formal o evidencia a gran escala.

## Piloto físico de temporización por reset

El endpoint `benchmark_reset_pilot` mantiene UART fuera de la ventana medida,
almacena 10,000 conteos DWT crudos y los transmite sólo al terminar. Se
aceptaron las 12 celdas Chen —tres métodos, dos representaciones y dos
placas—, con 10,000 valores por celda y 120,000 valores en total:

- estado: `accepted_12_of_12_reset_pilot_cells`;
- unidad experimental: repetición de reset hardware por ST-LINK;
- repeticiones aceptadas por celda: \(N=1\);
- elegibilidad como arranque en frío y benchmark primario: falsa.

El resumen, CSV y figura se encuentran en
[`results/physical_timing_reset_pilot_chen/`](results/physical_timing_reset_pilot_chen/).
ST-LINK no retiró la alimentación, por lo que estos pilotos no reemplazan los
30 ciclos de alimentación por celda ni soportan inferencia entre reinicios.

## Calificación dinámica ABM de horizonte largo

La validación de la implementación del oráculo y la calificación de los
manifiestos son capas distintas. `validate_abm_oracle.py` comprueba el algoritmo
con soluciones conocidas. Después, `long_horizon_qualification.py` exige que
ese informe siga vigente y ejecuta los criterios congelados en
`long_horizon_criteria.json`:

```powershell
python .\validation\validate_abm_oracle.py
python .\validation\long_horizon_qualification.py
python .\tests\test_abm_long_horizon_qualification.py
```

La calificación usa memoria completa y `float64` en \(h/2\) y \(h/4\), con
50 s de horizonte y 10 s de transitorio. Evalúa acotamiento observado,
actividad, una pantalla conservadora de recurrencia/entropía, estabilidad por
bloques y estabilidad distributiva entre resoluciones. No exige coincidencia
puntual de trayectorias caóticas a horizonte largo.

Los artefactos se escriben en `results/abm_long_horizon/`:

- `qualification.json`: criterios, procedencia, decisiones y limitaciones;
- `qualification_summary.csv`: una fila por manifiesto y resolución;
- `qualification_blocks.csv`: diagnósticos de los cuatro bloques temporales;
- `qualification_trajectory_samples.csv`: muestras a periodo común de 0.05 s.

Una decisión `qualified_observed_long_horizon_screen` se limita al horizonte y
a las pantallas predeclaradas. No demuestra matemáticamente acotamiento, caos,
atractor oculto ni equivalencia con el firmware de memoria finita.

La ejecución registrada calificó únicamente `chen_caputo_v1`. Lorenz falló la
estabilidad dentro de resolución en \(h/4\); Rössler falló estabilidad por
bloques en ambas resoluciones y la pantalla de entropía. Ambos conservaron
finitud y no se reajustaron umbrales después de observar el resultado.

### Exploración de reemplazos Lorenz y Rössler

Los contratos exactos tomados de Feng et al. (2023) para Lorenz y de Wang,
Wang y Li (2024) para Rössler quedaron fijados antes de ejecutar
`explore_manifest_replacements.py` en
`replacement_candidate_grid_v1.json`. La exploración reutiliza los criterios
congelados, pero escribe en una capa distinta y no constituye calificación
formal:

```powershell
python .\validation\explore_manifest_replacements.py
python .\tests\test_abm_manifest_replacements.py
```

El proceso termina con código 1 cuando falta al menos un reemplazo elegible.
En la ejecución registrada fallaron ambos: Lorenz no superó estabilidad de
bloque en \(h/2\) ni la comparación cuantil entre resoluciones, y Rössler no
superó estabilidad de bloque en ninguna resolución. La decisión y sus límites
están en
[`results/abm_replacement_exploration_v1/DECISION.md`](results/abm_replacement_exploration_v1/DECISION.md).
No se congeló un manifiesto v2 ni se modificaron umbrales.

## Bloqueadores de cierre

La evidencia actual no cierra la campaña primaria. Faltan los 30 ciclos
físicos de alimentación por celda promovible, energía y corriente, watermark
de pila, la ablación de comunicación H0-E/H1/H2 y las baterías largas
predeclaradas. Lorenz y Rössler requieren contratos que superen la
calificación congelada antes de incorporarse como resultados dinámicos
primarios.
