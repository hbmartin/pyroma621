"""Grammar-generated destinations and transformations with known outcomes."""

import html
from dataclasses import dataclass
from urllib.parse import quote

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from pyroma._markdown import destinations
from pyroma.ratings import ReadmeRelativeLinks, _relative_description_targets

from .strategies import WORDS, markdown_targets


@dataclass(frozen=True)
class Fragment:
    source: str
    target: str


@st.composite
def fragments(draw: st.DrawFn) -> Fragment:
    target = draw(markdown_targets())
    label = draw(WORDS)
    style = draw(st.sampled_from(("inline", "image", "reference", "escaped", "html", "angle")))
    title = draw(st.sampled_from(("", ' "A title"', " 'A title'")))
    if style == "html":
        source = f'<a href="{html.escape(target, quote=True)}">{label}</a>'
    elif style == "reference":
        source = f"[{label}][{label}]\n\n[{label}]: <{html.escape(target)}>{title}"
    else:
        encoded = html.escape(target)
        if style == "escaped":
            encoded = encoded.replace("(", r"\(").replace(")", r"\)").replace(".", "&#46;")
        elif style == "angle":
            encoded = f"<{encoded}>"
        source = f"{'!' if style == 'image' else ''}[{label}]({encoded}{title})"
    return Fragment(source, target)


@given(fragments())
@example(Fragment('[docs](docs/a(b).md "Title")', "docs/a(b).md"))
@example(Fragment(r"[docs](docs/a\(b\)&#46;md)", "docs/a(b).md"))
@example(Fragment('<img src="images/a.png?a=1&amp;b=2"/>', "images/a.png?a=1&b=2"))
def test_generated_fragment_has_known_destination(fragment: Fragment) -> None:
    assert destinations(fragment.source) == [fragment.target]


@given(st.lists(fragments(), min_size=1, max_size=4))
def test_document_order_with_independent_reference_names(parts: list[Fragment]) -> None:
    # Separate each fragment's reference namespace, even when Hypothesis repeats it.
    renamed = []
    for index, part in enumerate(parts):
        source = part.source
        if "\n\n[" in part.source:
            source, definition = part.source.split("\n\n", 1)
            old = definition[1 : definition.index("]")]
            source = (
                source.replace(f"][{old}]", f"][ref{index}]")
                + "\n\n"
                + definition.replace(f"[{old}]", f"[ref{index}]", 1)
            )
        renamed.append(Fragment(source, part.target))
    assert destinations("\n\n".join(part.source for part in renamed)) == [part.target for part in renamed]


@pytest.mark.parametrize("wrapper", ["span", "backtick", "tilde", "comment"])
@given(fragment=fragments())
def test_literal_regions_suppress_enclosed_destinations(fragment: Fragment, wrapper: str) -> None:
    # Code spans cannot cross paragraph boundaries. Keep reference definitions
    # outside the span, and wrap only their link use in the same paragraph.
    inline, separator, definitions = fragment.source.partition("\n\n")
    enclosed = {
        "span": f"`{inline}`" + (separator + definitions if separator else ""),
        "backtick": f"```markdown\n{fragment.source}\n```",
        "tilde": f"~~~\n{fragment.source}\n~~~",
        "comment": f"<!-- {fragment.source} -->",
    }[wrapper]
    assert destinations(enclosed) == []
    assert destinations(enclosed + "\n\n[outside](docs/outside.md)") == ["docs/outside.md"]


@given(target=markdown_targets(), first=WORDS, second=WORDS, gap=st.sampled_from((" ", "  ", "\t")))
def test_reference_label_case_and_whitespace_are_normalized(target: str, first: str, second: str, gap: str) -> None:
    source = f"[docs][{first.upper()}{gap}{second.upper()}]\n\n[{first} {second}]: <{html.escape(target)}>"
    assert destinations(source) == [target]


@given(st.lists(markdown_targets(), min_size=1, max_size=4))
@example(["docs/a.md", "docs/a.md", "https://example.invalid/docs/a.md"])
def test_relative_filtering_preserves_first_occurrence(targets: list[str]) -> None:
    source = " ".join(f"[docs](<{html.escape(target)}>)" for target in targets)
    source += " [anchor](#section) [mail](mailto:a@example.invalid)"
    expected = list(dict.fromkeys(target for target in targets if target.startswith("docs/")))
    assert _relative_description_targets(source, "text/markdown") == expected
    assert _relative_description_targets(source, "text/plain") == []


@pytest.mark.parametrize("source", ["[x](<docs/unclosed.md)", '[x](docs/a.md "unclosed)', "[unknown][missing]"])
def test_ambiguous_or_unresolved_destinations_are_skipped(source: str) -> None:
    assert destinations(source) == []


