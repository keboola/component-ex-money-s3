"""Dev utility (NOT a pytest test) — record VCR cassettes + datadir fixtures for every
Money S3 endpoint against the live test instance.

Usage:
    .venv/bin/python tests/functional/record_cassettes.py [endpoint ...]

Reads real credentials from data/config.json (git-ignored), records each endpoint with the
default "all fields / full load" configuration, scrubs every secret and the host, then writes:
    tests/functional/cassettes/<endpoint>.yaml          (secrets scrubbed -> PLACEHOLDER_*)
    tests/functional/<endpoint>/source/data/config.json (placeholder creds, localhost host)
    tests/functional/<endpoint>/expected/data/out/...    (produced output for the assertion)

Endpoints whose recording run fails (non-collection roots, server errors) are skipped and
reported at the end.
"""

import json
import re
import runpy
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote

import vcr
import yaml

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FUNC = ROOT / "tests" / "functional"
CASS = FUNC / "cassettes"
sys.path.insert(0, str(SRC))

from auth import TokenProvider  # noqa: E402
from client import MoneyS3Client  # noqa: E402

_REAL = json.loads((ROOT / "data" / "config.json").read_text())
_RP = _REAL["parameters"]
_REAL_HOST = _RP["base_url"].rstrip("/")
_REAL_APP_ID = unquote(_REAL["image_parameters"]["app_id"])  # stored value is pre-URL-encoded
_REAL_CLIENT_ID = _RP["client_id"]
_REAL_SECRET = _RP["#client_secret"]
_REAL_AGENDA = _RP["agenda_id"]

_PH_HOST = "http://localhost:28085"
_PH = {
    "app_id": "PLACEHOLDER_APPID",
    "client_id": "PLACEHOLDER_CLIENTID",
    "secret": "PLACEHOLDER_SECRET",
    "agenda": "PLACEHOLDER_AGENDA",
    "token": "PLACEHOLDER_BEARER_TOKEN",
}

_MATCH_ON = ["method", "scheme", "host", "port", "path", "query", "body"]


def list_endpoints():
    tokens = TokenProvider(_RP["base_url"], _REAL_APP_ID, _REAL_CLIENT_ID, _REAL_SECRET)
    client = MoneyS3Client(_RP["base_url"], tokens, _REAL_AGENDA)
    return sorted(r["name"] for r in client.query_roots())


def _recording_config(endpoint, fields):
    return {
        "parameters": {
            "base_url": _REAL_HOST,
            "client_id": _REAL_CLIENT_ID,
            "#client_secret": _REAL_SECRET,
            "agenda_id": _REAL_AGENDA,
            "endpoint": endpoint,
            "fields": fields,
            "load_type": "full_load",
            "destination": {},
        },
        "image_parameters": {"app_id": _REAL_APP_ID},
        "action": "run",
    }


def _fixture_config(endpoint, fields):
    return {
        "parameters": {
            "base_url": _PH_HOST,
            "client_id": _PH["client_id"],
            "#client_secret": _PH["secret"],
            "agenda_id": _PH["agenda"],
            "endpoint": endpoint,
            "fields": fields,
            "load_type": "full_load",
            "destination": {},
        },
        "image_parameters": {"app_id": _PH["app_id"]},
        "action": "run",
    }


_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        tokens = TokenProvider(_RP["base_url"], _REAL_APP_ID, _REAL_CLIENT_ID, _REAL_SECRET)
        _CLIENT = MoneyS3Client(_RP["base_url"], tokens, _REAL_AGENDA)
    return _CLIENT


def _scalar_fields(endpoint):
    from schema import classify_fields

    item_type, _ = _client().endpoint_meta(endpoint)
    scalars, _, _ = classify_fields(_client().type_fields(item_type))
    return scalars


def _extract_real_token(cassette_text):
    m = re.search(r'"access_token"\s*:\s*"([^"]+)"', cassette_text)
    return m.group(1) if m else None


