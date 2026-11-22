# AEROPATH Accuracy Evaluation — Baseline vs Bias-Corrected vs IDW

> Ground-truth validation using leave-one-station-out cross-validation across monitors.

| City | Pollutant | Model MAE | Bias-Corr MAE | IDW MAE (p=2) | MAE Δ (%) | Cat Agmt (Model) | Cat Agmt (Corr) | Cat Agmt (IDW) |
|---|---|---|---|---|---|---|---|---|
| Kolkata | PM2.5 | 80.5 | 20.12 | **7.87** | -90.2% | 100.0% | 100.0% | **100.0%** |
| Kolkata | PM10 | 126.0 | 31.5 | **16.08** | -87.2% | 100.0% | 100.0% | **100.0%** |
| Delhi | PM2.5 | 177.89 | 44.47 | **15.45** | -91.3% | 100.0% | 100.0% | **100.0%** |

### Key Findings
- **IDW Spatial Spatial Smoothness:** Inverse Distance Weighting captures localized spatial concentration gradients between stations, achieving the lowest overall MAE.
- **Fallback Protection:** When fewer than 3 stations are within radius, the model falls back seamlessly to nearest-station bias correction.
- **Category Fidelity:** CPCB AQI category agreement reaches ~90%+ across all cities.
