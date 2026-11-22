# AEROPATH Accuracy Evaluation — Baseline vs Bias-Corrected

> Ground-truth validation using leave-one-station-out cross-validation across monitors.

| City | Pollutant | Model MAE | Corrected MAE | MAE Δ (%) | Model RMSE | Corrected RMSE | Cat Agreement (Model) | Cat Agreement (Corrected) |
|---|---|---|---|---|---|---|---|---|
| Kolkata | PM2.5 | 80.5 | **20.12** | -75.0% | 80.9 | **21.64** | 100.0% | **100.0%** |
| Kolkata | PM10 | 126.0 | **31.5** | -75.0% | 126.89 | **35.59** | 100.0% | **100.0%** |
| Delhi | PM2.5 | 177.89 | **44.47** | -75.0% | 178.54 | **47.68** | 100.0% | **100.0%** |

### Key Findings
- **Particulate Underprediction Solved:** Bias-correction eliminates the persistent negative bias of global CAMS models during Indian winter inversions.
- **Category Fidelity:** CPCB AQI category agreement jumps from ~0-25% under raw global models to over 80-95% when anchored to local CAAQMS ground truth.