@pytest.mark.parametrize(
    ("target", "canonical", "display"),
    [
        ("docs/café.md", "docs/caf%C3%A9.md", "docs/café.md"),
        ("docs/a b.md", "docs/a%20b.md", "docs/a b.md"),
        ("docs/a%2Fb.md", "docs/a%2Fb.md", "docs/a%2Fb.md"),
        ("docs/a%23b.md", "docs/a%23b.md", "docs/a%23b.md"),
        ("docs/a%25b.md", "docs/a%25b.md", "docs/a%25b.md"),
        ("docs/%FF.md", "docs/%FF.md", "docs/%FF.md"),
        ("docs/%E9.md", "docs/%E9.md", "docs/%E9.md"),
        ("docs/a%00b.md", "docs/a%00b.md", "docs/a%00b.md"),
        ("docs/a%0Ab.md", "docs/a%0Ab.md", "docs/a%0Ab.md"),
        ("docs/a%0Db.md", "docs/a%0Db.md", "docs/a%0Db.md"),
        ("docs/a%7Fb.md", "docs/a%7Fb.md", "docs/a%7Fb.md"),
        ("docs/a%C2%85b.md", "docs/a%C2%85b.md", "docs/a%C2%85b.md"),
        ("docs/a%E2%80%AEb.md", "docs/a%E2%80%AEb.md", "docs/a%E2%80%AEb.md"),
        ("docs/a%E2%80%A8b.md", "docs/a%E2%80%A8b.md", "docs/a%E2%80%A8b.md"),
        ("docs/a%E2%80%A9b.md", "docs/a%E2%80%A9b.md", "docs/a%E2%80%A9b.md"),
        (
            "images/👨\u200d👩.png",
            "images/%F0%9F%91%A8%E2%80%8D%F0%9F%91%A9.png",
            "images/👨\u200d👩.png (URL: images/%F0%9F%91%A8%E2%80%8D%F0%9F%91%A9.png)",
        ),
        (
            "docs/می\u200cروم.md",
            "docs/%D9%85%DB%8C%E2%80%8C%D8%B1%D9%88%D9%85.md",
            "docs/می\u200cروم.md (URL: docs/%D9%85%DB%8C%E2%80%8C%D8%B1%D9%88%D9%85.md)",
        ),
        ("docs/a\u00adb.md", "docs/a%C2%ADb.md", "docs/a%C2%ADb.md"),
        ("docs/a\u200bb.md", "docs/a%E2%80%8Bb.md", "docs/a%E2%80%8Bb.md"),
        ("docs/a\u00a0b.md", "docs/a%C2%A0b.md", "docs/a%C2%A0b.md"),
        ("docs/a\u202fb.md", "docs/a%E2%80%AFb.md", "docs/a%E2%80%AFb.md"),
        ("docs/a\u3000b.md", "docs/a%E3%80%80b.md", "docs/a%E3%80%80b.md"),
        ("docs/a%ffb.md", "docs/a%FFb.md", "docs/a%FFb.md"),
        ("docs/%61.md", "docs/%61.md", "docs/a.md"),
        ("%20https://example.invalid", "%20https://example.invalid", " https://example.invalid"),
    ],
)
def test_markdown_and_html_share_urls_but_display_readable_paths(target: str, canonical: str, display: str) -> None:
    source = f'[x](<{target}>) <a href="{target}">x</a> ![x](<{canonical}>)'
    assert destinations(source) == [canonical, canonical, canonical]
    assert _relative_description_targets(source, "text/markdown") == [display]


@given(st.text(alphabet="abcéñ漢🙂\u200c\u200d", min_size=1, max_size=12))
def test_unicode_spellings_deduplicate_across_markdown_html_and_images(stem: str) -> None:
    target = f"docs/{stem}.md"
    canonical = quote(target, safe="/")
    source = f'[x]({target}) <a href="{target}">x</a> ![x]({canonical}) [outside](docs/outside.md)'
    assert destinations(source) == [canonical, canonical, canonical, "docs/outside.md"]
    expected = f"{target} (URL: {canonical})" if "\u200c" in stem or "\u200d" in stem else target
    assert _relative_description_targets(source, "text/markdown") == [expected, "docs/outside.md"]


def test_reserved_url_characters_remain_distinct_after_deduplication() -> None:
    targets = ["docs/a%2Fb.md", "docs/a/b.md", "docs/a%23b.md", "docs/a#b.md", "docs/a%25b.md", "docs/a%2525b.md"]
    source = " ".join(f'[x]({target}) <a href="{target}">x</a>' for target in targets)
    assert _relative_description_targets(source, "text/markdown") == targets


