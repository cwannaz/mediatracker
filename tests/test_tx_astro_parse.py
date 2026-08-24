"""Parser tests for the Astro-era TX Group front-end (24 heures).

The fixtures are trimmed from real pages, keeping the structure that actually
matters: the class names the parsers key on, the nesting of the reply marker
inside the comment body, and a premium article that serves a figure but no
paragraph.
"""
from __future__ import annotations

from mediatracker.sources import tx_astro

_PAGE_DATA = """
<script id="page-data" type="application/json">
{"articleCategory":"savoirs","articleId":"185602408582","articlePremium":"0",
 "articleTitle":"Le recul des glaciers","articleType":"article",
 "articleSubcategory":"sciences","authorId":"85540151","authorName":"Laure Schmidt",
 "canonicalUrl":"https://www.24heures.ch/abeilles-185602408582","tenantLang":"fr"}
</script>
"""

_LD = """
<script type="application/ld+json" slot="head">
{"@context":"https://schema.org","@type":"NewsMediaOrganization","name":"24 heures"}
</script>
<script type="application/ld+json" slot="head">
{"@context":"https://schema.org","@type":"NewsArticle",
 "headline":"Abeilles: la fonte des glaciers pourrait favoriser les maladies",
 "alternativeHeadline":"La fonte des glaciers menace les abeilles",
 "articleId":"185602408582",
 "description":"Une étude menée en Valais montre que les infections augmentent.",
 "datePublished":"2026-08-24T16:23:52+02:00","dateModified":"2026-08-24T18:00:00+02:00",
 "inLanguage":"fr","articleSection":"savoirs","isAccessibleForFree":true,
 "url":"https://www.24heures.ch/abeilles-185602408582",
 "thumbnailUrl":"https://cdn.unitycms.io/images/thumb.jpg",
 "author":[{"@type":"Person","@id":"https://www.24heures.ch/author/85540151/x",
            "name":"Laure Schmidt"}]}
</script>
"""

_BODY = """
<article class="ArticleContainer" data-article-id="185602408582" data-article-body>
  <div class="ArticleElementsList">
    <figure class="ArticleImage article-element">
      <picture>
        <source srcset="https://cdn.unitycms.io/images/x.jpg?op=ocroped&amp;val=110 110w">
        <img src="https://cdn.unitycms.io/images/x.jpg?op=resized"
             data-image-url="https://cdn.unitycms.io/images/x.jpg"
             alt="Un insecte butinant une fleur." class="ResponsiveImage">
      </picture>
      <figcaption class="ContentCaption content-caption">
        <div><span class="HtmlText">Le recul des glaciers peut avoir un impact.</span></div>
        <div class="contentcredit">G. Losapio/UNIL</div>
      </figcaption>
    </figure>
    <p class="articleParagraph _content-width article-element">
      <span class="HtmlText">Le recul des glaciers pourrait favoriser les maladies chez les
      </span><a href="/abeilles-774285294135"><span>abeilles</span></a><span
      class="HtmlText">, selon une étude.</span>
    </p>
    <h2 class="HtmlText crosshead _content-width article-element">Un constat valaisan</h2>
    <p class="articleParagraph _content-width article-element">
      <span class="HtmlText">Les chercheurs ont étudié la zone du Mont-Miné.</span>
    </p>
    <aside class="DynamicTeaser article-element">
      <a href="/autre-sujet-123456789"><span>Lire aussi: un autre sujet</span></a>
    </aside>
  </div>
  <div class="content" id="comment-content" data-article-id="185602408582"
       data-article-type="article" data-comment-count="13"></div>
</article>
"""

_PREMIUM = """
<script id="page-data" type="application/json">
{"articleId":"644833589701","articlePremium":"1","articleCategory":"vaud-regions",
 "authorName":"Laurent Antonoff","articleType":"article"}
</script>
<script type="application/ld+json" slot="head">
{"@type":"NewsArticle","headline":"Lausanne: le PS réclame une piscine",
 "articleId":"644833589701","isAccessibleForFree":false,
 "datePublished":"2026-08-24T09:00:00+02:00","inLanguage":"fr"}
</script>
<article class="ArticleContainer" data-article-body>
  <div class="ArticleElementsList">
    <figure class="ArticleImage article-element">
      <img data-image-url="https://cdn.unitycms.io/images/pool.jpg" alt="La piscine">
      <figcaption class="ContentCaption">
        <div><span class="HtmlText">Les quatre piscines de quartier.</span></div>
        <div class="contentcredit">Philippe Maeder</div>
      </figcaption>
    </figure>
  </div>
  <div id="comment-content" data-article-id="644833589701"
       data-article-type="article" data-comment-count="74"></div>
</article>
"""


