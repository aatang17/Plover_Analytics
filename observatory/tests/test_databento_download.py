"""No-network checks for the metered futures downloader's spending safeguards."""
import importlib.util
import io
import json
from datetime import date
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "databento_download", Path(__file__).parents[1] / "tools" / "databento_download.py")
downloader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(downloader)


def plan():
    return downloader.make_plan(date(2026, 10, 5), date(2026, 10, 6),
                                "continuous", "volume", ["nasdaq-100"])


def test_quote_above_limit_never_requests_data(monkeypatch, tmp_path):
    class QuoteOnly:
        def __init__(self, key):
            pass

        def metadata(self, endpoint, params):
            if endpoint == "metadata.get_dataset_range":
                return {"schema": {"ohlcv-1h": {
                    "start": "2010-01-01T00:00:00.000000000Z",
                    "end": "2026-10-10T00:00:00.000000000Z"}}}
            if endpoint == "metadata.get_cost":
                return 1
            assert endpoint == "symbology.resolve"
            return {"result": {params["symbols"]: [
                {"d0": "2026-10-05", "d1": "2026-10-06", "s": "123"}]}}

        def open(self, *args, **kwargs):
            pytest.fail("An over-budget request must never download data")

    monkeypatch.setattr(downloader, "Client", QuoteOnly)
    monkeypatch.setattr(downloader, "read_key", lambda: "unused")
    with pytest.raises(downloader.DownloadError, match="exceeds"):
        downloader.main(["--start", "2026-10-05", "--end", "2026-10-06",
                         "--products", "nasdaq-100", "--output", str(tmp_path),
                         "--download", "--max-cost-usd", "0"])
    assert list(tmp_path.iterdir()) == []


def test_completed_download_is_reused_without_auth_or_network(monkeypatch, tmp_path):
    item = plan()[0]
    body = (b"ts_event,instrument_id,publisher_id,open,high,low,close,volume\n"
            b"2026-10-05T00:00:00Z,123,1,100,101,99,100,20\n")

    class Download:
        def open(self, endpoint, params, post=False):
            assert endpoint == "timeseries.get_range" and post
            assert params["stype_out"] == "instrument_id"
            assert params["map_symbols"] == "false"
            return io.BytesIO(body)

    downloader.download(Download(), tmp_path, item)
    assert downloader.completed(tmp_path, item)
    receipt = json.loads((tmp_path / (item["filename"] + ".json")).read_text())
    assert receipt["rows"] == 1

    def forbidden():
        pytest.fail("Complete files must not trigger authentication or API requests")

    monkeypatch.setattr(downloader, "read_key", forbidden)
    assert downloader.main(["--start", "2026-10-05", "--end", "2026-10-06",
                            "--products", "nasdaq-100", "--output", str(tmp_path)]) == 0


def test_interrupted_stream_blocks_repeat_billing(tmp_path):
    item = plan()[0]

    class Interrupted(io.BytesIO):
        def read(self, *args):
            raise OSError("simulated connection interruption")

    class Download:
        def open(self, *args, **kwargs):
            return Interrupted(b"ts_event,instrument_id,publisher_id,open,high,low,close,volume\n")

    with pytest.raises(OSError):
        downloader.download(Download(), tmp_path, item)
    with pytest.raises(downloader.DownloadError, match="interrupted"):
        downloader.completed(tmp_path, item)


def test_cost_quote_uses_form_post():
    client = downloader.Client("unused-test-credential")

    class Capture:
        def open(self, request, timeout):
            assert request.get_method() == "POST"
            assert request.data == b"dataset=GLBX.MDP3"
            return io.BytesIO(b"0.25")

    client._opener = Capture()
    assert client.metadata("metadata.get_cost", {"dataset": "GLBX.MDP3"}) == 0.25


def test_hourly_trial_and_minute_outputs_do_not_collide():
    hourly = plan()[0]
    minute = downloader.make_plan(date(2026, 10, 5), date(2026, 10, 6),
                                  "continuous", "volume", ["nasdaq-100"],
                                  schema="ohlcv-1m")[0]
    assert hourly["request"]["schema"] == "ohlcv-1h"
    assert hourly["request"]["symbols"] == minute["request"]["symbols"] == "NQ.v.0"
    assert hourly["filename"] != minute["filename"]
    # Existing minute downloads keep their original filenames and receipts.
    assert minute["filename"] == "nasdaq-100_NQ.v.0_2026-10-05_2026-10-06.csv.gz"


def test_hourly_quote_requires_hourly_coverage():
    class MinuteOnly:
        def metadata(self, endpoint, params):
            assert endpoint == "metadata.get_dataset_range"
            return {"schema": {"ohlcv-1m": {
                "start": "2010-01-01T00:00:00.000000000Z",
                "end": "2026-10-10T00:00:00.000000000Z"}}}

    with pytest.raises(downloader.DownloadError, match="ohlcv-1h coverage unavailable"):
        downloader.quote(MinuteOnly(), plan())


def test_offline_plan_schema_selection(capsys):
    for schema in ("ohlcv-1h", "ohlcv-1m"):
        assert downloader.main(["--plan", "--schema", schema,
                                "--start", "2026-10-05", "--end", "2026-10-06",
                                "--products", "nasdaq-100"]) == 0
        items = json.loads(capsys.readouterr().out)
        assert len(items) == 1
        assert items[0]["request"]["schema"] == schema
