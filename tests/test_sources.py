"""source.yaml loading and checks (made-up folders)."""

import pytest

from app.sources import SourceError, check_unit_topics, load_source, material_type, topic_for_file, unit_of
from ingest_helpers import SOURCE_YAML


def write(tmp_path, text):
    folder = tmp_path / "test-book"
    folder.mkdir()
    (folder / "source.yaml").write_text(text, encoding="utf-8")
    return folder


def test_valid_source_loads(tmp_path):
    cfg = load_source(write(tmp_path, SOURCE_YAML))
    assert (cfg.subject, cfg.level, cfg.slug, cfg.title) == ("science", "sec1", "test-book", "Test Book")
    assert cfg.units == {"01": "sci-01-scientific-endeavour"}
    assert cfg.warnings == []


@pytest.mark.parametrize("remove, message", [
    ("subject: science\n", "'subject' is missing"),
    ("level: sec1\n", "'level' is missing"),
])
def test_missing_required_key_is_an_error(tmp_path, remove, message):
    with pytest.raises(SourceError, match=message):
        load_source(write(tmp_path, SOURCE_YAML.replace(remove, "")))


def test_missing_files_is_an_error(tmp_path):
    text = "title: X\nsubject: science\nlevel: sec1\n"
    with pytest.raises(SourceError, match="'files' is missing"):
        load_source(write(tmp_path, text))


def test_unknown_material_type_is_an_error(tmp_path):
    with pytest.raises(SourceError, match="unknown material type"):
        load_source(write(tmp_path, SOURCE_YAML.replace("answer_key", "answer_keys")))


def test_unknown_key_is_a_warning(tmp_path):
    cfg = load_source(write(tmp_path, SOURCE_YAML + "colour: blue\n"))
    assert any("unknown key 'colour'" in w for w in cfg.warnings)


def test_missing_source_yaml(tmp_path):
    with pytest.raises(SourceError, match="not found"):
        load_source(tmp_path)


def test_unquoted_unit_numbers_are_normalised(tmp_path):
    cfg = load_source(write(tmp_path, SOURCE_YAML.replace('"01":', "1:")))
    assert cfg.units == {"01": "sci-01-scientific-endeavour"}


def test_first_matching_pattern_wins(tmp_path):
    cfg = load_source(write(tmp_path, SOURCE_YAML))
    assert material_type(cfg, "qns_c01.pdf") == "questions"
    assert material_type(cfg, "answers.pdf") == "answer_key"
    assert material_type(cfg, "notes.pdf") is None


@pytest.mark.parametrize("name, unit", [
    ("qns_c01.pdf", "01"), ("qns_u02.pdf", "02"), ("rev_u01.pdf", "01"), ("qns_c05a.pdf", "05"),
    ("answers.pdf", None), ("2024_jwss_eoy.pdf", None),
])
def test_unit_of(name, unit):
    assert unit_of(name) == unit


def test_numbered_file_without_unit_is_unassigned_with_warning(tmp_path):
    cfg = load_source(write(tmp_path, SOURCE_YAML))
    topic, warning = topic_for_file(cfg, "qns_c07.pdf")
    assert topic == "unassigned" and "no 'units' entry for 07" in warning
    assert topic_for_file(cfg, "qns_c01.pdf") == ("sci-01-scientific-endeavour", None)


def test_unit_topic_typo_is_reported(tmp_path):
    cfg = load_source(write(tmp_path, SOURCE_YAML.replace("sci-01-scientific-endeavour", "sci-01-scientfic")))
    assert check_unit_topics(cfg, {"sci-01-scientific-endeavour"}) == [
        "unit 01: topic 'sci-01-scientfic' is not in topics.yaml"]
