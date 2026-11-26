import json
from pathlib import Path

import pytest

from imagetrace import db as dbmod
from imagetrace.audit import list_events, verify_chain
from imagetrace.cases import CaseRepository, ValidationError
from imagetrace.db import SCHEMA_VERSION, open_database
from imagetrace.evidence import EvidenceRepository
from imagetrace.hashing import sha256_file
from imagetrace.ocr import (
    OcrError,
    OcrService,
    TesseractNotFoundError,
    parse_tesseract_tsv,
    validate_language,
)
from synthetic_images import make_truncated_jpeg, make_valid_jpeg

SCOPE = "Synthetic OCR test scope."

TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)

SAMPLE_TSV = "\n".join(
    [
        TSV_HEADER,
        "1\t1\t0\t0\t0\t0\t0\t0\t320\t220\t-1\t",
        "5\t1\t1\t1\t1\t1\t10\t20\t60\t18\t94.5\tHARBOUR",
        "5\t1\t1\t1\t1\t2\t78\t20\t45\t18\t89.0\tGATE",
        "5\t1\t1\t1\t2\t1\t10\t46\t55\t18\t91.2\tSECRET_SIGN_9911",
        "5\t1\t2\t1\t1\t1\t10\t90\t120\t18\t88.0\t<b>UNTRUSTED</b>",
    ]
)


@pytest.fixture
def env(tmp_path):
    d = open_database(tmp_path)
    cases = CaseRepository(d, tmp_path / "cases", actor=lambda: "Tester")
    case = cases.create("OCR test case", SCOPE)
    ev_repo = EvidenceRepository(d, tmp_path, actor=lambda: "Tester")
    yield d, ev_repo, case, tmp_path
    d.close()


# --- pure unit tests ----------------------------------------------------------------------
@pytest.mark.parametrize("good", ["eng", "eng+deu", "chi_sim", "ara+eng+fra"])
def test_validate_language_accepts_valid(good):
    assert validate_language(good) == good


@pytest.mark.parametrize("bad", ["", " ", "--oem", "../eng", "eng;calc", "en g", "a"])
def test_validate_language_rejects_unsafe(bad):
    with pytest.raises(ValidationError):
        validate_language(bad)


def test_parse_tsv_reconstructs_text_confidence_and_bboxes():
    full_text, items, warns = parse_tesseract_tsv(SAMPLE_TSV)
    assert full_text == "HARBOUR GATE\nSECRET_SIGN_9911\n\n<b>UNTRUSTED</b>"
    assert len(items) == 4
    assert items[0].text == "HARBOUR"
    assert items[0].confidence == pytest.approx(94.5)
    assert items[0].bounding_box["left"] == 10 and items[0].bounding_box["width"] == 60
    assert warns == []


def test_parse_tsv_handles_empty_low_confidence_and_malformed_rows():
    # Empty
    txt, items, warns = parse_tesseract_tsv(TSV_HEADER + "\n")
    assert txt == "" and items == []
    assert any("no visible text" in w.lower() for w in warns)

    # Low confidence + malformed row + negative confidence + control chars
    tsv = "\n".join(
        [
            TSV_HEADER,
            "5\t1\t1\t1\t1\t1\t5\t5\t20\t10\t32.0\tblurry\x07word",
            "5\t1\t1\t1\t1\t2\tnot_an_int\t5\t20\t10\t40.0\tskipme",
            "5\t1\t1\t1\t1\t3\t30\t5\t20\t10\t-1\tnoconf",
            "short_malformed_line",
        ]
    )
    txt2, items2, warns2 = parse_tesseract_tsv(tsv)
    assert txt2 == "blurryword noconf"
    assert items2[0].text == "blurryword" and items2[1].confidence is None
    joined = "\n".join(warns2).lower()
    assert "malformed tsv" in joined
    assert "low average ocr confidence" in joined

    # Bad header
    with pytest.raises(OcrError, match="unrecognized TSV header"):
        parse_tesseract_tsv("col1\tcol2\n1\t2\n")


# --- migration v4 -> v5 -------------------------------------------------------------------
def test_migration_v4_to_v5_preserves_prior_tables_and_backs_up(tmp_path):
    dbmod.ensure_data_layout(tmp_path)
    d4 = dbmod.Database(dbmod.database_path(tmp_path))
    dbmod.migrate(d4, tmp_path / "backups", target=4)
    assert d4.schema_version == 4
    case = CaseRepository(d4, tmp_path / "cases").create("Legacy v4 case", SCOPE)
    ev = EvidenceRepository(d4, tmp_path).import_file(case.id, make_valid_jpeg(tmp_path / "a.jpg"))
    d4.close()

    d5 = open_database(tmp_path)
    try:
        assert d5.schema_version == SCHEMA_VERSION
        tables = {r[0] for r in d5.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"ocr_runs", "ocr_items"} <= tables
        assert EvidenceRepository(d5, tmp_path).get(ev.id) == ev
        assert len(list((tmp_path / "backups").glob("imagetrace-pre-migration-v4-*.sqlite"))) == 1
        assert d5.integrity_check() == []
    finally:
        d5.close()


