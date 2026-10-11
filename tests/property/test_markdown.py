"""Grammar-generated destinations and transformations with known outcomes."""

import html
from dataclasses import dataclass
from urllib.parse import quote

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from pyroma._markdown import destinations
from pyroma.ratings import _relative_description_targets

from .strategies import WORDS, markdown_targets, property_settings


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


@property_settings
@given(fragments())
@example(Fragment('[docs](docs/a(b).md "Title")', "docs/a(b).md"))
@example(Fragment(r"[docs](docs/a\(b\)&#46;md)", "docs/a(b).md"))
@example(Fragment('<img src="images/a.png?a=1&amp;b=2"/>', "images/a.png?a=1&b=2"))
def test_generated_fragment_has_known_destination(fragment: Fragment) -> None:
    assert destinations(fragment.source) == [fragment.target]


@property_settings
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
@property_settings
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


@property_settings
@given(target=markdown_targets(), first=WORDS, second=WORDS, gap=st.sampled_from((" ", "  ", "\t")))
def test_reference_label_case_and_whitespace_are_normalized(target: str, first: str, second: str, gap: str) -> None:
    source = f"[docs][{first.upper()}{gap}{second.upper()}]\n\n[{first} {second}]: <{html.escape(target)}>"
    assert destinations(source) == [target]


@property_settings
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
        ("%20https://example.invalid", "%20https://example.invalid", " https://example.invalid"),
    ],
)
def test_markdown_and_html_share_urls_but_display_readable_paths(target: str, canonical: str, display: str) -> None:
    source = f'[x](<{target}>) <a href="{target}">x</a> ![x](<{canonical}>)'
    assert destinations(source) == [canonical, canonical, canonical]
    assert _relative_description_targets(source, "text/markdown") == [display]


@property_settings
@given(st.text(alphabet="abcéñ漢🙂", min_size=1, max_size=12))
def test_unicode_spellings_deduplicate_across_markdown_html_and_images(stem: str) -> None:
    target = f"docs/{stem}.md"
    canonical = quote(target, safe="/")
    source = f'[x]({target}) <a href="{target}">x</a> ![x]({canonical}) [outside](docs/outside.md)'
    assert destinations(source) == [canonical, canonical, canonical, "docs/outside.md"]
    assert _relative_description_targets(source, "text/markdown") == [target, "docs/outside.md"]


def test_reserved_url_characters_remain_distinct_after_deduplication() -> None:
    targets = ["docs/a%2Fb.md", "docs/a/b.md", "docs/a%23b.md", "docs/a#b.md", "docs/a%25b.md", "docs/a%2525b.md"]
    source = " ".join(f'[x]({target}) <a href="{target}">x</a>' for target in targets)
    assert _relative_description_targets(source, "text/markdown") == targets


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
