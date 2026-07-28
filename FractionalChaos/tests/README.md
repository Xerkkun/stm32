# Pruebas del núcleo fraccionario portátil

El proyecto de este directorio compila únicamente el núcleo C11 de
`../common`. No requiere STM32Cube, HAL ni una placa.

Desde PowerShell:

```powershell
cmake -S STM32/FractionalChaos/tests `
      -B STM32/FractionalChaos/tests/build `
      -G "MinGW Makefiles" `
      -DCMAKE_C_COMPILER=C:/msys64/ucrt64/bin/gcc.exe `
      -DCMAKE_BUILD_TYPE=Release
cmake --build STM32/FractionalChaos/tests/build --config Release
ctest --test-dir STM32/FractionalChaos/tests/build `
      -C Release --output-on-failure
```

Con GCC o Clang que incluya las bibliotecas de tiempo de ejecución de
AddressSanitizer y UndefinedBehaviorSanitizer se habilitan ambos mediante:

```powershell
cmake -S STM32/FractionalChaos/tests `
      -B STM32/FractionalChaos/tests/build-sanitize `
      -G Ninja `
      -DFC_ENABLE_SANITIZERS=ON
cmake --build STM32/FractionalChaos/tests/build-sanitize
ctest --test-dir STM32/FractionalChaos/tests/build-sanitize `
      --output-on-failure
```

La configuración se detiene con un diagnóstico explícito si el compilador
seleccionado no dispone de esas bibliotecas; esto ocurre con la instalación
MinGW actual, aunque la compilación estricta sin sanitizadores sí se valida.

El ejecutable `fractional_reference_dump` permite obtener las palabras
`float32` tras un número corto de pasos:

```powershell
STM32/FractionalChaos/tests/build/fractional_reference_dump.exe 32
```

Para generar una tabla `const float` congelada se dirige la salida del
generador a un archivo de la plataforma. Por ejemplo:

```powershell
python STM32/FractionalChaos/tests/generate_tables.py lorenz efork
```

Cada descriptor generado incorpora el SHA-256 de las palabras `float32`.
Al asignarlo a `fc_config_t.precomputed_tables`, `fc_solver_init()` valida
el método, \(q\), \(h\) y \(M\), y copia los pesos a `fc_workspace_t`.

En los binarios embebidos se define
`FC_REQUIRE_PRECOMPUTED_TABLES=1`. Con esa guarda, una configuración sin
tabla falla durante `fc_solver_init()` y el objeto no contiene referencias a
`powf`, `tgammaf`, `log1pf` ni `expm1f`.
