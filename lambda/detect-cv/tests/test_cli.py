from pathlib import Path

from cli import output_paths


def test_output_paths_uses_cv_suffix():
    json_path, png_path = output_paths(
        Path("samples/appraisal-1.png"), Path("samples/results")
    )

    assert json_path == Path("samples/results/appraisal-1-cv.json")
    assert png_path == Path("samples/results/appraisal-1-cv-annotated.png")
