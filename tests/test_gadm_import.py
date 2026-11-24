import io
import json
import zipfile
from io import StringIO

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from factory.catalog import importers
from factory.catalog.models import AdminArea

INDEX = """<html><body><h1>Index of /gadm/gadm4.1/json</h1>
<a href="gadm41_ATA_0.json.zip">gadm41_ATA_0.json.zip</a>
<a href="gadm41_NPL_0.json.zip">gadm41_NPL_0.json.zip</a>
<a href="gadm41_NPL_1.json.zip">gadm41_NPL_1.json.zip</a>
<a href="gadm41_NPL_2.json.zip">gadm41_NPL_2.json.zip</a>
<a href="gadm41_NPL_3.json.zip">gadm41_NPL_3.json.zip</a>
<a href="gadm41_VNM_0.json.zip">gadm41_VNM_0.json.zip</a>
<a href="gadm41_VNM_1.json.zip">gadm41_VNM_1.json.zip</a>
</body></html>"""


def _feature(iso3, level, index, x):
    """A tiny square with GADM attributes; ancestors are always the first area of each level."""
    props = {"GID_0": iso3, "COUNTRY": f"Country {iso3}"}
    for n in range(1, level + 1):
        props[f"GID_{n}"] = iso3 + ".1" * (n - 1) + f".{index if n == level else 1}_1"
        props[f"NAME_{n}"] = f"{iso3} L{n} {index}"
    ring = [[x, 0], [x + 1, 0], [x + 1, 1], [x, 1], [x, 0]]
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _gadm_zip(iso3, level):
    features = [_feature(iso3, level, 1, 0)] if level == 0 else [_feature(iso3, level, i, i) for i in (1, 2)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"gadm41_{iso3}_{level}.json", json.dumps({"type": "FeatureCollection", "features": features})
        )
    return buf.getvalue()


@pytest.fixture
def gadm(monkeypatch):
    """Serve the GADM index and zips from memory; `requested` lists the files asked for."""
    state = {"requested": [], "broken": set(), "missing": set()}

    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        if not name:
            return httpx.Response(200, text=INDEX)
        state["requested"].append(name)
        iso3, level = name.split("_")[1], int(name.split("_")[2].split(".")[0])
        if (iso3, level) in state["missing"]:
            return httpx.Response(404)
        if (iso3, level) in state["broken"]:
            return httpx.Response(200, content=b"not a zip")
        return httpx.Response(200, content=_gadm_zip(iso3, level))

    real_client = httpx.Client
    monkeypatch.setattr(
        importers.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    return state


def _run(*args, cache):
    out, err = StringIO(), StringIO()
    call_command("import_gadm", *args, "--cache-dir", str(cache), stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def test_list_gadm_countries_parses_the_index(gadm):
    assert importers.list_gadm_countries() == {"ATA": {0}, "NPL": {0, 1, 2, 3}, "VNM": {0, 1}}


def test_list_gadm_countries_fails_on_an_empty_index(monkeypatch):
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html></html>"))
    monkeypatch.setattr(importers.httpx, "Client", lambda **kw: real_client(transport=transport, **kw))
    with pytest.raises(RuntimeError, match="No GADM files"):
        importers.list_gadm_countries()


def test_all_imports_only_the_published_levels(db, gadm, tmp_path):
    out, _ = _run("--all", "--levels", "0,1,2", cache=tmp_path)
    assert sorted(gadm["requested"]) == [
        "gadm41_ATA_0.json.zip",
        "gadm41_NPL_0.json.zip",
        "gadm41_NPL_1.json.zip",
        "gadm41_NPL_2.json.zip",
        "gadm41_VNM_0.json.zip",
        "gadm41_VNM_1.json.zip",
    ]
    assert "6 imported, 0 skipped, 0 failed" in out
    assert AdminArea.objects.filter(country_id="NPL", level=2).count() == 2
    assert AdminArea.objects.get(code="NPL.1.2_1").parent.code == "NPL.1_1"
    assert not AdminArea.objects.filter(country_id="VNM", level=2).exists()


def test_skip_existing_leaves_imported_levels_alone(db, gadm, tmp_path, nepal):
    out, _ = _run("--all", "--levels", "0,1,2", "--skip-existing", cache=tmp_path)
    assert not [name for name in gadm["requested"] if "_NPL_" in name]
    assert "3 imported, 3 skipped, 0 failed" in out
    assert AdminArea.objects.filter(country_id="NPL", level=1).count() == 2  # the fixture's areas

    gadm["requested"].clear()
    _run("NPL", "VNM", "--levels", "0,1", "--skip-existing", cache=tmp_path / "fresh")
    assert gadm["requested"] == []


def test_exclude_leaves_countries_out(db, gadm, tmp_path):
    _run("--all", "--levels", "0", "--exclude", "npl, vnm", cache=tmp_path)
    assert gadm["requested"] == ["gadm41_ATA_0.json.zip"]


def test_a_failure_is_reported_and_the_rest_still_imports(db, gadm, tmp_path):
    gadm["broken"].add(("NPL", 1))
    with pytest.raises(CommandError, match="1 country/level imports failed"):
        _run("--all", "--levels", "0,1,2", cache=tmp_path)
    # NPL 2 is not tried without its parents; the other countries import
    assert "gadm41_NPL_2.json.zip" not in gadm["requested"]
    assert set(AdminArea.objects.values_list("country_id", "level")) == {
        ("ATA", 0),
        ("NPL", 0),
        ("VNM", 0),
        ("VNM", 1),
    }


def test_failure_summary_lists_the_pairs(db, gadm, tmp_path):
    gadm["broken"].update({("ATA", 0), ("VNM", 1)})
    err = StringIO()
    with pytest.raises(CommandError):
        call_command("import_gadm", "--all", "--cache-dir", str(tmp_path), stdout=StringIO(), stderr=err)
    summary = err.getvalue().split("Failed:")[1]
    assert "ATA 0:" in summary and "VNM 1:" in summary


def test_explicit_codes_warn_on_a_missing_level(db, gadm, tmp_path):
    gadm["missing"].add(("VNM", 2))
    out, err = _run("VNM", "--levels", "0,1,2", cache=tmp_path)
    assert "VNM level 2: not published by GADM" in err
    assert "2 imported, 0 skipped, 0 failed" in out


@pytest.mark.parametrize("args", [[], ["NPL", "--all"]])
def test_codes_or_all_is_required(db, gadm, tmp_path, args):
    with pytest.raises(CommandError, match="ISO3 codes or --all"):
        _run(*args, cache=tmp_path)
    assert gadm["requested"] == []