# --- service integration tests ------------------------------------------------------------
def test_run_ocr_uses_temp_copy_preserves_original_and_never_logs_text(env, tmp_path):
    d, ev_repo, case, root = env
    ev = ev_repo.import_file(case.id, make_valid_jpeg(tmp_path / "sign.jpg"))
    orig_path = ev_repo.original_path(ev)
    orig_sha = ev.sha256

    seen_paths: list[Path] = []

    def fake_runner(img_path: Path, lang: str, _td: str):
        seen_paths.append(img_path)
        assert img_path.is_file()
        assert img_path != orig_path
        assert img_path.parent == (root / "temp").resolve() or img_path.parent == root / "temp"
        assert lang == "eng"
        return "Tesseract", "5.4.0-synth", SAMPLE_TSV

    svc = OcrService(d, ev_repo, actor=lambda: "Tester", runner=fake_runner)
    res = svc.run_ocr(ev.id, language="eng")

    # Temp copy cleaned up & original untouched
    assert len(seen_paths) == 1 and not seen_paths[0].exists()
    assert sha256_file(orig_path) == orig_sha

    assert res.run.engine == "Tesseract"
    assert res.run.engine_version == "5.4.0-synth"
    assert res.run.language == "eng"
    assert res.run.status == "ok"
    assert "HARBOUR GATE" in res.run.full_text
    assert "<b>UNTRUSTED</b>" in res.run.full_text
    assert len(res.items) == 4
    assert res.mean_confidence == pytest.approx(90.7, abs=0.2)

    # Privacy: raw OCR text MUST NOT be written to audit_events!
    events = list_events(d.con, evidence_id=ev.id)
    assert events[0].event_type == "ocr_completed"
    assert "SECRET_SIGN_9911" not in str(events[0].details)
    assert "HARBOUR" not in str(events[0].details)
    assert verify_chain(d.con).ok


def test_run_ocr_handles_malformed_image_safely_before_engine(env, tmp_path):
    d, ev_repo, case, root = env
    trunc = make_truncated_jpeg(tmp_path / "corrupt.jpg")
    ev = ev_repo.import_file(case.id, trunc)
    called = False

    def should_not_run(*_a):
        nonlocal called
        called = True
        return "Tesseract", "5.4.0", SAMPLE_TSV

    svc = OcrService(d, ev_repo, runner=should_not_run)
    with pytest.raises(OcrError, match="could not be decoded safely"):
        svc.run_ocr(ev.id)
    assert not called
    assert list((root / "temp").glob("ocr-*.png")) == []
    assert ev_repo.verify_integrity(ev.id).ok


def test_missing_tesseract_raises_clean_error(env, tmp_path):
    d, ev_repo, case, _root = env
    ev = ev_repo.import_file(case.id, make_valid_jpeg(tmp_path / "a.jpg"))
    svc = OcrService(
        d, ev_repo, tesseract_path_getter=lambda: str(tmp_path / "missing_tesseract.exe")
    )
    with pytest.raises(TesseractNotFoundError):
        svc.run_ocr(ev.id)


def test_export_ocr_json_and_txt(env, tmp_path):
    d, ev_repo, case, _root = env
    ev = ev_repo.import_file(case.id, make_valid_jpeg(tmp_path / "sign.jpg"))
    svc = OcrService(
        d, ev_repo, actor=lambda: "Tester", runner=lambda *_: ("Tesseract", "5.4.0", SAMPLE_TSV)
    )
    svc.run_ocr(ev.id, language="eng")

    json_dest = tmp_path / "out.json"
    jres = svc.export_ocr(ev.id, json_dest, fmt="json")
    assert sha256_file(json_dest) == jres.sha256
    data = json.loads(json_dest.read_text(encoding="utf-8"))
    assert "Automated result" in data["classification"]
    assert data["engine"] == "Tesseract" and data["engine_version"] == "5.4.0"
    assert data["evidence_sha256"] == ev.sha256
    assert "HARBOUR GATE" in data["full_text_untrusted"]
    assert len(data["items"]) == 4

    txt_dest = tmp_path / "out.txt"
    tres = svc.export_ocr(ev.id, txt_dest, fmt="txt")
    assert sha256_file(txt_dest) == tres.sha256
    txt = txt_dest.read_text(encoding="utf-8")
    assert "AUTOMATED RESULT" in txt
    assert "Tesseract 5.4.0" in txt
    assert "HARBOUR GATE" in txt

    events = list_events(d.con, evidence_id=ev.id)
    assert [e.event_type for e in events[:2]] == ["ocr_exported", "ocr_exported"]
    assert "SECRET_SIGN_9911" not in str(events[0].details)
    assert verify_chain(d.con).ok
