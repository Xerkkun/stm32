# Decisión de selección de sistemas alternativos

Fecha de corte: 2026-07-29.

## Resultado ejecutivo

Se ejecutaron cuatro candidatos nuevos con una batería numérica congelada,
una corrección bibliográfica independiente para Lü y una segunda cohorte
cerrada de tres candidatos con contratos publicados completos. Los umbrales no
se relajaron tras observar los resultados.

- `liu_caputo_v1` superó todas las pantallas, quedó segundo en el ranking
  numérico v1 y conserva un contrato bibliográfico compatible con Caputo PECE
  de memoria completa. Conserva una de las dos plazas alternativas y pasa a la
  siguiente puerta de validación.
- `genesio_tesi_simplified_caputo_v1` superó las pantallas numéricas, pero
  quedó tercero cuando sólo había dos plazas y el artículo de origen no
  documenta el mismo marchador ABM de memoria completa. Se conserva como IVP
  Caputo independiente; no se promueve automáticamente.
- `shimizu_morioka_caputo_v1` tiene un contrato bibliográfico compatible, pero
  falló no periodicidad y estabilidad por bloques en ambas resoluciones.
- `lu_caputo_v1`, tomado de Yadav et al., fue fuerte numéricamente y ocupó el
  primer lugar v1. Una auditoría posterior encontró que el procedimiento
  impreso en esa fuente es un ABM ordinario de cuatro pasos, sin la convolución
  histórica fraccionaria, y no declara \(h\). La trayectoria se conserva como
  una IVP integrada por nuestro oráculo, pero deja de considerarse una
  reproducción cerrada apta para promoción formal.
- `lu_caputo_v2`, elegido por corrección de procedencia antes de calcular su
  trayectoria, usa el contrato Caputo predictor-corrector de Chen et al. Falló
  únicamente la pantalla de recurrencia en \(h/4\), por lo que tampoco se
  promueve.
- Hammouch--Mekkaoui, Muñoz--Pacheco y glucosa--insulina superaron todas las
  pantallas de la segunda cohorte. La regla de una sola plaza seleccionó
  `hammouch_mekkaoui_caputo_v1` por el mayor margen mínimo.

La cohorte primaria provisional es, por tanto,
**Chen + Liu + Hammouch--Mekkaoui**. Lü no se sustituyó por Genesio–Tesi de
manera retrospectiva: la plaza se resolvió con una segunda cohorte congelada
antes de ejecutarla.

Esta decisión todavía no autoriza una campaña STM32. Liu debe superar primero
la ampliación explícita del esquema C para sus seis parámetros. Liu y
Hammouch--Mekkaoui deben superar el port C, la comparación C–ABM, las
comprobaciones de punto fijo, la compilación de ambos targets y el preflight
físico.

## Qué pruebas se aplicaron y por qué importan

| Puerta | Regla congelada | Riesgo que controla | Lo que no demuestra |
|---|---|---|---|
| Oráculo ABM | Constante exacta y solución manufacturada convergente | Errores en la implementación del integrador | Que un sistema particular sea caótico |
| Finitud y actividad | \(\max |x_i|\le1000\), tres componentes activos, desviación \(\ge0.001\), rango \(\ge0.01\) | Divergencia numérica o variables prácticamente congeladas | Acotamiento matemático para todo tiempo |
| No periodicidad | RMSE de recurrencia normalizado \(\ge0.05\) y entropía de permutación \(\ge0.25\) | Órbitas repetitivas simples que pueden parecer complejas en una figura | Exponente de Lyapunov positivo o prueba de caos |
| Estabilidad por bloques | Desplazamiento medio \(\le1.5\sigma\), razón de desviación por bloque entre 0.25 y 4 | Un promedio global que oculte tramos casi estacionarios o transitorios tardíos | Estacionariedad universal |
| Consistencia \(h/2\)–\(h/4\) | Límites congelados para medias, desviaciones, cuantiles y entropía | Dependencia excesiva de la discretización | Coincidencia punto a punto de trayectorias caóticas |
| Auditoría bibliográfica | Operador, ecuaciones, parámetros, \(q\), estado inicial, paso y método identificables | Atribuir al artículo una reproducción que realmente no se ejecutó | Validez del firmware |

La pantalla de recurrencia compara estados separados por retardos de 1 a 15 s,
normalizados por la dispersión de la señal. Un valor pequeño significa que
existe un retardo para el que la serie vuelve demasiado cerca de sí misma. La
entropía de permutación mide diversidad de patrones ordinales. Se exigen ambas:
una no reemplaza a la otra.

