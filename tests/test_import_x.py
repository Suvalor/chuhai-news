import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("import_x", Path(__file__).resolve().parent.parent / "scripts" / "import_x.py")
ix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ix)


def test_article_blocks_to_paras():
    art = {"content": {"blocks": [
        {"type": "header-two", "text": "Why"},
        {"type": "unstyled", "text": "Intro"},
        {"type": "unstyled", "text": "  "},
        {"type": "ordered-list-item", "text": "one"},
        {"type": "ordered-list-item", "text": "two"},
        {"type": "unstyled", "text": "mid"},
        {"type": "ordered-list-item", "text": "again"},
        {"type": "unordered-list-item", "text": "dot"},
        {"type": "atomic", "text": " ", "entityRanges": [{"key": 3}]},
        {"type": "atomic", "text": " ", "entityRanges": [{"key": 4}]},  # 不是图片的嵌入，丢掉
    ], "entityMap": [{"key": "3", "value": {"type": "MEDIA", "data": {"mediaItems": [{"mediaId": "9"}]}}},
                     {"key": "4", "value": {"type": "TWEET", "data": {}}}]},
        "media_entities": [{"media_id": "9", "media_info": {"original_img_url": "https://pbs.twimg.com/a.jpg"}}]}
    assert ix.article_paras(art) == [("h2", "Why", ""), ("p", "Intro", ""), ("p", "1. one", ""), ("p", "2. two", ""),
                                     ("p", "mid", ""), ("p", "1. again", ""), ("p", "• dot", ""),
                                     ("img", "", "https://pbs.twimg.com/a.jpg")]


def test_note_paras_and_title():
    t = {"text": "First line. More here\n\nSecond para https://t.co/abc",
         "media": {"all": [{"type": "photo", "url": "https://pbs.twimg.com/b.jpg"}, {"type": "video", "url": "x"}]}}
    assert ix.note_paras(t) == [("p", "First line. More here", ""), ("p", "Second para", ""),
                                ("img", "", "https://pbs.twimg.com/b.jpg")]
    assert ix.note_title(t["text"]) == "First line."


def test_translate_skips_empty_chunks_and_checks_count():
    calls = []

    def fake(system, payload):
        calls.append(len(payload["items"]))
        return {"items": ["译" + s for s in payload["items"]]}

    texts = ["a", "", "b"] + ["x" * 10] * 70
    out = ix.translate(texts, "zh", call=fake)
    assert out[:3] == ["译a", "", "译b"] and len(out) == 73
    assert calls == [60, 12]  # 空串不送，按 60 条一批

    with pytest.raises(ValueError, match="条数对不上"):
        ix.translate(["a", "b"], "zh", call=lambda s, p: {"items": ["只有一条"]})
