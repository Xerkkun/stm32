# UART time-series Lyapunov diagnostic

Software: `hidden-attractors-fo 1.0.0` from the recorded local revision, with `nolds 0.6.3`.

Numerical integration on the boards: M2sFRK, q=0.995, h=0.005 model-time units. Estimation: scalar Rosenstein LLE and Eckmann spectrum; Kaplan--Yorke is derived from the ordered Eckmann spectrum.

All lanes are harmonized to decimation 1024 (sample interval 5.12 model-time units), use x, discard 64 raw UART rows, and analyze the first 4096 harmonized samples.

| Board | Representation | LLE Rosenstein | Eckmann spectrum | D_KY | R2 |
|---|---|---:|---|---:|---:|
| F746 | float32 | 0.0079733194 | [0.017225061, -0.0015019537, -0.030950219] | 2.508013 | 0.73361 |
| F746 | fixed_q14 | 0.0076868783 | [0.017607819, -0.0010398458, -0.030878609] | 2.536552 | 0.71950 |
| H755 | float32 | 0.0078134573 | [0.018406448, -0.0016077687, -0.031024131] | 2.541471 | 0.72241 |
| H755 | fixed_q14 | 0.0078728625 | [0.0176951, -0.0012146033, -0.031055267] | 2.530683 | 0.73016 |

## Limited sensitivity

| Board | Representation | LLE range | D_KY range | minimum R2 |
|---|---|---:|---:|---:|
| F746 | float32 | 0.00398296--0.00797332 | 2.50655--2.55964 | 0.2124 |
| F746 | fixed_q14 | 0.00405793--0.00769749 | 2.53655--2.56761 | 0.2157 |
| H755 | float32 | 0.00400615--0.0078389 | 2.52563--2.54674 | 0.2137 |
| H755 | fixed_q14 | 0.00418585--0.00796805 | 2.50383--2.53246 | 0.2334 |

## Evidence boundary

- These are finite-time scalar-reconstruction diagnostics.
- The Eckmann spectrum and Kaplan--Yorke dimension are exploratory.
- Heavy UART decimation can alias the reconstructed dynamics.
- Lorenz did not pass the frozen ABM qualification.
- Formal chaos, randomness, hiddenness, and asymptotic-spectrum claims: 0.