La estabilidad por bloques divide los 40 s posteriores al transitorio en
cuatro partes. Su objetivo es verificar que la actividad no esté concentrada
en un único tramo. La comparación entre resoluciones evalúa distribuciones y
no estados instantáneos, porque dos trayectorias caóticas válidas se separan
puntualmente aunque describan el mismo régimen.

## Resultados de la cohorte v1

Todos los candidatos permanecieron finitos, mantuvieron tres componentes
activos y pasaron la comparación \(h/2\)–\(h/4\).

| Contrato | Entropía \(h/2\) / \(h/4\) | RMSE de recurrencia \(h/2\) / \(h/4\) | Razón mínima de bloque \(h/2\) / \(h/4\) | Decisión dinámica | Decisión de seguimiento |
|---|---:|---:|---:|---|---|
| Lü Yadav v1 | 0.7023 / 0.6960 | 1.1729 / 1.2300 | 0.9720 / 0.9805 | Calificado, margen 2.4582 | No promovido: procedencia numérica incompleta |
| Genesio–Tesi simplificado | 0.2549 / 0.2549 | 0.1590 / 0.1590 | 0.5925 / 0.5925 | Calificado, margen 1.0196 | No seleccionado: tercer lugar y contrato algorítmico no equivalente |
| Shimizu–Morioka | 0.2151 / 0.2151 | 0.6559 / 0.6559 | 0.08545 / 0.08543 | No calificado | Falló entropía y estabilidad por bloques en ambas resoluciones |
| Liu | 0.3794 / 0.3725 | 0.9941 / 0.9570 | 0.3188 / 0.3155 | Calificado, margen 1.2619 | Promovido a validación C/firmware |

El margen es el menor cociente normalizado respecto de todos los límites
continuos. Un margen mayor que 1 indica aprobación; cuanto más cerca está de
1, más cerca se encuentra alguna métrica de su umbral. Genesio–Tesi aprobó por
un margen pequeño, dominado por su entropía \(0.2549\) frente al límite 0.25.

## Corrección bibliográfica Lü v2

La sustitución se decidió por completitud del contrato publicado, no por
ajustar el resultado v1. Chen et al. reportan Caputo, predictor-corrector
generalizado de Adams–Bashforth–Moulton, paso fijo \(h=0.01\),
\(q=0.90\), \((a,b,c)=(35,3,28)\) y
\(\mathbf{x}_0=(0,3,9)\).

| Resolución | Máximo absoluto | Entropía | RMSE de recurrencia | Razón mínima de bloque | Resultado |
|---|---:|---:|---:|---:|---|
| \(h/2=0.005\) | 52.5414 | 0.8361 | 1.2169 | 0.8950 | Pasa |
| \(h/4=0.0025\) | 51.9518 | 0.6778 | **0.03280** | 0.9914 | Falla no periodicidad |

La comparación entre resoluciones pasó. La decisión exacta es
`not_qualified_dynamic_screen_failed`, con la razón
`h/4 failed nonperiodicity_screen`. No se cambió el mínimo 0.05.

## Segunda cohorte que cerró la plaza vacante

La selección bibliográfica se cerró antes de ejecutar las trayectorias. Los
tres artículos publican un sistema 3D autónomo, operador de Caputo, método
ABM/PECE con historia, \(q\), \(h=0.01\), parámetros y estado inicial. La
regla solicitó una sola promoción.

| Contrato | Entropía \(h/2\) / \(h/4\) | RMSE de recurrencia \(h/2\) / \(h/4\) | Razón mínima de bloque \(h/2\) / \(h/4\) | Margen mínimo | Decisión |
|---|---:|---:|---:|---:|---|
| Hammouch--Mekkaoui | 0.3786 / 0.3772 | 1.0834 / 1.1527 | 0.8491 / 0.9343 | 1.5088 | Calificado y seleccionado |
| Muñoz--Pacheco | 0.2597 / 0.2597 | 1.0575 / 1.0572 | 0.7192 / 0.7191 | 1.0389 | Calificado, no seleccionado |
| Glucosa--insulina | 0.3315 / 0.3304 | 1.0832 / 1.0128 | 0.8470 / 0.8196 | 1.3216 | Calificado, no seleccionado |

Los tres permanecieron finitos, conservaron actividad en los tres componentes
y pasaron la comparación entre resoluciones. El orden congelado por margen fue
Hammouch--Mekkaoui, glucosa--insulina y Muñoz--Pacheco.