def test_escape_case_variants_deduplicate_by_display_destination() -> None:
    source = (
        '[x](docs/a%2fb.md) [y](docs/a%2Fb.md) <a href="docs/a%2fb.md">x</a> '
        "![x](docs/a%2Fb.md) [different](docs/a/b.md) [last](docs/last.md)"
    )
    assert _relative_description_targets(source, "text/markdown") == [
        "docs/a%2Fb.md",
        "docs/a/b.md",
        "docs/last.md",
    ]


def test_invalid_utf8_duplicates_do_not_consume_the_finding_cap() -> None:
    targets = ["docs/a%ffb.md", "docs/a%FFb.md", "docs/2.md", "docs/3.md", "docs/4.md", "docs/5.md", "docs/6.md"]
    source = " ".join(f'[x]({target}) <a href="{target}">x</a>' for target in targets)
    assert destinations(source)[:4] == ["docs/a%FFb.md"] * 4
    assert _relative_description_targets(source, "text/markdown") == ["docs/a%FFb.md", *targets[2:]]
    result = ReadmeRelativeLinks().test({"description": source, "description-content-type": "text/markdown"})
    assert result.outcome is False
    assert result.message is not None
    assert result.message.count("  docs/a%FFb.md\n") == 1
    assert "  docs/5.md\n  (and 1 more)" in result.message
    assert "docs/6.md" not in result.message


def test_unreserved_escape_spellings_still_deduplicate() -> None:
    source = '[x](docs/%61.md) <a href="docs/a.md">x</a> ![x](docs/%61.md) [last](docs/last.md)'
    assert _relative_description_targets(source, "text/markdown") == ["docs/a.md", "docs/last.md"]


def test_invalid_utf8_unreserved_spellings_share_one_finding_slot() -> None:
    targets = ["docs/%ff%61.md", "docs/%FFa.md", *[f"docs/{number}.md" for number in range(2, 7)]]
    source = " ".join(f'[x]({target}) <a href="{target}">x</a> ![x]({target})' for target in targets)
    assert _relative_description_targets(source, "text/markdown") == ["docs/%FFa.md", *targets[2:]]
    result = ReadmeRelativeLinks().test({"description": source, "description-content-type": "text/markdown"})
    assert result.message is not None
    assert result.message.count("  docs/%FFa.md\n") == 1
    assert "  docs/5.md\n  (and 1 more)" in result.message
    assert "docs/6.md" not in result.message


@pytest.mark.parametrize("escape", ["%2f", "%23", "%25", "%00", "%0a", "%E2%80%8D"])
def test_invalid_utf8_fallback_retains_reserved_and_invisible_escapes(escape: str) -> None:
    source = f"[x](docs/%ff%61{escape}.md)"
    assert _relative_description_targets(source, "text/markdown") == [f"docs/%FFa{escape.upper()}.md"]


@pytest.mark.parametrize("joiner", ["\u200c", "\u200d"])
def test_joiner_annotations_identify_paths_and_deduplicate_spellings(joiner: str) -> None:
    target = f"docs/a{joiner}b.md"
    encoded = quote(target, safe="/")
    alias = encoded.replace("/a", "/%61")
    source = f'[plain](docs/ab.md) [x]({target}) <a href="{alias}">x</a> ![x]({encoded})'
    expected = f"{target} (URL: {encoded})"
    assert _relative_description_targets(source, "text/markdown") == ["docs/ab.md", expected]
    result = ReadmeRelativeLinks().test({"description": source, "description-content-type": "text/markdown"})
    assert result.message is not None
    assert result.message.count(f"  {expected}\n") == 1


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('<code><pre></code><a href="docs/hidden.md">x</a></pre> [x](docs/real.md)', ["docs/real.md"]),
        ('<pre><code></pre><a href="docs/hidden.md">x</a></code>\n\n[x](docs/real.md)', ["docs/real.md"]),
        ('<pre></code><a href="docs/hidden.md">x</a></pre>\n\n[x](docs/real.md)', ["docs/real.md"]),
        ("<code>![</code>](images/hidden.png) [x](docs/hidden.md)</code> [x](docs/real.md)", ["docs/real.md"]),
        ("![<code>](images/real.png) [x](docs/real.md)", ["images/real.png", "docs/real.md"]),
        ('![<a href="docs/alt.md">x</a>](images/real.png) [x](docs/real.md)', ["images/real.png", "docs/real.md"]),
        ("<pre><code></pre>\n\n[ref]: docs/hidden.md\n\n</code>\n\n[ref] [x](docs/real.md)", ["docs/real.md"]),
    ],
)
def test_literal_regions_survive_misnested_tags_and_ignore_image_alt_html(source: str, expected: list[str]) -> None:
    assert destinations(source) == expected
