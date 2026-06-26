import json
from pathlib import Path

from predict import predict


def test_text_shoe_precision():
    assert predict("shoe", ["red shoe", "sandal"]) >= 0.8


def test_writes_predictions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from predict import main

    main()
    data = json.loads((Path("results") / "predictions.json").read_text(encoding="utf-8"))
    assert data["cases"]["text_shoe"]["precision"] >= 0.8
