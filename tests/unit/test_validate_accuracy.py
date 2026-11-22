"""Tests for offline validation harness (Sprint 1 - A3)."""

from __future__ import annotations

from pathlib import Path

from scripts.validate_accuracy import evaluate_leave_one_out, export_csv, export_markdown


def test_leave_one_out_offline_evaluation() -> None:
    fixtures_path = Path("tests/fixtures/validation_stations.json")
    assert fixtures_path.exists(), "Recorded fixtures file must exist for offline validation"

    results = evaluate_leave_one_out(fixtures_path)
    assert len(results) > 0, "Evaluation should yield results for recorded cities"

    kolkata_res = next((r for r in results if r["city"] == "Kolkata"), None)
    assert kolkata_res is not None
    assert "pollutants" in kolkata_res
    assert "PM2.5" in kolkata_res["pollutants"]

    pm25_metrics = kolkata_res["pollutants"]["PM2.5"]
    assert "model_only" in pm25_metrics
    assert "bias_corrected" in pm25_metrics

    # Bias correction should reduce MAE / RMSE compared to raw coarse model
    assert pm25_metrics["bias_corrected"]["mae"] < pm25_metrics["model_only"]["mae"]


def test_markdown_and_csv_export(tmp_path: Path) -> None:
    fixtures_path = Path("tests/fixtures/validation_stations.json")
    results = evaluate_leave_one_out(fixtures_path)

    md_path = tmp_path / "accuracy.md"
    csv_path = tmp_path / "accuracy.csv"

    export_markdown(results, md_path)
    export_csv(results, csv_path)

    assert md_path.exists()
    assert csv_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "# AEROPATH Accuracy Evaluation" in md_content
    assert "Kolkata" in md_content

    csv_content = csv_path.read_text(encoding="utf-8")
    assert "city,pollutant,samples,model_mae,corrected_mae" in csv_content
