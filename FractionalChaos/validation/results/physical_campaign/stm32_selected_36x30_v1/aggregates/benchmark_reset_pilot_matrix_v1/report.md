# Matriz física seleccionada: piloto por reset ST-LINK

Estado: `accepted_36_of_36_selected_reset_pilot_cells`.

Se validaron **36/36 celdas** con una repetición aceptada por celda y 10 000 valores de ciclos por repetición.
El inventario conserva 45 intentos: 40 aceptados y 5 fallidos.

## Política de selección congelada

- Se usa `r04` para 33 celdas.
- Se usa `r05` sólo para `chen_m2sfrk_f746_fixed`, `liu_m2sfrk_f746_fixed` y `hammouch_mekkaoui_m2sfrk_f746_fixed`.
- Los tres `r04` fallidos correspondientes permanecen en el inventario.

## Límites de evidencia

**N=1 reset ST-LINK por celda; no hubo corte y restauración de alimentación. Los 36 registros tienen `source.dirty=true`. Este agregado no es el benchmark primario.**

- No presentar estos pilotos por reset hardware como arranques en frío con ciclo de alimentación.
- No reportar estadística inferencial ni incertidumbre entre resets a partir de N=1 por celda.
- No promover esta instantánea al benchmark primario prerregistrado.
- No presentar estos pilotos como procedentes de un árbol fuente limpio: los 36 run.json registran source.dirty=true.
- No tratar los 10 000 ciclos de una ejecución como 10 000 unidades experimentales independientes.
- No inferir energía, aleatoriedad, fidelidad numérica ni caos formal a partir de los conteos de ciclos.

## Celdas seleccionadas

| Sistema | Método | Placa | Representación | ID de repetición seleccionada | Media | Mediana | P95 | Desv. poblacional |
|---|---|---|---|---:|---:|---:|---:|---:|
| chen | efork3 | f746 | float32 | 4 | 58335.7681 | 58338.0 | 58528.0 | 119.774069 |
| chen | efork3 | f746 | fixed_q14_q30 | 4 | 353345.3508 | 353328.5 | 386491.55 | 21317.56746 |
| chen | efork3 | h755 | float32 | 4 | 56944.3953 | 56949.0 | 57224.0 | 175.832231 |
| chen | efork3 | h755 | fixed_q14_q30 | 4 | 357140.0298 | 356937.5 | 385786.0 | 18461.995997 |
| chen | gl | f746 | float32 | 4 | 28130.0619 | 28122.0 | 29209.0 | 1001.630084 |
| chen | gl | f746 | fixed_q14_q30 | 4 | 107957.1093 | 107934.0 | 111033.05 | 1963.058298 |
| chen | gl | h755 | float32 | 4 | 27595.419 | 27589.0 | 28705.0 | 1003.985759 |
| chen | gl | h755 | fixed_q14_q30 | 4 | 107789.1488 | 107861.0 | 109783.0 | 1344.812997 |
| chen | m2sfrk | f746 | float32 | 4 | 312.073 | 312.0 | 312.0 | 0.97297 |
| chen | m2sfrk | f746 | fixed_q14_q30 | 5 | 578.8399 | 578.0 | 644.0 | 37.174374 |
| chen | m2sfrk | h755 | float32 | 4 | 261.0141 | 261.0 | 261.0 | 0.510589 |
| chen | m2sfrk | h755 | fixed_q14_q30 | 4 | 539.3849 | 540.0 | 573.0 | 22.760065 |
| liu | efork3 | f746 | float32 | 4 | 29654.6289 | 29645.0 | 29884.0 | 133.774397 |
| liu | efork3 | f746 | fixed_q14_q30 | 4 | 173572.1296 | 173465.0 | 189459.25 | 10297.644617 |
| liu | efork3 | h755 | float32 | 4 | 28891.585 | 28879.0 | 29224.05 | 195.574602 |
| liu | efork3 | h755 | fixed_q14_q30 | 4 | 174623.5479 | 174528.5 | 188657.35 | 9113.876821 |
| liu | gl | f746 | float32 | 4 | 14264.5727 | 14285.5 | 14803.0 | 502.581555 |
| liu | gl | f746 | fixed_q14_q30 | 4 | 50552.6771 | 50637.0 | 51740.05 | 730.576741 |
| liu | gl | h755 | float32 | 4 | 13922.3436 | 13935.5 | 14463.0 | 501.138586 |
| liu | gl | h755 | fixed_q14_q30 | 4 | 50812.5904 | 50589.0 | 51962.0 | 585.84147 |
| liu | m2sfrk | f746 | float32 | 4 | 316.0872 | 316.0 | 316.0 | 1.110854 |
| liu | m2sfrk | f746 | fixed_q14_q30 | 5 | 734.2313 | 733.0 | 789.05 | 32.910864 |
| liu | m2sfrk | h755 | float32 | 4 | 265.0058 | 265.0 | 265.0 | 0.279582 |
| liu | m2sfrk | h755 | fixed_q14_q30 | 4 | 626.8236 | 627.0 | 652.0 | 14.93454 |
| hammouch_mekkaoui | efork3 | f746 | float32 | 4 | 29656.005 | 29663.0 | 29777.0 | 82.868043 |
| hammouch_mekkaoui | efork3 | f746 | fixed_q14_q30 | 4 | 173877.999 | 173998.0 | 189952.05 | 10301.054608 |
| hammouch_mekkaoui | efork3 | h755 | float32 | 4 | 28900.5358 | 28910.0 | 29078.0 | 121.094642 |
| hammouch_mekkaoui | efork3 | h755 | fixed_q14_q30 | 4 | 174804.1169 | 174859.0 | 188980.05 | 9106.139386 |
| hammouch_mekkaoui | gl | f746 | float32 | 4 | 14299.5017 | 14317.0 | 14838.0 | 500.833328 |
| hammouch_mekkaoui | gl | f746 | fixed_q14_q30 | 4 | 51144.1566 | 51199.5 | 52307.05 | 804.031302 |
| hammouch_mekkaoui | gl | h755 | float32 | 4 | 13966.8893 | 13987.5 | 14516.0 | 501.228178 |
| hammouch_mekkaoui | gl | h755 | fixed_q14_q30 | 4 | 51350.7677 | 51304.0 | 52278.0 | 518.478355 |
| hammouch_mekkaoui | m2sfrk | f746 | float32 | 4 | 326.0745 | 326.0 | 326.0 | 1.006951 |
| hammouch_mekkaoui | m2sfrk | f746 | fixed_q14_q30 | 5 | 718.7632 | 716.0 | 807.0 | 55.010962 |
| hammouch_mekkaoui | m2sfrk | h755 | float32 | 4 | 275.0031 | 275.0 | 275.0 | 0.309984 |
| hammouch_mekkaoui | m2sfrk | h755 | fixed_q14_q30 | 4 | 616.0749 | 615.0 | 646.0 | 18.995728 |

