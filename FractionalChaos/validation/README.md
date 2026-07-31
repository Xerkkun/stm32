# Oráculo ABM Caputo de memoria completa

Este directorio contiene la referencia numérica independiente del firmware.
`abm_oracle.py` implementa el predictor-corrector
Adams--Bashforth--Moulton PECE de Diethelm para sistemas conmensurables de
Caputo, conserva toda la historia desde el terminal inferior y utiliza
`float64` en el anfitrión. No importa ni ejecuta los kernels EFORK3 o
GL-Caputo de `common/`.

El firmware C soporta cinco sistemas: Lorenz, Rössler, Chen, Liu y
Hammouch--Mekkaoui. La cohorte congelada para evaluación embebida es
**Chen + Liu + Hammouch--Mekkaoui** y su contrato está en
[`selected_system_manifests_v1.json`](selected_system_manifests_v1.json).
Lorenz y Rössler se conservan como condiciones históricas o diagnósticas
soportadas, no como integrantes de la matriz primaria seleccionada.

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

### Densidad dinámica descriptiva

`analyze_dynamics_density.py` cuantifica una captura densa aceptada sin
interpolar ni remuestrear sus estados. Rechaza gaps de secuencia, filas
descartadas, banderas del solver y valores no finitos:

```powershell
python validation/analyze_dynamics_density.py `
  --capture captura.csv --output densidad.json `
  --discard 5000 --sample-interval 0.01
```

El JSON conserva el hash de la captura, rangos y desviaciones de estado,
ocupación y entropía de histogramas `xy`, `xz` y `yz` en una malla
\(64\times64\), ocupación tridimensional en \(32^3\), entropía de permutación
Bandt--Pompe, recurrencia entre 1 y 15 s y los máximos radiales
\(\sqrt{x^2+y^2}\). La densidad se calcula después de estandarizar cada
componente y limitarla al intervalo \([-4,4]\). El alcance del informe es una
descripción cuantitativa de la serie UART; la identificación de caos,
atractores ocultos o aleatoriedad requiere evidencia independiente.

### Paridad densa entre placas

`compare_dense_capture_parity.py` compara dos capturas de la misma celda
sistema--método--representación. La paridad exige la misma secuencia y las
mismas palabras `x_bits`, `y_bits` y `z_bits`, además de `status` y `dropped`,
en todas las filas:

```powershell
python validation/compare_dense_capture_parity.py `
  captura_f746.csv captura_h755.csv `
  --output paridad.json
```

El informe conserva los hashes de ambos CSV y del payload canónico comparado,
el número de filas y el primer desacuerdo. Los ciclos DWT se excluyen de la
paridad porque describen el tiempo de cada placa y no el estado numérico.

## Comparación flotante frente a punto fijo mixto

`fixed_point_comparison.py` ejecuta los tres sistemas seleccionados con
EFORK3, GL-Caputo y M2sFRK mediante dos referencias independientes. Los
estados y parámetros fijos usan Q1.14.14; \(h^q\), coeficientes y pesos usan
Q1.30 para conservar los términos pequeños del historial. El punto fijo
redondea después de cada producto y suma, satura en vez de envolver y registra
saturaciones y coeficientes no nulos que se hayan cuantizado a cero. Una celda
con cualquiera de esos eventos queda fuera de la comparación aritmética.

```powershell
python validation/fixed_point_comparison.py --steps 256 `
  --manifests validation/selected_system_manifests_v1.json `
  --output validation/results/fixed_point_selected_v1
```

Por celda se guardan CSV, series temporales, tres proyecciones de trayectoria,
una trama con 8 LSB de `x`, `y` y `z` por iteración y un JSON de métricas.
[`results/fixed_point_selected_v1/summary.json`](results/fixed_point_selected_v1/summary.json)
registra 9/9 celdas seleccionadas con el contrato aritmético aprobado. Estos
resultados comparan una fórmula independiente `float64` con la aritmética fija
mixta en el anfitrión: no representan el kernel C `float32` ni evidencia de
ejecución física en las placas.

### Kernels C portables frente al ABM

La comparación complementaria ejecuta el código C real compartido por los
targets, en sus vías `float32` y `fixed_q14_q30`, durante un segundo y lo
contrasta con ABM Caputo `float64` de memoria completa a \(h/4\):

```powershell
python .\validation\compare_embedded_to_abm.py `
  --dump-executable .\build\host-release\tests\fractional_embedded_trajectory_dump.exe `
  --manifests .\validation\selected_system_manifests_v1.json `
  --output-dir .\validation\results\embedded_vs_abm_selected_short_horizon_v1
