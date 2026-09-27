import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_jquants_connection.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_jquants_connection", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"Mkt": "0111", "ProdCat": "011", "Code": "72030"}, "common_candidate"),
        ({"Mkt": "0104", "ProdCat": "011", "Code": "130A0"}, "common_candidate"),
        ({"Mkt": "0111", "ProdCat": "011", "Code": "25935"}, "unclassified"),
        ({"Mkt": "0112", "ProdCat": "099", "Code": "99990"}, "unclassified"),
        ({"Mkt": "0111", "ProdCat": "014", "Code": "13060"}, "excluded_prodcat"),
        ({"Mkt": "0111", "ProdCat": "013", "Code": "89510"}, "excluded_prodcat"),
        ({"Mkt": "0109", "ProdCat": "011", "Code": "12340"}, "out_of_market"),
        ({"Mkt": "0105", "ProdCat": "011", "Code": "12340"}, "out_of_market"),
    ],
)
def test_classify_never_guesses_common_stock(row: dict[str, str], expected: str) -> None:
    assert check.classify(row) == expected


def test_client_follows_pagination_and_saves_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [
        {"data": [{"Code": "10010"}], "pagination_key": "k1"},
        {"data": [{"Code": "10020"}]},
    ]
    seen: list[dict[str, str]] = []

    def fake_get(self: object, path: str, params: dict[str, str]) -> dict[str, object]:
        seen.append(dict(params))
        return pages[len(seen) - 1]

    monkeypatch.setattr(check.Client, "_get", fake_get)
    client = check.Client("key", 0.0, tmp_path)
    rows = client.get_all("/equities/master", {"date": "2026-06-01"}, "master")
    assert [r["Code"] for r in rows] == ["10010", "10020"]
    assert seen[1]["pagination_key"] == "k1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["master_0.json", "master_1.json"]


def test_missing_api_key_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JQUANTS_API_KEY", raising=False)
    monkeypatch.setattr(check, "PROJECT_ROOT", tmp_path)
    with pytest.raises(SystemExit):
        check.load_api_key()


def test_main_runs_end_to_end_with_fake_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_get(self: object, path: str, params: dict[str, str]) -> dict[str, object]:
        if path == "/markets/calendar":
            return {"data": [{"Date": params["to"], "HolDiv": "1"}]}
        if path == "/equities/master" and params.get("date") == "2026-06-01":
            return {
                "data": [
                    {"Code": "72030", "CoName": "A", "Mkt": "0111", "MktNm": "P", "ProdCat": "011"},
                    {"Code": "25935", "CoName": "B", "Mkt": "0111", "MktNm": "P", "ProdCat": "011"},
                ]
            }
        if path == "/equities/master":  # older snapshot / delisted lookup
            return {
                "data": [
                    {"Code": "72030", "CoName": "A", "Mkt": "0111", "MktNm": "P", "ProdCat": "011"},
                    {
                        "Code": "12340",
                        "CoName": "Gone",
                        "Mkt": "0112",
                        "MktNm": "S",
                        "ProdCat": "011",
                    },
                ]
            }
        if path == "/equities/bars/daily":
            return {"data": [{"Date": "2026-05-29", "Code": params["code"], "C": 100.0}]}
        return {"data": [{"Date": "2026-05-29", "C": 2800.0}]}  # TOPIX

    monkeypatch.setattr(check.Client, "_get", fake_get)
    monkeypatch.setattr(check, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("JQUANTS_API_KEY", "dummy")
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            "--date",
            "2026-06-01",
            "--min-interval",
            "0",
            "--delisted-code",
            "12340",
            "--listed-date",
            "2025-06-02",
        ],
    )
    assert check.main() == 0
    out = capsys.readouterr().out
    assert "UNCLASSIFIED (not included as common stock): 1" in out
    assert "12340 Gone" in out
    assert "present in master on 2026-06-01: False" in out
