# Campaña física reproducible

El runner `tools/run_physical_campaign.py` congela la matriz primaria de
3 sistemas por 3 métodos por 2 representaciones por 2 placas. Genera 30
bloques completos aleatorizados; cada bloque contiene una vez las 36 celdas.
Esto produce 1080 unidades experimentales y conserva
`cold_start_id` como clave de emparejamiento.

El manifiesto canónico es
`validation/physical_campaign_manifest.json`. Incluye los puertos y seriales
ST-LINK explícitos:

| Placa | ST-LINK serial | Puerto |
|---|---|---|
| NUCLEO-F746ZG | `066AFF504955657867165348` | `COM7` |
| NUCLEO-H755ZI-Q | `003700344142501220353451` | `COM6` |

## Estado del endpoint

El firmware dispone de un modo de benchmark compilable que ejecuta el warm-up
congelado del sistema, mantiene UART inactiva y conserva en RAM los 10 000
conteos DWT crudos. Sólo al terminar emite 2500 tramas FCC1 `kind=4`.
`sequence=0,4,...,9996` identifica el primer índice de cada bloque y los cuatro
conteos consecutivos se almacenan en `cycles`, `x_bits`, `y_bits`, `z_bits`.

El runner sólo acepta el endpoint si recibe los 2500 bloques exactos, los
10 000 valores son positivos, las identidades coinciden, todos los CRC son
válidos y `status=dropped=0`. También escribe `timing_cycles.csv`.

El checkpoint físico actual cubre las 12 celdas Chen: tres métodos por dos
representaciones por dos placas. Las 12/12 fueron aceptadas con 10,000 conteos
por celda, 120,000 conteos en total y una sola repetición seleccionada por
celda. El resumen canónico es
`validation/results/physical_timing_reset_pilot_chen/summary.json`; conserva
los hashes de cada `run.json` y `timing_cycles.csv`, además del CSV agregado y
la figura descriptiva.

La campaña primaria completa **todavía está bloqueada** por una cuestión
semántica: `STM32_Programmer_CLI` aplica un reset hardware,
pero no quita y restablece la alimentación. El resultado se etiqueta
`eligible_as_paper_cold_start=false` hasta congelar una de estas dos decisiones:

1. redefinir en el protocolo que el cold start experimental es un reset
   hardware y modificar el paper de forma explícita; o
2. incorporar un relé de alimentación o una intervención manual registrada
   que produzca un power cycle real.

El endpoint resuelve la conservación de la distribución cruda. La corrección
por ventana vacía, el pulso GPIO y cualquier segmentación adicional siguen
siendo factores del protocolo final; no se inventan dentro del piloto.

Los 10,000 valores dentro de una ejecución no son 10,000 réplicas
independientes. Con \(N=1\) reset ST-LINK por celda no se estima variabilidad
entre reinicios ni se realiza inferencia. Para el protocolo pre-registrado
siguen siendo necesarios 30 ciclos físicos de alimentación por celda.

## Endpoint y duración

Una duración fija no define evidencia válida porque EFORK3, GL y M2sFRK tienen
tasas distintas. El endpoint del piloto de estados es por secuencia:

```text
target = ceil((transient_steps + 10000) / decimation) * decimation
```

Los 180 segundos son únicamente un watchdog. No se presentan como duración
experimental ni forman parte de la métrica. El primer frame debe tener
`sequence=decimation`; después, todos los incrementos deben ser exactamente la
decimación configurada. También se exige identidad coherente, CRC válido,
`status=0` y `dropped=0`.

Ese piloto sólo produce evidencia
`transport_and_decimated_cycle_subsample_only`. Nunca se convierte
automáticamente en un benchmark primario.

El endpoint `benchmark_reset_pilot` termina al reconstruir los índices
`0..9999`, no después de un tiempo fijo. Los 180 segundos son únicamente su
watchdog.

## Endpoint de series temporales densas

