"""README.md (English) and README.ja.md (Japanese) stay the same document in two languages.

The pair is checked beyond headings, technical tokens, code examples and links: the prose itself is watched too.
- Every number written in the prose must appear in both languages (English number words count as numbers),
  so changing one figure in one language fails.
- Status claims are checked as English/Japanese pairs: a claim must be reworded or dropped in both languages
  or in neither, so a one-language rewording fails while a matching two-language rewording is allowed.
- Paragraph, bullet, inline-code and link counts must match per section, so added or dropped prose fails.
- Images (![...](...)) are extracted and compared like links, so an image URL changed in one language fails.
"""
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
EN, JA = HERE / "README.md", HERE / "README.ja.md"


def text_of(path):
    return path.read_text(encoding="utf-8")


def headings(text):
    """(level, text) for every heading line, in document order: the hash level alone cannot show a wrong
    heading text or a wrong heading order."""
    return [(len(m.group(1)), (m.group(2) or "").strip()) for m in HEAD.finditer(text)]


def code_blocks(text):
    blocks, cur = [], []
    for line in text.splitlines():
        if line.startswith("    ") and line[4:].strip():
            cur.append(re.sub(r"\s{2,}#.*$", "", line[4:]).rstrip())
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


LINK = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")
HEAD = re.compile(r"(?m)^(#{1,6})([ \t].*)?$")


def links(text):
    return [m.group(2) for m in LINK.finditer(text)]


