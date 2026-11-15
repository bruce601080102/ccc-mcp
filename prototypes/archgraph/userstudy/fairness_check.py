"""Fairness checks for the three conditions and the three cases.

1. Findability: for every required fact, where can an ordinary reader see it?
     level 1  first screen of the condition (A: header, alerts, novice tab, legend; B: text;
              C: the static diagram page)
     level 2  one deliberate step away (A: another tab; C: the shared fact sheet file)
     level 3  only after clicking a node (A: detail panel, present in the page data only)
     absent   not in the condition at all (a failure)
   Critical facts (blockers, out-of-plan files, dependents, pending action) must be level 2 or
   better in every condition.

2. Domain-word substitution: after replacing the domain words of each case with one placeholder
   and removing commit hashes, the three cases must be identical. Otherwise the cases differ in
   more than their surface words.

3. Contamination: a case must not contain the domain words of another case.

Learning effect is NOT removed by these checks. Identical structure lets a participant who has
seen one case answer the next faster. Counterbalancing (protocol section 6) and an order analysis
in the pilot are what control it; this script only proves the cases are structurally identical.
"""

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
MATERIALS = HERE / "materials"
CASES = ["case1_notes", "case2_bookmarks", "case3_todos"]
DOMAIN_WORDS = {
    "case1_notes": ["NotesStore", "Notes", "notes", "Note", "note", "筆記"],
    "case2_bookmarks": ["BookmarkStore", "Bookmarks", "bookmarks", "Bookmark", "bookmark", "書籤"],
    "case3_todos": ["TodoStore", "Todos", "todos", "Todo", "todo", "待辦"],
}
CRITICAL_PREFIXES = ("E1 ", "O1 ", "I1 ", "pending endpoint")
SECTION_LEVEL = {"header": 1, "tab-novice": 1, "legend": 1, "alerts": 1, "tab-planned": 2, "tab-engineer": 2}


class SectionText(HTMLParser):
    """Collects visible text per first-screen section, and payload text separately."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.text = {}
        self.payload = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script":
            self._in_script = attributes.get("type") == "application/json"
        if tag in ("header", "section", "div") and attributes.get("class") == "alerts":
            self.stack.append("alerts")
        elif tag == "header":
            self.stack.append("header")
        elif tag == "section" and attributes.get("class") == "legend":
            self.stack.append("legend")
        elif tag == "section" and attributes.get("id"):
            self.stack.append(attributes["id"])
        else:
            self.stack.append(None)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self._in_script:
            self.payload.append(data)
            return
        section = next((s for s in reversed(self.stack) if s), "other")
        self.text[section] = self.text.get(section, "") + data


def parse_html(path):
    parser = SectionText()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def needles_for(case_dir):
    sys.path.insert(0, str(HERE))
    import parity_check

    items = parity_check.required_facts(case_dir)
    return {label: needles for label, needles in items.items()}


def a_level(case_dir, needle):
    """Level of a needle in the A condition (the per-Task architecture pages)."""
    best = None
    for path in sorted((case_dir / "A").glob("*.html")):
        parser = parse_html(path)
        levels = [SECTION_LEVEL[s] for s, text in parser.text.items() if needle in text and s in SECTION_LEVEL]
        if any(text for s, text in parser.text.items() if s not in SECTION_LEVEL and needle in text):
            levels.append(2)
        if needle in "\n".join(parser.payload):
            levels.append(3)
        if levels:
            level = min(levels)
            best = level if best is None else min(best, level)
    return best


def c_level(case_dir, needle):
    """Level 1 on the page of the Task the needle belongs to (the final Task is the default
    reading for every question); level 2 on another Task's page."""
    pages = sorted((case_dir / "C").glob("*.html"))
    own = [p for p in pages if p.name == "TASK-0003.html"]
    if any(needle in " ".join(parse_html(p).text.values()) for p in own):
        return 1
    if any(needle in " ".join(parse_html(p).text.values()) for p in pages):
        return 2
    return None


def b_level(case_dir, needle):
    return 1 if needle in (case_dir / "B_text.md").read_text(encoding="utf-8") else None


def findability(case):
    case_dir = MATERIALS / case
    problems = []
    distribution = {"A": {}, "B": {}, "C": {}}
    for label, needles in needles_for(case_dir).items():
        critical = label.startswith(CRITICAL_PREFIXES)
        for condition, levels_fn, key in (
            ("A", a_level, "A"),
            ("B", b_level, "B"),
            ("C", c_level, "C"),
        ):
            level = levels_fn(case_dir, needles[key])
            bucket = "absent" if level is None else str(level)
            distribution[condition][bucket] = distribution[condition].get(bucket, 0) + 1
            if level is None:
                problems.append(f"{case} {condition} ABSENT: {label}")
            elif critical and level > 2:
                problems.append(f"{case} {condition} critical fact only at level {level}: {label}")
    return problems, distribution


def _normalize_string(text, case):
    for word in DOMAIN_WORDS[case]:
        text = text.replace(word, "〈D〉")
    return re.sub(r"\b[0-9a-f]{40}\b", "<sha>", text)


def _canonical(value, case):
    """Strings get the domain-word substitution; lists are sorted, because the sort order of
    file names depends on the domain words and must not count as a structural difference."""
    if isinstance(value, dict):
        return {_normalize_string(k, case): _canonical(v, case) for k, v in value.items()}
    if isinstance(value, list):
        items = [_canonical(v, case) for v in value]
        return sorted(items, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
    if isinstance(value, str):
        return _normalize_string(value, case)
    return value


def normalized(case):
    case_dir = MATERIALS / case
    parts = []
    for name in ("facts.json", "keys.json"):
        data = json.loads((case_dir / name).read_text(encoding="utf-8"))
        data.pop("case", None)  # the case identifier itself is the only expected difference
        parts.append(json.dumps(_canonical(data, case), ensure_ascii=False, sort_keys=True))
    parts.append(_normalize_string((case_dir / "B_text.md").read_text(encoding="utf-8"), case))
    return "\n".join(parts)


def contamination(case):
    case_dir = MATERIALS / case
    body = "".join(
        p.read_text(encoding="utf-8") for p in case_dir.rglob("*") if p.is_file() and p.suffix in (".md", ".json", ".html")
    )
    foreign = [w for other, words in DOMAIN_WORDS.items() if other != case for w in words]
    # Plain "note"/"todo" substrings occur inside unrelated English words, so match whole tokens.
    return sorted({w for w in foreign if re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", body)})


def main():
    failures = []
    for case in CASES:
        problems, distribution = findability(case)
        print(f"== {case} findability levels: {distribution}")
        failures.extend(problems)
        leaks = contamination(case)
        print(f"   foreign domain words present: {leaks or 'none'}")
        failures.extend(f"{case} contains foreign domain words {leaks}" for _ in ([1] if leaks else []))
    signatures = {case: normalized(case) for case in CASES}
    same = len(set(signatures.values())) == 1
    print(f"\nstructure identical after domain-word substitution: {same}")
    if not same:
        failures.append("cases differ beyond their domain words")
    for problem in failures:
        print("FAIL:", problem)
    print("\nFAIRNESS CHECKS PASSED" if not failures else f"\n{len(failures)} FAILURE(S)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