## Intentos fallidos retenidos

| Run | Celda | Rep. | Tipo | Error |
|---|---|---:|---|---|
| `stm32_selected_36x30_v1__r01__liu_m2sfrk_f746_float32__4ad34d95e0__benchmark-reset-pilot` | liu_m2sfrk_f746_float32 | 1 | CampaignError | el proceso terminó con código 1; vea C:\Users\moren\Desktop\Codes\STM32\FractionalChaos\validation\results\physical_campaign\stm32_selected_36x30_v1\runs\stm32_selected_36x30_v1__r01__liu_m2sfrk_f746_float32__4ad34d95e0__benchmark-reset-pilot\build.log |
| `stm32_selected_36x30_v1__r02__hammouch_mekkaoui_gl_f746_fixed__5f8f2c5f9f__benchmark-reset-pilot` | hammouch_mekkaoui_gl_f746_fixed | 2 | CampaignError | reset terminó con código 1; vea C:\Users\moren\Desktop\Codes\STM32\FractionalChaos\validation\results\physical_campaign\stm32_selected_36x30_v1\runs\stm32_selected_36x30_v1__r02__hammouch_mekkaoui_gl_f746_fixed__5f8f2c5f9f__benchmark-reset-pilot\reset.log |
| `stm32_selected_36x30_v1__r04__chen_m2sfrk_f746_fixed__d77e90f884__benchmark-reset-pilot` | chen_m2sfrk_f746_fixed | 4 | CampaignError | reset terminó con código 1; vea C:\Users\moren\Desktop\Codes\STM32\FractionalChaos\validation\results\physical_campaign\stm32_selected_36x30_v1\runs\stm32_selected_36x30_v1__r04__chen_m2sfrk_f746_fixed__d77e90f884__benchmark-reset-pilot\reset.log |
| `stm32_selected_36x30_v1__r04__hammouch_mekkaoui_m2sfrk_f746_fixed__cf1a5e6894__benchmark-reset-pilot` | hammouch_mekkaoui_m2sfrk_f746_fixed | 4 | CampaignError | reset terminó con código 1; vea C:\Users\moren\Desktop\Codes\STM32\FractionalChaos\validation\results\physical_campaign\stm32_selected_36x30_v1\runs\stm32_selected_36x30_v1__r04__hammouch_mekkaoui_m2sfrk_f746_fixed__cf1a5e6894__benchmark-reset-pilot\reset.log |
| `stm32_selected_36x30_v1__r04__liu_m2sfrk_f746_fixed__9a8e4b0502__benchmark-reset-pilot` | liu_m2sfrk_f746_fixed | 4 | CampaignError | reset terminó con código 1; vea C:\Users\moren\Desktop\Codes\STM32\FractionalChaos\validation\results\physical_campaign\stm32_selected_36x30_v1\runs\stm32_selected_36x30_v1__r04__liu_m2sfrk_f746_fixed__9a8e4b0502__benchmark-reset-pilot\reset.log |

Los hashes y el inventario completo de archivos de cada intento se encuentran en `summary.json`; `attempts.csv` conserva una fila por intento.