def _scrub(cassette_path: Path):
    text = cassette_path.read_text()
    token = _extract_real_token(text)
    text = text.replace(_REAL_HOST, _PH_HOST)
    text = re.sub(r"AppId=[^&\"'\s\\]+", f"AppId={_PH['app_id']}", text)
    for real, ph in (
        (_REAL_APP_ID, _PH["app_id"]),
        (quote(_REAL_APP_ID, safe=""), _PH["app_id"]),
        (_REAL_CLIENT_ID, _PH["client_id"]),
        (quote_plus(_REAL_CLIENT_ID), _PH["client_id"]),
        (_REAL_SECRET, _PH["secret"]),
        (quote_plus(_REAL_SECRET), _PH["secret"]),
        (_REAL_AGENDA, _PH["agenda"]),
    ):
        text = text.replace(real, ph)
    if token:
        text = text.replace(token, _PH["token"])
    cassette_path.write_text(text)


def _dedupe(cassette_path: Path):
    data = yaml.safe_load(cassette_path.read_text())
    seen, kept = set(), []
    for it in data["interactions"]:
        req = it["request"]
        key = (req["method"], req["uri"], req.get("body"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(it)
    data["interactions"] = kept
    cassette_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def record(endpoint, fields=None) -> tuple[bool, str]:
    fields = [] if fields is None else fields
    tmp_cass = CASS / f"{endpoint}.yaml"
    if tmp_cass.exists():
        tmp_cass.unlink()
    with tempfile.TemporaryDirectory() as d:
        datadir = Path(d)
        for sub in ("out/tables", "out/files", "in/tables", "in/files"):
            (datadir / sub).mkdir(parents=True, exist_ok=True)
        (datadir / "config.json").write_text(json.dumps(_recording_config(endpoint, fields)))
        myvcr = vcr.VCR(cassette_library_dir=str(CASS), record_mode="all", match_on=_MATCH_ON)
        import os

        os.environ["KBC_DATADIR"] = str(datadir)
        try:
            with myvcr.use_cassette(f"{endpoint}.yaml"):
                runpy.run_path(str(SRC / "component.py"), run_name="__main__")
        except SystemExit as e:
            if e.code not in (0, None):
                if tmp_cass.exists():
                    tmp_cass.unlink()
                return False, f"component exited {e.code}"
        except Exception as e:  # noqa: BLE001
            if tmp_cass.exists():
                tmp_cass.unlink()
            return False, f"{type(e).__name__}: {e}"

        _scrub(tmp_cass)
        _dedupe(tmp_cass)

        # write fixture
        case = FUNC / endpoint
        if case.exists():
            shutil.rmtree(case)
        src_cfg = case / "source" / "data"
        src_cfg.mkdir(parents=True)
        (src_cfg / "config.json").write_text(json.dumps(_fixture_config(endpoint, fields), indent=2))
        exp_tables = case / "expected" / "data" / "out" / "tables"
        exp_tables.mkdir(parents=True)
        produced = datadir / "out" / "tables"
        if produced.exists():
            for f in produced.iterdir():
                shutil.copy2(f, exp_tables / f.name)
        n = len(list(exp_tables.iterdir()))
        return True, f"{n} output files"


def main():
    targets = sys.argv[1:] or list_endpoints()
    print(f"Recording {len(targets)} endpoint(s)...")
    ok, skipped = [], []
    for ep in targets:
        success, msg = record(ep)
        if not success:
            # all-fields failed (often an unnamed "Unexpected Execution Error" on a
            # nested/list field) -> retry with scalars only.
            try:
                scalars = _scalar_fields(ep)
            except Exception as e:  # noqa: BLE001
                scalars = None
                msg = f"{msg}; scalars introspection failed: {e}"
            if scalars:
                success, msg2 = record(ep, fields=scalars)
                msg = f"scalars-only ({msg2})" if success else f"{msg}; scalars-only also failed: {msg2}"
        print(f"  {'OK ' if success else 'SKIP'} {ep}: {msg}")
        (ok if success else skipped).append(ep)
    print(f"\nRecorded {len(ok)} endpoints; skipped {len(skipped)}: {skipped}")


if __name__ == "__main__":
    main()
