"""Static checks on the console's Role Studio code.

The real behaviour was exercised in a browser (see the P4 report); these tests only guard what
can be checked without one: that the scripts parse, that user text is never put into the page
unescaped, and that every string the new screens show has a Traditional Chinese translation.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[1] / "src/ccc_mcp/interfaces/webui_static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
I18N = (STATIC / "i18n.js").read_text(encoding="utf-8")


def studio_source():
    start = APP.index("// ---- Role Studio")
    end = APP.index("  function route() {")
    drift = APP.index("const driftCard")
    return APP[start:end] + APP[drift:drift + 1400]


def zh_keys():
    start = I18N.index("const zh = {")
    block = I18N[start:I18N.index("\n  };\n", start)]
    return {m.group(1) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"\s*:\s*"', block)}


def translate_arguments(source):
    """Every quoted string inside a UI.translate( ... ) call, including ternary branches."""
    found = []
    for match in re.finditer(r"UI\.translate\(", source):
        depth, i = 1, match.end()
        while depth and i < len(source):
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            i += 1
        argument = re.sub(r'(?:===|!==)\s*"[^"]*"', "", source[match.end():i - 1])
        found += re.findall(r'"((?:[^"\\]|\\.)*)"', argument)
    return found


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
@pytest.mark.parametrize("name", ["app.js", "i18n.js"])
def test_the_console_scripts_parse(name):
    done = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_every_string_the_new_screens_translate_has_a_traditional_chinese_entry():
    keys = zh_keys()
    strings = {s for s in translate_arguments(studio_source()) if re.search(r"[A-Za-z]{3}", s)}
    sections = re.findall(
        r'(?:goal|duties|constraints|io_format|collaboration|failure_handling): \["([^"]+)", "([^"]+)"\]',
        studio_source())
    strings |= {text for pair in sections for text in pair}
    strings |= {text for pair in re.findall(r'(?:operator_editable|fixed_contract|dynamic|provider_wrapper): \["([^"]+)"', studio_source()) for text in [pair]}
    missing = sorted(s for s in strings if s not in keys)
    assert not missing, missing


def test_user_text_is_only_put_into_the_page_escaped_or_as_a_value():
    # the escaper covers the single quote too, which attribute values in single quotes need
    assert '.replace(/\'/g, "&#39;")' in APP
    source = studio_source()
    # big free text goes in through .value / .textContent, never through markup
    assert 'document.getElementById("rs-free").value = draft.freeform' in source
    assert 'pre.textContent = segment ? segment.text : ""' in source
    assert "all.textContent = p.prompt_text" in source
    # every ${...} that holds a role's own text is wrapped in esc(), checked on the fields a
    # person controls
    for field in ("role.name", "definition.name", "finding.message", "finding.why",
                  "finding.suggestion", "finding.evidence", "w.name", "e.actor"):
        for hit in re.finditer(r"\$\{([^}]*" + re.escape(field) + r"[^}]*)\}", source):
            expression = hit.group(1)
            if source[max(0, hit.start() - 8):hit.start()] == "value: `":
                continue          # a modal field value: showModal escapes it
            assert "esc(" in expression or "UI.translate(" in expression, expression


def test_the_page_never_uses_inner_html_with_raw_response_fields():
    source = studio_source()
    assert "insertAdjacentHTML" not in source and "document.write" not in source
    assert "eval(" not in source and "new Function" not in source


def test_the_sections_the_page_offers_are_the_ones_the_backend_accepts():
    from ccc_mcp.core.custom_roles import SECTION_KEYS

    source = studio_source()
    block = source[source.index("const STUDIO_SECTIONS"):source.index("const STUDIO_LAYERS")]
    assert re.findall(r"^\s+(\w+): \[", block, flags=re.M) == list(SECTION_KEYS)


# -- importing roles (P5) ---------------------------------------------------------------------------


def test_the_import_review_puts_what_a_file_says_in_as_text_never_as_markup():
    source = studio_source()
    # the claims a bundle makes about itself and the role's whole prompt go in as textContent
    assert 'node.textContent = values[index] || "—"' in source
    assert 'prompt.textContent = promptText(r.role)' in source
    assert "const values = [r.source.source_project_id, r.source.source_status, r.source.bundle_sha256]" in source
    # ids and names that do reach markup are escaped
    for field in ("snap.snapshot_id", "snap.version", "w.name", "item.provider", "item.model"):
        for hit in re.finditer(r"\$\{([^}]*" + re.escape(field) + r"[^}]*)\}", source):
            assert "esc(" in hit.group(1) or "UI.translate(" in hit.group(1), hit.group(1)


def test_confirming_an_imported_role_goes_through_the_typed_phrase_dialog_only():
    source = studio_source()
    assert source.count("/confirm-import`") == 1 and source.count("/confirm-snapshot`") == 1
    for route in ("/confirm-import`", "/confirm-snapshot`"):
        before = source[:source.index(route)]
        assert before.rindex("confirmTextModal({") > before.rindex("function wire()")
    # the phrase comes from the server and is compared by the server again
    assert "challenge: form.phrase" in source and "challenge: snap.phrase" in source
    # nothing on the page confirms by itself: no automatic call, no default-checked older snapshot
    assert 'data-review-snapshot="${esc(snap.snapshot_id)}" />' in source
    assert 'data-review-snapshot="${esc(snap.snapshot_id)}" checked' not in source


def test_an_imported_role_waiting_for_confirmation_has_no_edit_tab():
    source = studio_source()
    assert 'state.review ? ["review", "preview", "history"]' in source
