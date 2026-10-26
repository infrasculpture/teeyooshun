"""Loading docs/topics.yaml (the real topic tree, which holds no source material)."""

import pytest

from app.importer import import_questions
from app.topics import TopicsError, load_topics, read_topics, topic_ids, unknown_question_topics


def test_topic_tree_reads():
    topics = read_topics()
    ids = {t["id"] for t in topics}
    assert len(topics) == len(ids) == 35
    assert "sci-07-particulate-nature" in ids and "geo-school-weather-climate" in ids


def test_load_is_repeatable(conn):
    assert load_topics(conn) == 35
    assert load_topics(conn) == 35
    assert conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 35
    row = conn.execute("SELECT * FROM topics WHERE id = 'geo-school-weather-climate'").fetchone()
    assert row["subject"] == "geography" and row["school_added"] == 1


def test_duplicate_topic_id_rejected(tmp_path):
    path = tmp_path / "topics.yaml"
    path.write_text("science:\n  - {id: a, title: A}\n  - {id: a, title: B}\n", encoding="utf-8")
    with pytest.raises(TopicsError, match="duplicate topic id 'a'"):
        read_topics(path)


def test_unknown_question_topics_are_listed(conn, points_question):
    load_topics(conn)
    points_question["topic"] = "sci-99-typo"
    import_questions(conn, [points_question])
    assert unknown_question_topics(conn) == [("sci-test-points-q1", "sci-99-typo")]
    assert "sci-99-typo" not in topic_ids(conn)
