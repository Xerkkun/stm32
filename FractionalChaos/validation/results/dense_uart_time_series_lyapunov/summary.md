# Dense UART time-series Lyapunov diagnostic

Software: `hidden-attractors-fo 1.0.0` with `nolds 0.6.3`; exact local source revision `e7cf1b74d0cd7fa520aeaa69407565ce1fe8673c`.

## Sampling correction at acquisition

Each board buffers 12000 consecutive solver states before UART drain. The accepted sequence is exactly 1--12000 with `status=0`, `dropped=0`, and one row per integration step. The model-time interval is therefore h=0.005. Host reception time is not treated as model time.

The first 2000 states are discarded. Two non-overlapping windows of 4096 x samples are used (sequences 2001--6096 and 6097--10192). No interpolation, decimation, resampling, or cross-lane harmonization is performed.

## Numerical methods

The embedded trajectory uses M2sFRK for the q=0.995 fractional Lorenz pilot with h=0.005. Rosenstein estimates a scalar largest Lyapunov exponent; Eckmann reconstructs a five-component scalar spectrum; Kaplan--Yorke is algebraic post-processing of that ordered exploratory spectrum.

Primary parameters follow the documented nolds Lorenz example: Rosenstein emb_dim=5, lag=5, min_tsep=10, min_neighbors=20, trajectory_len=28, fit=poly, fit_offset=8; Eckmann emb_dim=5, matrix_dim=5, min_neighbors=8, min_tsep=10.

## Primary window

| Board | Representation | Rosenstein LLE | Eckmann spectrum | D_KY | R2 |
|---|---|---:|---|---:|---:|
| F746 | float32 | 7.0181906 | [1.4922402, 0.15136828, -7.5371599, -17.353262, -131.23944] | 2.218067 | 0.98939 |
| F746 | fixed_q14_q30 | 5.648099 | [4.1549602, -0.12034859, -8.5583769, -53.904835, -126.00052] | 2.471423 | 0.99198 |
| H755 | float32 | 7.0181906 | [1.4922402, 0.15136828, -7.5371599, -17.353262, -131.23944] | 2.218067 | 0.98939 |
| H755 | fixed_q14_q30 | 5.648099 | [4.1549602, -0.12034859, -8.5583769, -53.904835, -126.00052] | 2.471423 | 0.99198 |

## Non-overlapping window and parameter sensitivity

| Board | Representation | Analysis | Rosenstein LLE | Eckmann spectrum | D_KY | R2 |
|---|---|---|---:|---|---:|---:|
| F746 | float32 | primary_window_2 | 6.0086225 | [1.507408, 0.27433068, -6.2200873, -17.04865, -132.08048] | 2.286449 | 0.99333 |
| F746 | float32 | parameter_sensitivity_window_1 | 2.9027838 | [1.5208793, 0.22816039, -7.5090137, -17.722439, -130.57903] | 2.232925 | 0.99577 |
| F746 | fixed_q14_q30 | primary_window_2 | 6.6453036 | [5.1076251, 0.87275077, -6.5693728, -53.014701, -139.67878] | 2.910342 | 0.99195 |
| F746 | fixed_q14_q30 | parameter_sensitivity_window_1 | 2.6591604 | [3.9186212, -0.021605133, -8.5405935, -54.391973, -126.69385] | 2.456293 | 0.99579 |
| H755 | float32 | primary_window_2 | 6.0086225 | [1.507408, 0.27433068, -6.2200873, -17.04865, -132.08048] | 2.286449 | 0.99333 |
| H755 | float32 | parameter_sensitivity_window_1 | 2.9027838 | [1.5208793, 0.22816039, -7.5090137, -17.722439, -130.57903] | 2.232925 | 0.99577 |
| H755 | fixed_q14_q30 | primary_window_2 | 6.6453036 | [5.1076251, 0.87275077, -6.5693728, -53.014701, -139.67878] | 2.910342 | 0.99195 |
| H755 | fixed_q14_q30 | parameter_sensitivity_window_1 | 2.6591604 | [3.9186212, -0.021605133, -8.5405935, -54.391973, -126.69385] | 2.456293 | 0.99579 |

## Input provenance

| Case | run.json SHA-256 | capture.csv SHA-256 | capture.bin SHA-256 |
|---|---|---|---|
| f746_float32 | `bd941cccb0c94033f43a1722cb465255cd0b5c3cfb0561105a67966ed83db24d` | `c4f0458baed7f63cf18e569d2c1211342f64aff2871a4185c2bc5a6acaee97dc` | `569de6027e31e724ce7a89205b71eac387ad33fb5edc53342ddce2fa0d4ea404` |
| f746_fixed_q14_q30 | `0dcf40b23539df4dcb7a3cfa3dc42c193dbdef3833c2c560aa47899e08947ffe` | `dd9a59e334965e17f430be64f80d2f41e1ae4e8ca5ee28da8dbb5d22356ac871` | `be490211b75bf497ca139c70da8e5b0bc669c6b9c5ad0ace03803a78ab8e44cf` |
| h755_float32 | `2a34303eda4f977dc89cee3ce38723d062cd0f4270d2139ad50f7087b95f6486` | `f27800a1bd0961839b3ba08991b33fae7cf528de2b2a0c01172f4588b7b8a5f9` | `054bc77f34d8b97de322495cfb2ee439878e4e577947724b05fca562082e20a7` |
| h755_fixed_q14_q30 | `4de9bb26026a21243713f9daa179add638e5fa369286e57a8ac7ac1ecfaf7441` | `014db2d9b84b14426d048ebdb21bc4a117d280792708634fdcdf0e55e03e68fa` | `3c9cf146864d73309bcdd5c68bc341da4a611b0d3a1b42605a9b81b6f862832c` |

## Evidence boundary

- All estimates are finite-time and reconstructed from scalar x only.
- The Eckmann spectrum and Kaplan--Yorke dimension are exploratory.
- The second window and parameter perturbation are sensitivity checks, not uncertainty intervals.
- Lorenz failed the frozen ABM qualification.
- Formal chaos, asymptotic-spectrum, hiddenness, randomness, and cryptographic claims: 0.
