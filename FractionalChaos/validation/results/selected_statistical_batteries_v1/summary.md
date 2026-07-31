# Auditoría estadística del piloto físico M2sFRK

Este documento separa diagnósticos descriptivos, elegibilidad y salida de baterías externas. No declara que ninguna trama haya aprobado una batería formal.

## Herramientas

| Herramienta | En PATH al inicio | Seleccionada localmente | Versión |
|---|---:|---:|---|
| NIST SP 800-22 STS | no | sí | 2.1.2 |
| PractRand | no | sí | 0.96 |
| TestU01 | no | sí | 1.2.3 |

## Resultados observados

| Trama | Bits completos | Fracción de unos | Entropía por byte | NIST p<0.01 | Familias NIST señaladas | NIST no aplicables | Prefijo PractRand | Cola no evaluada | Evaluaciones PractRand |
|---|---:|---:|---:|---:|---|---|---:|---:|---|
| chen_m2sfrk_f746_fixed | 1054464 | 0.500406842 | 7.998820637 | 2 | NonOverlappingTemplate | ninguna | 131072 | 736 | normal: 41, unusual: 1 |
| chen_m2sfrk_f746_float32 | 1054464 | 0.500414429 | 7.998622824 | 3 | BlockFrequency, NonOverlappingTemplate | RandomExcursions, RandomExcursionsVariant | 131072 | 736 | normal: 41, normalish: 1 |
| chen_m2sfrk_h755_fixed | 1054464 | 0.499878611 | 7.998619382 | 1 | NonOverlappingTemplate | ninguna | 131072 | 736 | normal: 42 |
| chen_m2sfrk_h755_float32 | 1054464 | 0.499824555 | 7.998421112 | 3 | NonOverlappingTemplate | ninguna | 131072 | 736 | normal: 41, normalish: 1 |
| hammouch_mekkaoui_m2sfrk_f746_fixed | 1054464 | 0.498996647 | 7.997090045 | 13 | ApproximateEntropy, FFT, NonOverlappingTemplate, Serial | RandomExcursions, RandomExcursionsVariant | 131072 | 736 | FAIL !!: 1, normal: 39, normalish: 2 |
| hammouch_mekkaoui_m2sfrk_f746_float32 | 1054464 | 0.499771448 | 7.998586469 | 6 | NonOverlappingTemplate, RandomExcursionsVariant | ninguna | 131072 | 736 | normal: 41, normalish: 1 |
| hammouch_mekkaoui_m2sfrk_h755_fixed | 1054464 | 0.498949229 | 7.997020904 | 7 | ApproximateEntropy, FFT, NonOverlappingTemplate, RandomExcursionsVariant, Serial | ninguna | 131072 | 736 | FAIL !!!: 1, normal: 39, normalish: 2 |
| hammouch_mekkaoui_m2sfrk_h755_float32 | 1054464 | 0.499810330 | 7.998760896 | 3 | NonOverlappingTemplate | RandomExcursions, RandomExcursionsVariant | 131072 | 736 | normal: 42 |
| liu_m2sfrk_f746_fixed | 1054464 | 0.501294497 | 7.998695413 | 2 | CumulativeSums, Frequency | ninguna | 131072 | 736 | normal: 42 |
| liu_m2sfrk_f746_float32 | 1054464 | 0.499326672 | 7.998500595 | 1 | NonOverlappingTemplate | RandomExcursions, RandomExcursionsVariant | 131072 | 736 | normal: 42 |
| liu_m2sfrk_h755_fixed | 1054464 | 0.500499780 | 7.998670407 | 1 | NonOverlappingTemplate | RandomExcursions, RandomExcursionsVariant | 131072 | 736 | normal: 42 |
| liu_m2sfrk_h755_float32 | 1054464 | 0.499303912 | 7.998681081 | 0 |  | ninguna | 131072 | 736 | normal: 41, normalish: 1 |

## TestU01 Rabbit/Alphabit

