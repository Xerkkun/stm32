# Auditoría numérica y de implementación

## Resultado

Se sustituye el código histórico por un núcleo común porque la realización
original no conserva el contrato experimental del artículo ni aprovecha de
forma adecuada la unidad de punto flotante de las dos placas. Los proyectos
anteriores se conservan sin cambios para mantener su trazabilidad; el firmware
canónico se encuentra en `STM32/FractionalChaos`.

## Hallazgos en los proyectos históricos

Se observan los siguientes problemas:

| Aspecto | Código histórico | Realización canónica |
|---|---|---|
| Placa | Se trata de STM32F746, no de STM32F4 | Se mantiene F746 y se añade H755 |
| Horizonte de memoria | Se declara \(L_m=10\) s, pero se fija `MAX_M=10` | Se utilizan \(M=2000\), \(1000\) y \(2000\), según el manifiesto |
| Aritmética | Se mezclan `float`, `double` y `long double` | Se utiliza `float32` de extremo a extremo |
| GL | Se aplica una recurrencia GL/Riemann--Liouville sin corrección explícita | Se aplica GL a \(u=x-x_0\), alineado con Caputo |
| Historial EFORK3 | Se recorre por separado para cada etapa | Se acumulan las tres correcciones en un solo recorrido |
| Pesos | Se calculan funciones trascendentales en la placa | Se generan en el anfitrión y se congelan como palabras `float32` |
| Suma del historial | Se realiza una suma lineal simple | Se utilizan bloques de 32 términos y compensación de Neumaier |
| Almacenamiento | Se desplazan o indexan estados completos | Se utiliza un anillo de incrementos para EFORK3 y de desviaciones para GL |
| Compilación | Se encuentran configuraciones `-O0` y rutas absolutas obsoletas | Se utiliza `-O3`, LTO y descubrimiento de herramientas STM32Cube |
| Periféricos | Se inicializan DAC, temporizadores, Ethernet, USB, I2C y otros módulos | Solo se inicializan reloj, caché, DWT y la ruta UART/DMA necesaria |
| Comparabilidad | Se utilizan frecuencias diferentes entre variantes F746 | Las seis variantes F746 comparten el mismo árbol de reloj |
| Ejecución | En una variante sin UART no queda conectada la interrupción de TIM2 | El integrador se ejecuta en el bucle principal y se mide con DWT |

`long double` no aporta precisión de hardware en la F746. El núcleo Cortex-M7
de esa placa dispone de FPU de precisión simple, por lo que las operaciones
dobles se resuelven mediante rutinas de software. Además de aumentar el costo,
esa mezcla impide una comparación homogénea con `float32`. Se selecciona
`float32` como contrato primario para utilizar la FPU de ambas placas y
conservar exactamente la misma representación.

## Contrato aplicado

Se fijan los manifiestos siguientes:

| Sistema | Parámetros | Estado inicial | \(q\) | \(h\) | \(L_m\) | \(M\) |
|---|---|---:|---:|---:|---:|---:|
| Lorenz | \(\sigma=10,\rho=28,\beta=8/3\) | \((0.1,0.1,0.1)\) | 0.995 | 0.005 | 10 s | 2000 |
| Rössler | \(a=0.2,b=0.2,c=5.7\) | \((1,0,0)\) | 0.9877 | 0.010 | 10 s | 1000 |
| Chen | \(a=35,b=3,c=28\) | \((0.1,0.1,0.1)\) | 0.900 | 0.005 | 10 s | 2000 |

La fuente canónica del contrato activo es
`validation/candidate_manifests_rossler_classic_v2.json`. El identificador
`rossler_classic_caputo_v2` reemplaza `rossler_caputo_v1` en el firmware
`float32`, el núcleo fijo y las tablas Rössler generadas. No reemplaza la
proveniencia de experimentos ya ejecutados: `validation/candidate_manifests.json`
permanece congelado como manifiesto histórico v1 y sus resultados conservan
sus parámetros, identificadores y conclusiones originales.

En EFORK3 se almacenan los incrementos
\(\Delta x_j=x_{j+1}-x_j\). Los pesos de las tres abscisas se consultan en una
tabla y las nueve sumas —tres componentes por tres etapas— se actualizan en
una sola pasada por el anillo.

En GL se almacenan las desviaciones \(u_j=x_j-x_0\). Con ello se implementa
directamente la identidad que alinea la recurrencia GL con el problema de
Caputo:

\[
u_n=h^q f(x_{n-1})-\sum_{j=1}^{\min(n,M)}g_j u_{n-j},
\qquad x_n=x_0+u_n.
\]

No se habilita una variante GL nativa sin corregir dentro de la matriz
primaria, porque representaría otro problema matemático.

## Conservación de precisión

Se aplican estas decisiones:

1. Se conservan subnormales, redondeo al más cercano y propagación IEEE de
   valores no numéricos; no se activa `-ffast-math`.
2. Se emplea `fmaf` en productos y acumulaciones sensibles para efectuar un
   solo redondeo cuando la instrucción FMA se encuentra disponible.
3. Se calculan las tablas con el valor real de \(q\) representable en
   `float32`, se emiten literales hexadecimales exactos y se registra un
   SHA-256 sobre sus palabras binarias.
4. Se copian únicamente las tablas del binario seleccionado desde Flash hacia
   DTCM durante la inicialización. No se evalúan `pow`, `gamma`, `log` ni
   exponenciales dentro de `step`.
5. Se comprueba que cada estado, incremento y coeficiente permanezca finito.
   Una pérdida de finitud detiene la ejecución y se informa en la trama.

La memoria máxima del núcleo ocupa 48 000 bytes. Por ello cabe en los 64 KiB
de DTCM de la F746 y con mayor holgura en la DTCM de la H755. Los búferes de
DMA y la cola entre núcleos se colocan en SRAM accesible por el periférico o
por ambos núcleos; no se colocan dentro de DTCM.

## Validación reproducible

Se ejecutan pruebas del campo vectorial, equilibrio, primer paso GL-Caputo,
coeficientes y pesos EFORK3, envolvimiento del anillo, copia de tablas
precalculadas y seis vectores dorados. También se contrasta una trayectoria
corta contra una implementación Python independiente.

Las pruebas del anfitrión verifican consistencia algebraica e implementación.
No sustituyen la calificación ABM de alta precisión, los barridos de paso y
memoria ni las mediciones sobre las placas físicas. Las divergencias puntuales
a horizonte largo tampoco se interpretan por sí solas como pérdida de
dinámica caótica.

## Estado reproducido del firmware

El 25 de julio de 2026 se recompilan en modo `Release` el anfitrión y las dos
plataformas mediante `tools/build.ps1`. Se obtienen los seis ejecutables de la
F746, los seis ejecutables CM7 de la H755 y el ejecutable UART común del CM4.
Para los trece ejecutables se generan los formatos ELF, HEX y BIN.

La batería `host-release-test` finaliza con cinco de cinco pruebas aprobadas.
En la inspección de los trece ELF no se encuentran símbolos sin resolver ni
referencias a aritmética `double`, funciones trascendentales, asignación
dinámica, `printf` o controladores HAL de ETH, I2C, DAC, TIM, PCD y HCD. El
objeto completo del solucionador ocupa 48 160 bytes y se enlaza en DTCM.

STM32CubeProgrammer no detecta un ST-LINK conectado durante esta revisión. Por
ello se verifican la compilación, el enlace y los artefactos de programación,
pero no se atribuye a esta ejecución una prueba física de arranque, temporización
o transmisión UART. Estas mediciones se obtienen al ejecutar la campaña
experimental en ambas placas.
