import re
from pathlib import Path

from cli import make_run_timestamp, output_paths


def test_output_paths_writes_into_given_results_dir():
    json_path, png_path = output_paths(
        Path("samples/appraisal-1.png"),
        Path("samples/results/20260816T120000Z"),
    )

    assert json_path == Path("samples/results/20260816T120000Z/appraisal-1.json")
    assert png_path == Path(
        "samples/results/20260816T120000Z/appraisal-1-annotated.png"
    )


def test_make_run_timestamp_is_filesystem_safe_and_sortable():
    timestamp = make_run_timestamp()

    # No characters that are awkward/illegal in filenames or shell globs (e.g. ':').
    assert re.fullmatch(r"\d{8}T\d{6}Z", timestamp)