| Trama | Rabbit | Bits Rabbit | Alphabit | Bits Alphabit | Cola omitida (bits) |
|---|---|---:|---|---:|---:|
| chen_m2sfrk_f746_fixed | all_tests_passed | 1054464/1054464 | all_tests_passed | 1054464/1054464 | 0 |
| chen_m2sfrk_f746_float32 | all_tests_passed | 1054464/1054464 | all_tests_passed | 1054464/1054464 | 0 |
| chen_m2sfrk_h755_fixed | at_least_one_result_flagged | 1054464/1054464 | all_tests_passed | 1054464/1054464 | 0 |
| chen_m2sfrk_h755_float32 | at_least_one_result_flagged | 1054464/1054464 | all_tests_passed | 1054464/1054464 | 0 |
| hammouch_mekkaoui_m2sfrk_f746_fixed | at_least_one_result_flagged | 1054464/1054464 | at_least_one_result_flagged | 1054464/1054464 | 0 |
| hammouch_mekkaoui_m2sfrk_f746_float32 | all_tests_passed | 1054464/1054464 | all_tests_passed | 1054464/1054464 | 0 |
| hammouch_mekkaoui_m2sfrk_h755_fixed | at_least_one_result_flagged | 1054464/1054464 | at_least_one_result_flagged | 1054464/1054464 | 0 |
| hammouch_mekkaoui_m2sfrk_h755_float32 | all_tests_passed | 1054464/1054464 | all_tests_passed | 1054464/1054464 | 0 |
| liu_m2sfrk_f746_fixed | all_tests_passed | 1054464/1054464 | all_tests_passed | 1054464/1054464 | 0 |
| liu_m2sfrk_f746_float32 | all_tests_passed | 1054464/1054464 | all_tests_passed | 1054464/1054464 | 0 |
| liu_m2sfrk_h755_fixed | all_tests_passed | 1054464/1054464 | all_tests_passed | 1054464/1054464 | 0 |
| liu_m2sfrk_h755_float32 | all_tests_passed | 1054464/1054464 | all_tests_passed | 1054464/1054464 | 0 |

## Límites de interpretación

- NIST procesó exactamente todos los bits originales. Su lector binario exige lecturas de cuatro bytes; el relleno físico registrado nunca supera `tp.n` y no entra a las pruebas.
- Solo hay una secuencia física por configuración. Por ello los valores individuales de NIST son auditables, pero las proporciones de aprobación y la uniformidad entre secuencias no sustentan una validación formal.
- Para FFT, NIST SP 800-22 Rev. 1a, sección 2.6.7, recomienda `n >= 1000`; las cuatro tramas cumplen ese mínimo. Random Excursions y Random Excursions Variant quedaron no aplicables cuando no hubo ciclos suficientes; sus ceros de relleno en `results.txt` se excluyeron al reconciliar con `finalAnalysisReport.txt`.
- PractRand opera con bloques enteros de 1 KiB. Se probó el mayor prefijo exacto de cada archivo; la cola indicada no se rellenó, repitió ni recicló.
- Las corridas PractRand de 127–136 KiB son diagnósticos cortos, no evidencia de aleatoriedad a gran escala.
- TestU01 no estaba en PATH. Se compiló localmente la versión oficial 1.2.3 y se ejecutaron `bbattery_RabbitFile` y `bbattery_AlphabitFile` mediante el adaptador finito auditado.
- El adaptador pasó el número exacto de bits solicitado. TestU01 consumió únicamente palabras completas de 32 bits; las colas de 0 a 24 bits indicadas no se rellenaron, repitieron ni reciclaron.
- Rabbit y Alphabit vuelven a abrir la misma secuencia para pruebas distintas, según la interfaz oficial. Esto no alarga ninguna prueba ni convierte la conclusión emitida por la herramienta en validación formal.

Los hashes, comandos, p-valores individuales y logs crudos están en `summary.json` y en los subdirectorios por trama.
