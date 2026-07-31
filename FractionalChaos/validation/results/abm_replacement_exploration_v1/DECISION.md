# Decisión de reemplazo ABM v1

La cuadrícula predeclarada no produjo un reemplazo elegible para Lorenz ni
para Rössler. Por tanto, no se crea `candidate_manifests_v2.json` y no se
ejecuta una calificación formal v2.

Esta decisión pertenece a la capa `exploratory_candidate_search`. Conserva
sin cambios los criterios, el horizonte de 50 s, el transitorio de 10 s y las
resoluciones \(h/2\) y \(h/4\) de
`validation/long_horizon_criteria.json`.

## Resultados que bloquean la promoción

- Lorenz, contrato de Feng et al. (2023), \(q=0.995\), \(h=0.001\) y
  \(x_0=(0.3,0.3,0.3)\): en \(h/2\), la razón mínima entre desviación
  estándar de bloque y global fue 0.2175794966, por debajo del mínimo
  congelado de 0.25. En \(h/4\) la pantalla interna sí pasó, pero la
  comparación entre resoluciones alcanzó una diferencia cuantil normalizada
  máxima de 1.0049914532, por encima del máximo 1.0.
- Rössler, contrato de Wang, Wang y Li (2024), \(q=0.9\), \(h=0.01\),
  \(x_0=(0.5,1.5,0.1)\) y parámetros \((0.5,0.2,10)\): la razón mínima de
  desviaciones fue 0.0706213583 en \(h/2\) y 0.0705860013 en \(h/4\), ambas
  por debajo de 0.25. La comparación entre resoluciones sí pasó.

Los valores completos, hashes de trayectoria y motivos de rechazo están en
`exploration.json`; los resúmenes tabulares están en
`exploration_summary.csv`, `exploration_blocks.csv` y
`exploration_trajectory_samples.csv`.

El resultado no demuestra ausencia de caos ni invalida los casos publicados.
Sólo establece que estos dos contratos exactos no satisfacen todos los
criterios conservadores congelados de este trabajo y, por ello, no pueden
promoverse como manifiestos formales.