La separación entre muestras del piloto UART continuo no se corrige mediante
interpolación. Una trama FCC1 ocupa 40 bytes y UART usa 8N1 a
921600 bit/s, por lo que el límite ideal es
\(921600/(40\times10)=2304\) tramas/s. El integrador M2sFRK ejecuta muchos más
pasos por segundo; por ello una transmisión continua con decimación 1 perdería
muestras aunque el cálculo numérico fuera correcto.

El manifiesto derivado `validation/dense_uart_capture_manifest.json` define el
endpoint separado `dense_timeseries_pilot`. La imagen de adquisición:

1. ejecuta 12 000 pasos consecutivos y conserva en RAM un registro compacto de
   estado y ciclos después de cada paso;
2. mantiene UART inactiva durante toda la ventana numérica;
3. detiene el integrador y sólo entonces vacía los 12 000 registros mediante
   FCC1; y
4. permanece detenida después de transmitir el último registro.

El contrato exige exactamente `sequence=1..12000`, incremento uno, CRC válido,
`status=0` y `dropped=0`. Para Lorenz se descartan después las secuencias
`1..2000`; las 10 000 restantes tienen separación de tiempo de modelo
\(\Delta t=h=0.005\). El tiempo se reconstruye como
\(t_i=(\texttt{sequence}_i-2001)h\): el instante de recepción en el anfitrión
no es tiempo del sistema porque UART se vacía después del cálculo.

Cada registro interno ocupa 16 bytes y el buffer completo 192 000 bytes. Los
mapas enlazados medidos dejan el uso de SRAM1 de F746 en 200 480/245 760 bytes
(81.58 %) y el de RAM_D1 de H755 en 196 160/524 288 bytes (37.41 %). Este
firmware es exclusivamente una imagen de adquisición; no es elegible para
throughput UART, tiempo de pared ni benchmark primario. Los conteos DWT de cada
paso se preservan, pero las comparaciones primarias de rendimiento continúan
usando `benchmark_reset_pilot`.

Ejemplo acotado para las dos representaciones F746:

```powershell
python tools/run_physical_campaign.py `
  --manifest validation/dense_uart_capture_manifest.json `
  --output-root validation/results/dense_uart_capture `
  run `
  --endpoint dense_timeseries_pilot `
  --cell lorenz_m2sfrk_f746_float32 `
  --cell lorenz_m2sfrk_f746_fixed `
  --board f746 `
  --reset-repetition 1 `
  --max-runs 2 `
  --execute `
  --confirm-campaign-id stm32_dense_lorenz_m2sfrk_4cells_v1 `
  --allow-pilot-only
```

Para H755 se sustituyen las dos celdas por
`lorenz_m2sfrk_h755_float32` y `lorenz_m2sfrk_h755_fixed`, y `--board` por
`h755`. El runner programa con `-NoReset`, abre y limpia el puerto UART antes
del reset, captura hasta la secuencia exacta y conserva binario, CSV, hashes,
mapas, logs y revisión Git.

## Plan sin hardware

El comando siguiente valida el manifiesto y escribe un JSON de resumen y un CSV
con los 1080 `run_id` deterministas:

```powershell
python tools/run_physical_campaign.py plan
```

Cada bloque se ordena por `sha256_rank_v1` con la semilla congelada `20260728`;
no depende de detalles internos de `random.shuffle` ni de la versión de Python.
El `run_id` depende de campaña, celda, repetición y semilla; por ello regenerar
el mismo plan produce exactamente los mismos identificadores y el mismo hash.

Un dry-run filtrado tampoco abre puertos, compila ni programa. Como Chen es el
único manifiesto que superó la calificación ABM de horizonte largo, los
ejemplos ejecutables se limitan a sus celdas:

```powershell
python tools/run_physical_campaign.py run `
  --endpoint benchmark_reset_pilot `
  --cell chen_m2sfrk_h755_float32 `
  --reset-repetition 1 `
  --max-runs 1
```

## Piloto físico acotado

El benchmark `kind=4` no depende de calibrar la decimación porque no transmite
durante la ventana medida. Una ejecución requiere tres reconocimientos
explícitos y se limita a dos runs por invocación.

Comando exacto para una repetición fija Chen/M2sFRK en F746:

```powershell
python tools/run_physical_campaign.py run `
  --endpoint benchmark_reset_pilot `
  --cell chen_m2sfrk_f746_fixed `
  --reset-repetition 1 `
  --max-runs 1 `
  --execute `
  --confirm-campaign-id stm32_primary_36x30_v1 `
  --allow-pilot-only
```

Y en H755:

```powershell
python tools/run_physical_campaign.py run `
  --endpoint benchmark_reset_pilot `
  --cell chen_m2sfrk_h755_fixed `
  --reset-repetition 1 `
  --max-runs 1 `
  --execute `
  --confirm-campaign-id stm32_primary_36x30_v1 `
  --allow-pilot-only
```

No se debe usar este comando como campaña masiva. `primary_benchmark` se
detiene con un error explicativo aunque se añada `--execute`. Lorenz y Rössler
no se promueven por apariencia de sus trayectorias UART: sus manifiestos
actuales y los dos reemplazos bibliográficos explorados (0/2 elegibles) no
superaron todas las pantallas ABM congeladas.

Para avanzar sin sobrescribir resultados se puede omitir
`--reset-repetition` y usar
`--resume`. Sólo se salta un `run_id` después de revalidar `run.json`, la
identidad, la aceptación y los hashes. Un directorio parcial o alterado detiene
el proceso:

```powershell
python tools/run_physical_campaign.py run `
  --endpoint benchmark_reset_pilot `
  --cell chen_m2sfrk_h755_float32 `
  --max-runs 2 `
  --resume `
  --execute `
  --confirm-campaign-id stm32_primary_36x30_v1 `
  --allow-pilot-only
```

## Aislamiento y trazabilidad

Los builds se configuran mediante `tools/build_campaign.ps1` en:

```text
build/campaign/f746/decim-<N>-release
build/campaign/h755/decim-<N>-release
build/campaign/f746/benchmark-10000-release
build/campaign/h755/benchmark-10000-release
build/campaign/f746/dense-12000-decim-1-release
build/campaign/h755/dense-12000-decim-1-release
```

El flasher comprueba el `Board Name`, exige el serial exacto y acepta
`-BuildDirectory` únicamente dentro de `build/`. El runner mantiene locks
atómicos global, por sonda, puerto y directorio de build. Un lock vivo nunca se
rompe. `--break-stale-locks` sólo elimina uno cuyo PID ya no existe en el mismo
host.

Cada piloto conserva:

- `build.log`, `flash.log` y `reset.log`;
- captura UART binaria y CSV FCC1;
- hashes SHA-256 de captura e imágenes;
- commit y estado dirty del repositorio;
- identidad de placa, puerto, serial, decimación y regla de reset;
- contadores de CRC, gaps, `status`, `dropped`, ruido y bytes finales;
- un `run.json` que mantiene falsas las dos elegibilidades primarias.

Si algo falla, se escribe `failure.json` y no se reutiliza ni sobrescribe el
`run_id`.

## Campaña de tramas binarias

Las 30 repeticiones de benchmark y las 100 secuencias de un millón de bits por
celda son campañas diferentes. Este runner cubre la agenda 36 por 30 y un
piloto corto de transporte; no genera ni declara completada la campaña larga
NIST/TestU01/PractRand.

Sobre cuatro capturas UART Lorenz/M2sFRK de diagnóstico sí se ejecutó un
cribado corto y separado: NIST SP 800-22 sobre una secuencia por
configuración, PractRand sobre 127--136 KiB y Rabbit/Alphabit de TestU01 sobre
las palabras completas disponibles. Sus informes y límites están en
`validation/results/statistical_batteries_m2sfrk_pilot/`. No sustituyen 100
secuencias por celda, los checkpoints PractRand de 64 MiB/256 MiB/1 GiB ni
SP 800-90B.

## Pendientes que conservan el estado de piloto

- 30 ciclos físicos de alimentación por cada celda promovida;
- medición de corriente y energía por paso;
- watermark de pila y memoria dinámica;
- ablación H0-E/H1/H2 con carga y paquetes comparables;
- baterías largas y múltiples secuencias según el protocolo.