def _item(cid, nick, when, text, *, parent=None, up=None, down=None, bulb=None):
    marker = f'<span class="parent-nickname">@{parent} </span>' if parent else ""
    return f"""
    <section class="CommentItem">
      <div class="wrapper" id="comment-item-{cid}" data-nickname="{nick}">
        <div class="nickname"> {nick} </div>
        <div class="date"><time datetime="{when}" class="RelativeDateTime">il y a 1 heure</time></div>
        <div class="text  ">{marker}<p>{text}</p></div>
        <div class="buttons">
          <div class="vote-icons">
            <button class="upvote"><span class="icon">HEART</span>
              <span class="upvotes count">{up if up is not None else ""}</span></button>
            <button class="lightbulb"><span class="icon">BULB</span>
              <span class="lightbulbs count">{bulb if bulb is not None else ""}</span></button>
            <button class="downvote"><span class="icon">DOWN</span>
              <span class="downvotes count">{down if down is not None else ""}</span></button>
          </div>
          <div class="reply-section"><button class="reply">Répondre</button></div>
        </div>
      </div>
    </section>"""


def _fragment(items, *, cursor=None, last_index=None):
    tail = ""
    if last_index is not None:
        tail += f'<span data-last-comment-index="{last_index}" style="display:none"></span>'
    if cursor:
        tail += ('<div class="action-buttons"><div class="load-more-wrapper">'
                 f'<button class="LoadMoreButton" data-pagination-cursor="{cursor}">'
                 "</button></div></div>")
    return ("<!DOCTYPE html><style>.CommentItem{color:red}</style>"
            '<div class="CommentList" data-article-id="1" data-sort-order="newestFirst">'
            f'<ul class="comment-list">{"".join(items)}</ul></ul>{tail}</div>')


# --------------------------------------------------------------------------- #
# Articles
# --------------------------------------------------------------------------- #

def test_parse_article_fields():
    a = tx_astro.parse_article(_PAGE_DATA + _LD + _BODY, "https://www.24heures.ch/seen")
    assert a is not None
    assert a.source_key == "185602408582"
    assert a.headline.startswith("Abeilles: la fonte")
    assert a.subhead == "Une étude menée en Valais montre que les infections augmentent."
    assert a.author == "Laure Schmidt"
    assert a.lang == "fr"
    # ld+json url wins over the URL we happened to crawl.
    assert a.url == "https://www.24heures.ch/abeilles-185602408582"
    # Desk path is richer than ld+json's bare section.
    assert a.section == "savoirs/sciences"
    assert a.published_at.year == 2026 and a.published_at.month == 8
    assert a.updated_at != a.published_at
    assert a.comment_count == 13
    # The flyout states the content type the comment endpoint needs; a gallery
    # is "slideshow" and takes comments like anything else.
    assert a.raw_meta["articleType"] == "article"


def test_body_keeps_content_and_drops_teasers():
    a = tx_astro.parse_article(_PAGE_DATA + _LD + _BODY, "https://x/1")
    assert "Le recul des glaciers pourrait favoriser" in a.body_text
    assert "Un constat valaisan" in a.body_text
    assert "Mont-Miné" in a.body_text
    # The DynamicTeaser is a link to a *different* article, not this one's text.
    assert "Lire aussi" not in a.body_text
    # Inline links inside a paragraph keep their text and stay in one block.
    assert "maladies chez les abeilles, selon une étude." in a.body_text
    assert a.raw_meta["textBlocks"] == 3


def test_body_image_prefers_unresized_original():
    a = tx_astro.parse_article(_PAGE_DATA + _LD + _BODY, "https://x/1")
    assert len(a.images) == 1
    img = a.images[0]
    assert img.orig_url == "https://cdn.unitycms.io/images/x.jpg"  # not the ?op=resized src
    assert img.role == "hero"
    assert img.caption == "Le recul des glaciers peut avoir un impact. — G. Losapio/UNIL"
    assert img.alt_text == "Un insecte butinant une fleur."


def test_premium_article_reports_no_body_rather_than_a_caption():
    """A paywalled page renders figures but not one paragraph. Storing the photo
    credit as body_text would make an absent article look like a 150-char one."""
    a = tx_astro.parse_article(_PREMIUM, "https://x/2")
    assert a.source_key == "644833589701"
    assert a.body_text is None
    assert a.raw_meta["paywalled"] is True
    assert a.raw_meta["bodyWithheld"] is True
    assert a.raw_meta["textBlocks"] == 0
    # The picture is still captured, and so is the fact that it takes comments.
    assert len(a.images) == 1
    assert a.comment_count == 74