python -m pytest .\tests\test_embedded_vs_abm.py -q
```

Los artefactos de
[`results/embedded_vs_abm_selected_short_horizon_v1/`](results/embedded_vs_abm_selected_short_horizon_v1/)
registran el estado `completed_no_arithmetic_contract_failures`: 18/18 celdas
seleccionadas sistema--método--representación sin saturaciones de estado o
coeficientes ni coeficientes no nulos cuantizados a cero. Las métricas RMSE y
error máximo son resultados de corto horizonte del host; no establecen
temporización o energía de placa, dinámica a largo plazo, aleatoriedad ni
equivalencia entre operadores fraccionarios distintos.

### Inventario estático Release

`collect_resource_usage.py` aplica `arm-none-eabi-size -B` a las 36 imágenes
Release no destinadas a benchmark y conserva hashes de ELF y mapa:

```powershell
python .\validation\collect_resource_usage.py
python -m pytest .\tests\test_resource_usage.py -q
```

[`results/resource_usage_selected_v1/resource_usage.json`](results/resource_usage_selected_v1/resource_usage.json)
registra las 36/36 imágenes seleccionadas y separa la imagen solver y, para
H755, el acompañante CM4 común. El SHA-256
`d9d554ca9126ecf9a86f3931fb41da9d4395521ffcb7d2470fa57fb1d6b7d259`
del manifiesto seleccionado está embebido en cada ELF solver y en el
acompañante CM4. Los campos Flash y RAM son un inventario estático de enlace;
no miden watermark de pila, heap, energía, tiempo ni fallos de memoria en
ejecución.

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

## Referencia de reloj INA14/1

`clock_reference.py` valida, sin adquirir datos, una captura JSONL `INA14/1`.
Exige exactamente un flanco ascendente y uno descendente con
`marker_kind=clock_reference`, comprueba el pulso nominal de 100 ms y calcula
`effective_core_clock_hz = expected_cycles / measured_duration_s`:

```powershell
python .\validation\clock_reference.py `
  --capture .\captura.jsonl --expected-cycles 21600000 `
  --firmware-profile f746_216mhz `
  --output .\clock_reference.json
```

La tolerancia predeterminada es ±1 ms, puede configurarse con
`--duration-tolerance-ms` y no puede ampliarse más allá de ±10 ms. Los ciclos
esperados se contrastan con perfiles congelados: `f746_216mhz` exige
21,600,000 ciclos y `h755_400mhz` exige 40,000,000 ciclos. Un valor arbitrario,
un perfil cruzado o la imagen H755 opcional de 480 MHz se rechazan mientras no
exista otro perfil predeclarado. El validador exige que esos argumentos
coincidan exactamente con `header.clock_reference_contract`, incluido
`source=host_selected_frozen_campaign_profile` y
`profile_is_measurement=false`. Este bloque registra una selección del host,
no una medición ni una atestación del binario cargado. Sin un artefacto separado
`fractional-chaos-arduino-timebase-calibration-v1`, ligado al mismo
`controller_id`, cuya entrada `traceability_artifact` apunte a un archivo cuyo
SHA-256 coincida con el declarado, la frecuencia queda como diagnóstico y
`publication_ready=false`.

## Pilotos físicos de temporización por reset

### Checkpoint histórico Chen

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

### Matriz de ingeniería de la cohorte seleccionada

El mismo endpoint se ejecutó después sobre las 36 celdas de
Chen/Liu/Hammouch--Mekkaoui, tres métodos, dos representaciones y dos placas.
La matriz de ingeniería quedó aceptada 36/36, con 10,000 conteos DWT
solver-only por celda. La selección final toma 33 celdas aceptadas de `r04` y
los tres reintentos aceptados de `r05`.

- unidad experimental: una repetición de reset hardware por ST-LINK;
- repeticiones aceptadas por celda: \(N=1\);
- alimentación retirada: `power_removed=false`;
- procedencia del código: `source.dirty=true`;
- elegibilidad como preflight físico, arranque en frío o benchmark primario:
  falsa.

Los `run.json`, conteos crudos, capturas, hashes y logs por ejecución están en
[`results/physical_campaign/stm32_selected_36x30_v1/runs/`](results/physical_campaign/stm32_selected_36x30_v1/runs/).
Esta capa comprueba programación, identidad, transporte y temporización
solver-only bajo reset; no sustituye las 30 repeticiones con ciclo real de
alimentación ni la medición de energía.

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
Esa exploración no congeló un manifiesto v2 ni modificó umbrales.

### Selección de sistemas alternativos

Una cohorte posterior evaluó Lü, Genesio--Tesi simplificado,
Shimizu--Morioka y Liu sin modificar los umbrales de horizonte largo. Los
campos vectoriales alternativos permanecen aislados en
`alternative_systems.py`, de modo que esta extensión no altera los hashes de
la evidencia histórica Lorenz--Rössler--Chen.

```powershell
python .\validation\validate_alternative_system_oracle.py
python .\validation\qualify_alternative_systems.py
python -m pytest .\tests\test_alternative_system_artifacts.py -q
```

Lü v1, Genesio--Tesi y Liu superaron las pantallas dinámicas; la regla
predeclarada de dos plazas ordenó Lü, Liu y Genesio--Tesi por margen. Una
auditoría bibliográfica posterior impidió promover el Lü v1 como reproducción
cerrada: la fuente de Yadav et al. publica el sistema y sus parámetros, pero
su procedimiento numérico impreso no contiene la historia fraccionaria y no
declara \(h\).