La palabra “hidden” en el ID de Muñoz--Pacheco identifica el caso publicado.
Esta ejecución no hizo un ensayo de cuencas y no valida por sí misma el
carácter oculto del atractor. Del mismo modo, el modelo glucosa--insulina se
usa como benchmark matemático y no sustenta una conclusión clínica.

Hammouch--Mekkaoui es además el ganador más sencillo de portar: no tiene
parámetros libres y su RHS sólo contiene términos cuadráticos con coeficientes
enteros. Esto no formó parte del ranking dinámico y deberá medirse, no
presuponerse, en el STM32.

## Por qué no se retoman Lorenz o Rössler

Los resultados anteriores siguen siendo válidos para sus contratos exactos:

- `lorenz_caputo_v1` falló estabilidad dentro de resolución en \(h/4\).
- `rossler_caputo_v1` obtuvo entropía aproximada 0.2487 y una razón mínima de
  bloque aproximada 0.0254; no superó todas las puertas.
- el reemplazo exploratorio Rössler es otro contrato y alcanzó una razón
  mínima de bloque aproximada 0.0706; tampoco resultó elegible;
- `rossler_classic_caputo_v2` sí supera la entropía con 0.251264, pero sus
  razones mínimas 0.018406 y 0.018407 fallan claramente la estabilidad por
  bloques.

No son cuatro mediciones intercambiables de “Rössler”. Cada resultado pertenece
a un manifiesto específico. “No promovido” significa que ese contrato no
superó este protocolo y este horizonte; no significa que la familia completa
sea incapaz de mostrar caos.

## Artefactos reproducibles

- Cohorte y regla v1:
  [`alternative_system_candidate_grid_v1.json`](../alternative_system_candidate_grid_v1.json)
  y
  [`alternative_system_criteria_v1.json`](../alternative_system_criteria_v1.json).
- Resultado completo v1:
  [`alternative_system_qualification_v1/qualification.json`](alternative_system_qualification_v1/qualification.json)
  y
  [`qualification_summary.csv`](alternative_system_qualification_v1/qualification_summary.csv).
- Corrección de procedencia Lü:
  [`lu_literature_correction_grid_v2.json`](../lu_literature_correction_grid_v2.json)
  y
  [`lu_literature_correction_criteria_v2.json`](../lu_literature_correction_criteria_v2.json).
- Resultado de corrección:
  [`lu_literature_correction_v2/qualification.json`](lu_literature_correction_v2/qualification.json).
- Cohorte cerrada de segunda ronda:
  [`alternative_system_round2_grid_v1.json`](../alternative_system_round2_grid_v1.json),
  [`alternative_system_round2_criteria_v1.json`](../alternative_system_round2_criteria_v1.json)
  y
  [`alternative_system_round2_manifests_v1.json`](../alternative_system_round2_manifests_v1.json).
- Resultado completo de segunda ronda:
  [`alternative_system_round2_v1/qualification.json`](alternative_system_round2_v1/qualification.json)
  y
  [`qualification_summary.csv`](alternative_system_round2_v1/qualification_summary.csv).
- Pruebas de integridad:
  [`test_alternative_system_artifacts.py`](../../tests/test_alternative_system_artifacts.py).

Fuentes primarias de los contratos:

- [Lü/Yadav 2019](https://doi.org/10.1016/j.cjph.2018.12.001)
- [Genesio–Tesi/Mao 2025](https://doi.org/10.3390/fractalfract9020074)
- [Shimizu–Morioka/Wei 2021](https://doi.org/10.3389/fphy.2021.636173)
- [Liu/Daftardar-Gejji y Bhalekar 2010](https://doi.org/10.1016/j.camwa.2009.07.003)
- [Lü/Chen et al. 2012, copia de autor](https://sprott.physics.wisc.edu/pubs/paper462.pdf)
- [Hammouch--Mekkaoui 2018](https://doi.org/10.1007/s40747-018-0070-3)
- [Muñoz--Pacheco et al. 2018](https://doi.org/10.3390/e20080564)
- [Glucosa--insulina 2020](https://doi.org/10.3390/sym12091395)

## Límite de la afirmación

Estos resultados califican o rechazan configuraciones concretas bajo pantallas
observacionales durante 50 s con ABM Caputo `float64` de memoria completa.
No prueban caos, acotamiento global, atractores ocultos, equivalencia entre
operadores, validez de una memoria embebida de 10 s, rendimiento STM32,
energía ni aleatoriedad.