def test_page_without_identity_is_skipped():
    assert tx_astro.parse_article("<html><body>nothing</body></html>", "https://x/3") is None


# --------------------------------------------------------------------------- #
# Comments
# --------------------------------------------------------------------------- #

def test_parse_comments_fields_and_votes():
    frag = _fragment([
        _item("aaa11111", "Rac749", "2026-08-24T20:16:04.148Z", "Premier avis.", up=2),
        _item("bbb22222", "Marie03", "2026-08-24T13:51:09.505Z", "Deuxième avis.",
              up=26, down=2, bulb=1),
    ], last_index=2)
    cs, cursor, idx = tx_astro.parse_comments(frag)
    assert len(cs) == 2
    assert cursor is None      # no load-more button: this was the last page
    assert idx == 2
    a, b = cs
    assert (a.source_key, a.author_nick, a.body_text) == ("aaa11111", "Rac749", "Premier avis.")
    assert a.like_count == 2
    assert a.posted_at.hour == 20
    assert b.raw_meta["votes"] == {"up": 26, "down": 2, "bulb": 1}
    assert b.author_key is None          # the platform exposes only a nickname
    assert b.reply_count is None         # replies are flattened, never counted


def test_reply_marker_is_not_swallowed_into_the_body():
    """The @nickname span sits *inside* the .text block. Tracking a single
    current field made its closing tag end the body too, and every reply came
    out empty."""
    frag = _fragment([
        _item("ccc33333", "GB1204", "2026-08-24T14:34:20.142Z",
              "Un plombier ne se réduit pas à serrer des tuyaux.", parent="Christian", up=9),
    ])
    cs, _, _ = tx_astro.parse_comments(frag)
    assert len(cs) == 1
    c = cs[0]
    assert c.body_text == "Un plombier ne se réduit pas à serrer des tuyaux."
    assert c.raw_meta["reply_to_nickname"] == "Christian"
    assert "@Christian" not in (c.body_text or "")


def test_pagination_cursor_is_read_from_the_load_more_button():
    frag = _fragment([_item("ddd44444", "X", "2026-08-24T10:00:00.000Z", "Bonjour.")],
                     cursor="2024-01-16T06:33:27.592Z", last_index=27)
    cs, cursor, idx = tx_astro.parse_comments(frag)
    assert len(cs) == 1
    assert cursor == "2024-01-16T06:33:27.592Z"
    assert idx == 27


def test_link_replies_resolves_only_unambiguous_parents():
    """The fragment names a reply's parent by nickname alone. That is enough
    when one person said one thing, and not enough when they said several."""
    frag = _fragment([
        _item("p1", "Christian", "2026-08-24T13:16:00.000Z", "Premier message."),
        _item("p2", "Christian", "2026-08-24T13:28:00.000Z", "Second message."),
        _item("q1", "Solange", "2026-08-24T13:20:00.000Z", "Message unique."),
        _item("r1", "GB1204", "2026-08-24T14:34:00.000Z", "Réponse.", parent="Christian"),
        _item("r2", "GB1204", "2026-08-24T14:40:00.000Z", "Autre réponse.", parent="Solange"),
    ])
    cs, _, _ = tx_astro.parse_comments(frag)
    tx_astro.link_replies(cs)
    by_id = {c.source_key: c for c in cs}

    # Two candidates named Christian precede the reply: refuse to pick one.
    assert by_id["r1"].parent_source_key is None
    assert by_id["r1"].raw_meta["parent_ambiguous"] == 2
    assert by_id["r1"].raw_meta["reply_to_nickname"] == "Christian"

    # One candidate named Solange: link it.
    assert by_id["r2"].parent_source_key == "q1"
    assert "parent_ambiguous" not in by_id["r2"].raw_meta

    # Top-level comments stay unparented.
    assert by_id["p1"].parent_source_key is None


def test_comments_survive_an_unclosed_final_item():
    """A truncated fragment must still yield the comments it did contain."""
    frag = ('<div class="CommentList">'
            + _item("e1", "A", "2026-08-24T10:00:00.000Z", "Complet.")
            + '<section class="CommentItem"><div class="wrapper" id="comment-item-e2"'
              ' data-nickname="B"><div class="text"><p>Tronqué.</p>')
    cs, _, _ = tx_astro.parse_comments(frag)
    assert [c.source_key for c in cs] == ["e1", "e2"]
    assert cs[1].body_text == "Tronqué."