La corrección `lu_caputo_v2` se eligió por completitud de procedencia antes de
calcularla. Usa el caso de Chen et al. con Caputo predictor-corrector,
\(h=0.01\), \(q=0.90\), \((a,b,c)=(35,3,28)\) y
\(\mathbf{x}_0=(0,3,9)\). Pasó finitud, actividad, estabilidad por bloques,
entropía y consistencia entre resoluciones, pero en \(h/4\) obtuvo RMSE de
recurrencia 0.032798, por debajo del mínimo 0.05. Conserva por ello la decisión
`not_qualified_dynamic_screen_failed`.

El resultado de esa ronda conservó `liu_caputo_v1` para la puerta C/firmware,
que posteriormente superó dentro de la cohorte embebida seleccionada, y dejó
una plaza alternativa abierta. Genesio--Tesi se conserva como una IVP Caputo
calificada numéricamente, no como reproducción de su algoritmo publicado ni
como sustituto automático.

La plaza restante se resolvió mediante una segunda cohorte bibliográfica
congelada antes de ejecutar las trayectorias:

```powershell
python .\validation\validate_alternative_system_oracle.py `
  --candidate-grid .\validation\alternative_system_round2_grid_v1.json `
  --manifests .\validation\alternative_system_round2_manifests_v1.json `
  --output .\validation\results\alternative_system_round2_oracle_v1.json
python .\validation\qualify_alternative_systems.py `
  --candidate-grid .\validation\alternative_system_round2_grid_v1.json `
  --criteria .\validation\alternative_system_round2_criteria_v1.json `
  --manifests .\validation\alternative_system_round2_manifests_v1.json `
  --implementation-report .\validation\results\alternative_system_round2_oracle_v1.json `
  --output-dir .\validation\results\alternative_system_round2_v1
```

Hammouch--Mekkaoui, Muñoz--Pacheco y glucosa--insulina superaron todas las
pantallas. La regla de una promoción ordenó por margen mínimo y seleccionó
`hammouch_mekkaoui_caputo_v1` con 1.5088; glucosa--insulina obtuvo 1.3216 y
Muñoz--Pacheco 1.0389. La cohorte congelada para evaluación embebida es
**Chen + Liu + Hammouch--Mekkaoui**. Liu y Hammouch--Mekkaoui ya están
portados al C portable y al firmware; la cohorte completa pasó 18/18 celdas
C--ABM, 9/9 celdas fijas y el inventario de 36/36 imágenes Release. El
preflight físico y la campaña primaria permanecen pendientes. La decisión
completa, incluidas las razones de cada prueba, está en
[`results/alternative_system_selection_decision_v2.md`](results/alternative_system_selection_decision_v2.md).

### Contrato activo Rössler clásico v2

El contrato `rossler_classic_caputo_v2` se adoptó después como una revisión
operativa independiente, sustentada en el régimen clásico
\(a=b=0.2,\ c=5.7,\ q=0.9877,\ \mathbf{x}_0=(1,0,0)\). Su fuente canónica es
`candidate_manifests_rossler_classic_v2.json`; el manifiesto v1 y sus
resultados permanecen inalterados.

La evaluación formal reutiliza sin cambios los umbrales v1:

```powershell
python .\validation\validate_abm_oracle.py `
  --manifests .\validation\candidate_manifests_rossler_classic_v2.json `
  --output .\validation\results\abm_oracle_validation_rossler_classic_v2.json
python .\validation\long_horizon_qualification.py `
  --manifests .\validation\candidate_manifests_rossler_classic_v2.json `
  --criteria .\validation\long_horizon_criteria_rossler_classic_v2.json `
  --implementation-report .\validation\results\abm_oracle_validation_rossler_classic_v2.json `
  --output-dir .\validation\results\abm_long_horizon_rossler_classic_v2
```

Rössler v2 supera acotamiento observado, actividad, no periodicidad y la
comparación distributiva entre \(h/2\) y \(h/4\). Su entropía de permutación
normalizada es \(0.251264\), apenas por encima del umbral 0.25. La razón mínima
entre la desviación de un bloque y la desviación global es 0.018406 en \(h/2\)
y 0.018407 en \(h/4\), por debajo del umbral 0.25; por ello su decisión formal
es `not_qualified_dynamic_screen_failed`. El cambio produce una geometría
física más poblada para la comparación descriptiva, pero Chen conserva el
papel de único manifiesto primario calificado a horizonte largo.

## Bloqueadores de cierre

La evidencia actual no cierra la campaña primaria. Faltan los 30 ciclos
físicos de alimentación por celda promovible, energía y corriente, watermark
de pila, la ablación de comunicación H0-E/H1/H2 y las baterías largas
predeclaradas. Lorenz y Rössler permanecen como condiciones soportadas
históricas o diagnósticas; sólo podrían reincorporarse como resultados
dinámicos primarios mediante contratos que superen la calificación congelada.