def images(text):
    return [m.group(2) for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", text)]


def prose(text):
    """Lines that are not indented code blocks."""
    return [ln for ln in text.splitlines() if not ln.startswith(("    ", "\t"))]


WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
         "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def numbers(text):
    """Every number in the prose, in order; English number words count as their digit."""
    out = []
    for ln in prose(text):
        for m in re.finditer(r"\b\d[\d.]*\b|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b",
                             ln, re.I | re.ASCII):
            tok = m.group(0)
            out.append(str(WORDS[tok.lower()]) if tok.lower() in WORDS else tok)
    return out


def structure(section):
    body = [ln for ln in section.splitlines() if not ln.startswith(("    ", "\t"))]
    bullets = [ln for ln in body if ln.startswith("- ")]
    paragraphs, cur = [], []
    for ln in body:
        if not ln.strip() or ln.startswith(("#", "- ")):
            if cur:
                paragraphs.append(cur)
                cur = []
        else:
            cur.append(ln)
    if cur:
        paragraphs.append(cur)
    return {
        "bullets": len(bullets),
        "paragraphs": len(paragraphs),
        "paragraph_lines": sum(len(p) for p in paragraphs),
        "inline_code": len(re.findall(r"`[^`]+`", "".join(body))),
        "links": len(LINK.findall(section)),
        "bold": len(re.findall(r"\*\*[^*]+\*\*", section)),
    }


def heading_lines(text):
    return [m.group(0) for m in HEAD.finditer(text)]


def slug_of(heading_text):
    """The in-page anchor a heading gets (GitHub-style: lowercased, punctuation dropped, each space becomes
    one hyphen, so a heading with two spaces gets two hyphens and a heading with a bracket keeps the rest)."""
    s = re.sub(r"[^\w\- ]+", "", heading_text.lower(), flags=re.U).strip()
    return s.replace(" ", "-").replace("\t", "-")


def heading_slugs(text):
    """Every heading anchor of the document, numbered the way GitHub numbers repeated headings
    (#license, #license-1, #license-2...), in document order."""
    seen, out = Counter(), []
    for base in (slug_of(ln.lstrip("#").strip()) for ln in heading_lines(text)):
        out.append(base if seen[base] == 0 else f"{base}-{seen[base]}")
        seen[base] += 1
    return out


def broken_links(text, here):
    """File links must exist inside this repository (a relative link that walks out of the repo root is not a
    link of this repo, even when the file it names happens to exist); in-page anchors must name a real heading."""
    slugs, root, bad = set(heading_slugs(text)), here.resolve(), []
    for target in links(text):
        file_part, _, frag = target.partition("#")
        if target.startswith("#"):
            if frag not in slugs:
                bad.append(f"broken anchor: {target}")
        elif target.startswith(("http://", "https://", "mailto:")):
            continue
        else:
            path = (here / file_part).resolve()
            if path != root and root not in path.parents:
                bad.append(f"link leaves the repository: {target}")
            elif not path.exists():
                bad.append(f"broken link: {target}")
            elif frag and frag not in slugs:
                bad.append(f"broken anchor: {target}")
    return bad



# The known good pairing of the two READMEs. Any heading, number or status claim changed in one language
# only must fail here, not just the looser structural checks.
EN_HEADINGS = ["# ai-ops-dashboard",
               "## What it reads (on each machine, through `remote_probe.py`)",
               "## Use", "## Organisation", "## Agent reports",
               "## Always-on (e.g. a DGX Spark class box or a home server)", "## License"]
JA_HEADINGS = ["# ai-ops-dashboard",
               "## 何を読むのか（各マシン上で、`remote_probe.py` 経由）",
               "## 使い方", "## 組織（Organisation）", "## Agent レポート",
               "## 常時運転（たとえば DGX Spark クラスのボックスやホームサーバー）", "## ライセンス"]


def known_headings(pinned):
    """The pinned heading lines as (level, text) pairs, the same shape headings() returns."""
    return [(len(ln) - len(ln.lstrip("#")), ln.lstrip("#").strip()) for ln in pinned]


# The known good pairing of the bullets under the title. A sentence added to (or dropped from) one language only
# must fail here: the README.md side is meant to stay exactly as published, with only the language switch added.
INTRO_BULLETS = [
    ("Python 3 standard library only, no install. Linux machines with systemd; the hub can be macOS or Linux.",
     "Python 3 の標準ライブラリだけ。インストール不要。マシン側は systemd を使う Linux、ハブは macOS でも Linux でも動きます。"),
    ("Read-only. It changes nothing on your machines and sends nothing anywhere; the page is a local file.",
     "読みとり専用。あなたのマシンは何も変更せず、どこへも送信しません。ページはローカルファイルです。"),
    ("Asks first. Before the first read on each machine it lists exactly what it will read and waits for `y`.\n"
     "The answer is remembered and asked again whenever the probe script or the ssh target changes.",
     "必ず確認してから読みとります。各マシンで初めて読む前に、何を読むのかを正確に列挙して `y` を待ちます。\n"
     "答えは記憶され、プローブスクリプトか ssh ターゲットが変わるたびに再び聞かれます。"),
]


def intro_bullets(text):
    """The bullets before the first section heading, each one with its continuation lines joined by newlines."""
    bullets, cur = [], None
    for ln in sections(text)[0].splitlines():
        if ln.startswith("- "):
            if cur is not None:
                bullets.append(cur)
            cur = [ln[2:].rstrip()]
        elif cur is not None and ln.startswith(("  ", "\t")):
            cur.append(ln.strip())
        elif cur is not None:
            bullets.append(cur)
            cur = None
    if cur is not None:
        bullets.append(cur)
    return ["\n".join(b) for b in bullets]

STATUS_CLAIMS = [
    ("Python 3 standard library only, no install", "Python 3 の標準ライブラリだけ。インストール不要"),
    ("Read-only. It changes nothing on your machines and sends nothing anywhere; the page is a local file.",
     "読みとり専用。あなたのマシンは何も変更せず、どこへも送信しません。ページはローカルファイルです。"),
    ("The only step that writes is the optional probe-key install", "書きこみを行うのは、オプションの probe-key インストール"),
    ("Asks first.", "必ず確認してから読みとります"),
    ("waits for `y`", "`y` を待ちます"),
    ("`.env` files are never read", "`.env` ファイルは決して読みません"),
    ("reads only the newest 30 files", "読むのは最新の 30 ファイルだけ"),
    ("guardians (read-only watchers)", "guardians（読みとり専用の見張り）"),
    ("GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later)",
     "GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later)"),
    ("Versions published before 2026-10-31 remain available under MIT",
     "2026-10-31 以前に公開されたバージョンは MIT でも利用可能です"),
]


def heading_levels(text):
    return [len(m.group(1)) for m in re.finditer(r"(?m)^(#{1,6})[ \t]?", text)]


def sections(text):
    return re.split(r"(?m)^## ", text)


TECH = re.compile(r"\b[a-z0-9_]+\.(?:py|json|html|sh|service|yaml|env)\b|"
                  r"--yes|--config|--port|--https|--every|--bind|--bg|authorized_keys|\.ai-ops|"
                  r"LaunchAgents|launchd_match|8795|127\.0\.0\.1|tailscale|AGPL-3\.0-or-later|MIT|"
                  r"hosts\.example\.json|Ollama|OpenAI|crontab")


def tech_tokens(section):
    return sorted(set(TECH.findall(section)))


def test_both_languages_are_published():
    assert EN.exists() and JA.exists()


def test_switch_line_is_the_first_line_under_the_title():
    assert text_of(EN).splitlines()[2] == "English | [日本語](README.ja.md)"
    assert text_of(JA).splitlines()[2] == "[English](README.md) | 日本語"


def test_headings_are_the_same_number_in_the_same_order():
    en, ja = headings(text_of(EN)), headings(text_of(JA))
    assert en == known_headings(EN_HEADINGS), f"the English README renamed, added or moved a heading: {en}"
    assert ja == known_headings(JA_HEADINGS), f"the Japanese README renamed, added or moved a heading: {ja}"
    assert heading_levels(text_of(EN)) == heading_levels(text_of(JA)), "the two languages use different heading levels"
    assert en[0] == (1, "ai-ops-dashboard") and sum(1 for lv, _ in en if lv == 2) >= 6


def test_sections_hold_the_same_technical_content_in_the_same_order():
    en, ja = sections(text_of(EN)), sections(text_of(JA))
    assert len(en) == len(ja), "the two languages must have the same number of sections"
    for i, (e, j) in enumerate(zip(en, ja)):
        te, tj = tech_tokens(e), tech_tokens(j)
        assert te == tj, f"section {i} is out of order or covers different content: {te} vs {tj}"


def test_the_commands_and_examples_are_the_same_in_both_languages():
    assert code_blocks(text_of(EN)) == code_blocks(text_of(JA))


def test_every_relative_link_points_at_a_file_of_this_repo():
    for path in (EN, JA):
        assert not broken_links(text_of(path), HERE), broken_links(text_of(path), HERE)


def test_an_in_page_anchor_is_not_mistaken_for_a_file_path(tmp_path):
    ok = "See [License](#license) and [what it reads](#what-it-reads).\n\n## What it reads\n\ntext\n\n## License\n\ntext\n"
    assert broken_links(ok, tmp_path) == []          # a valid anchor is not a "broken link" to a missing file
    bad = "See [Somewhere](#somewhere).\n\n## License\n\ntext\n"
    assert broken_links(bad, tmp_path) == ["broken anchor: #somewhere"]


def test_repeated_headings_get_the_anchor_numbers_github_gives_them(tmp_path):
    text = ("See [one](#license), [two](#license-1), [three](#license-2) and [four](#license-3).\n\n"
            "## License\n\ntext\n\n## License\n\ntext\n\n## License\n\ntext\n")
    assert heading_slugs(text) == ["license", "license-1", "license-2"]
    assert broken_links(text, tmp_path) == ["broken anchor: #license-3"]


def test_a_heading_full_of_punctuation_keeps_the_anchor_github_gives_it(tmp_path):
    pairs = [("## Always-on (e.g. a DGX Spark class box or a home server)",
              "always-on-eg-a-dgx-spark-class-box-or-a-home-server"),
             ("## 常時運転（たとえば DGX Spark クラスのボックスやホームサーバー）",
              "常時運転たとえば-dgx-spark-クラスのボックスやホームサーバー"),
             ("##   Two  Spaces  Here  ", "two--spaces--here"),
             ("## What it reads (on each machine, through `remote_probe.py`)",
              "what-it-reads-on-each-machine-through-remote_probepy")]
    for title, anchor in pairs:
        assert slug_of(title.lstrip("#").strip()) == anchor
        assert broken_links(f"See [x](#{anchor}).\n\n{title}\n\ntext\n", tmp_path) == [], \
            f"{title!r} does not resolve its own anchor"


def test_a_relative_link_must_stay_inside_the_repository(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "inner.md").write_text("text")
    (tmp_path / "outside.md").write_text("text")        # the file exists, but it is outside the repository
    text = "[ok](docs/inner.md) [out](../outside.md) [gone](docs/missing.md)\n\n## License\n\ntext\n"
    assert broken_links(text, repo) == ["link leaves the repository: ../outside.md", "broken link: docs/missing.md"]


def test_both_languages_link_to_the_same_things():
    en, ja = links(text_of(EN)), links(text_of(JA))
    assert "README.ja.md" in en, "README.md must link to the Japanese copy"
    assert "README.md" in ja, "README.ja.md must link to the English copy"
    # in-page anchors are language-specific (they name that language's headings): compare their number, not their text
    others = lambda ls: sorted(t for t in ls if t not in ("README.md", "README.ja.md") and not t.startswith("#"))
    assert others(en) == others(ja)
    assert sum(t.startswith("#") for t in en) == sum(t.startswith("#") for t in ja), \
        "an internal link exists in one language only"


def test_status_and_numbers_are_unchanged_in_both_languages():
    for path in (EN, JA):
        t = text_of(path)
        for token in ("AGPL-3.0-or-later", "2026-10-31", "8795", "127.0.0.1", "remote_probe.py"):
            assert token in t, f"{path.name}: {token} missing"


def test_every_number_in_the_prose_is_the_same_in_both_languages():
    en, ja = numbers(text_of(EN)), numbers(text_of(JA))
    assert en == ja, f"prose numbers differ between languages: {Counter(en)} vs {Counter(ja)}"


def test_the_headings_are_the_known_english_japanese_pair():
    assert heading_lines(text_of(EN)) == EN_HEADINGS
    assert heading_lines(text_of(JA)) == JA_HEADINGS


def test_the_intro_bullets_are_the_known_english_japanese_pair():
    en, ja = intro_bullets(text_of(EN)), intro_bullets(text_of(JA))
    assert en == [e for e, _ in INTRO_BULLETS], f"the English README intro changed: {en}"
    assert ja == [j for _, j in INTRO_BULLETS], f"the Japanese README intro changed: {ja}"
    assert len(en) == len(ja), "an intro bullet exists in one language only"


def test_each_section_has_the_same_prose_structure_in_both_languages():
    en, ja = sections(text_of(EN)), sections(text_of(JA))
    assert len(en) == len(ja)
    for i, (e, j) in enumerate(zip(en, ja)):
        se, sj = structure(e), structure(j)
        assert se == sj, f"section {i} changed shape in one language only: {se} vs {sj}"


def test_status_claims_are_present_in_both_languages():
    en, ja = text_of(EN), text_of(JA)
    for claim_en, claim_ja in STATUS_CLAIMS:
        # a claim may be reworded, but only in both languages at once: presence must stay paired
        assert (claim_en in en) == (claim_ja in ja), \
            f"status claim reworded or dropped in one language only: {claim_en!r} / {claim_ja!r}"


def test_images_are_the_same_in_both_languages():
    en, ja = images(text_of(EN)), images(text_of(JA))
    assert en == ja, f"an image was changed in one language only: {en} vs {ja}"
