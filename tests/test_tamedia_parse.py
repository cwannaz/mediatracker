from __future__ import annotations

import json

from mediatracker.sources import tamedia

_NEXT = {
    "props": {"pageProps": {"data": {
        "id": "999",
        "content": {
            "id": "103621502",
            "meta": {
                "published": "2026-08-23T14:57:28.108Z",
                "updated": "2026-08-23T15:30:00.000Z",
                "mainCategoryName": "Athlétisme",
                "urlSlug": "record-semi",
                "kickword": "Sport",
                "tags": ["athlétisme"],
                "wordCount": 271,
                "authors": [
                    {"name": "Agence France-Presse"},
                    {"name": "Jean Dupont"},
                ],
                "settings": {"commentingEnabled": True},
                "teaser": {"default": {"title": "Teaser title"}},
            },
            "article": {
                "title": "L’Éthiopien Kejelcha bat le record",
                "titleHeader": "Athlétisme",
                "lead": "<b>Yomif Kejelcha</b> a battu le record.",
                "elements": [
                    {"type": "publishDate", "publishedTime": "2026-08-23T14:57:28.108Z"},
                    {"type": "title", "htmlText": "ignored"},
                    {"type": "lead", "htmlText": "ignored"},
                    {"type": "authors", "authors": []},
                    {"type": "image", "image": {
                        "id": "PC18Zpv0AjSA",
                        "credit": "Sven Hoppe/dpa",
                        "caption": {"text": "Kejelcha en course"},
                        "variants": {
                            "400px": {"src": "https://image.lematin.ch/x-400.jpg", "width": 400},
                            "base": {"src": "https://image.lematin.ch/x-base.jpg", "width": 4582},
                        },
                    }},
                    {"type": "textBlockArray", "items": [
                        {"type": "htmlTextItem", "htmlText": "Premier paragraphe."},
                        {"type": "htmlTextItem", "htmlText": "Deuxième paragraphe."},
                    ]},
                    {"type": "ad"},
                    {"type": "textBlockArray", "items": [
                        {"type": "htmlTextItem", "htmlText": "Troisième paragraphe."},
                    ]},
                ],
            },
        },
    }}}
}


def test_extract_next_data_roundtrip():
    html = (
        'prefix<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(_NEXT)
        + "</script>suffix"
    )
    assert tamedia.extract_next_data(html) == _NEXT
    assert tamedia.extract_next_data("<html>no data</html>") is None


def test_parse_article_fields():
    art = tamedia.parse_article(_NEXT, "https://www.lematin.ch/story/record-semi-103621502")
    assert art is not None
    assert art.source_key == "103621502"
    assert art.headline == "L’Éthiopien Kejelcha bat le record"
    assert art.subhead == "Yomif Kejelcha a battu le record."  # html stripped
    assert art.author == "Agence France-Presse, Jean Dupont"
    assert art.section == "Athlétisme"
    assert art.lang == "fr"
    assert art.published_at.year == 2026 and art.published_at.month == 8
    assert art.updated_at.hour == 15
    assert art.raw_meta["commentingEnabled"] is True


def test_parse_article_body_and_images():
    art = tamedia.parse_article(_NEXT, "https://www.lematin.ch/story/x-1")
    # three paragraphs survive; the ad and the duplicated title/lead do not
    assert art.body_html.count("<p>") == 3
    assert "Premier paragraphe." in art.body_text
    assert "ignored" not in art.body_text
    # one image, hero role, full-res base variant chosen
    assert len(art.images) == 1
    img = art.images[0]
    assert img.role == "hero"
    assert img.orig_url == "https://image.lematin.ch/x-base.jpg"
    assert img.caption == "Kejelcha en course — Sven Hoppe/dpa"
    assert "<figure>" in art.body_html


def test_story_href_regex_finds_paths():
    html = 'x <a href="/story/foo-123">a</a> <a href="/story/bar-456?utm=1">b</a> <a href="/live/x">c</a>'
    paths = sorted(set(tamedia._STORY_HREF_RE.findall(html)))
    assert paths == ["/story/bar-456", "/story/foo-123"]


def test_parse_article_teaser_image_fallback():
    data = json.loads(json.dumps(_NEXT))  # deep copy
    # remove the body image element -> parser must fall back to teaser image
    els = data["props"]["pageProps"]["data"]["content"]["article"]["elements"]
    data["props"]["pageProps"]["data"]["content"]["article"]["elements"] = [
        e for e in els if e.get("type") != "image"
    ]
    data["props"]["pageProps"]["data"]["content"]["meta"]["teaser"]["default"]["image"] = {
        "id": "TEASER1", "description": "photo teaser",
        "variants": {"base": {"src": "https://image.lematin.ch/teaser-base.jpg", "width": 3000}},
    }
    art = tamedia.parse_article(data, "https://www.lematin.ch/story/x-2")
    assert len(art.images) == 1
    assert art.images[0].role == "hero"
    assert art.images[0].orig_url == "https://image.lematin.ch/teaser-base.jpg"
