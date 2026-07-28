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
| f746_float32_dec512 | 1062600 | 0.499732731 | 7.998680296 | 1 | NonOverlappingTemplate | RandomExcursions, RandomExcursionsVariant | 132096 | 729 | normal: 41 |
| f746_fixed_q14_q30_dec512 | 1042944 | 0.500907048 | 7.998545434 | 5 | NonOverlappingTemplate, Serial | RandomExcursions, RandomExcursionsVariant | 130048 | 320 | normal: 40, normalish: 1 |
| h755_float32_dec1024 | 1052760 | 0.500240321 | 7.998639907 | 2 | NonOverlappingTemplate | RandomExcursions, RandomExcursionsVariant | 131072 | 523 | normal: 40, normalish: 1 |
| h755_fixed_q14_q30_dec512 | 1119120 | 0.500019658 | 7.998730772 | 1 | NonOverlappingTemplate | RandomExcursions, RandomExcursionsVariant | 139264 | 626 | normal: 40, normalish: 1 |

## TestU01 Rabbit/Alphabit

| Trama | Rabbit | Bits Rabbit | Alphabit | Bits Alphabit | Cola omitida (bits) |
|---|---|---:|---|---:|---:|
| f746_float32_dec512 | all_tests_passed | 1062592/1062600 | all_tests_passed | 1062592/1062600 | 8 |
| f746_fixed_q14_q30_dec512 | all_tests_passed | 1042944/1042944 | all_tests_passed | 1042944/1042944 | 0 |
| h755_float32_dec1024 | all_tests_passed | 1052736/1052760 | all_tests_passed | 1052736/1052760 | 24 |
| h755_fixed_q14_q30_dec512 | all_tests_passed | 1119104/1119120 | all_tests_passed | 1119104/1119120 | 16 |

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
