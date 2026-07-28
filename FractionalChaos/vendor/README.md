# Dependencias de STM32Cube

El proyecto utiliza dos paquetes oficiales:

- STM32F7: los controladores ya presentes en `STM32/Lorenz_efork/Drivers`;
- STM32H7: `STM32CubeH7` v1.13.0, limitado a CMSIS y al controlador HAL.

El script `../tools/bootstrap.ps1` resuelve las herramientas instaladas por
STM32Cube para VS Code y descarga la revisión H7 fijada cuando no se encuentra
en este directorio.
