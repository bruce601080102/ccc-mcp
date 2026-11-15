"""Build the notes-cli demo repository in a throwaway directory.

Git commands here run ONLY inside the demo directory, which must not be inside the ccc-mcp
checkout. The user's repository is never touched.

  C1  TASK-0001  committed baseline
  C2  TASK-0002  committed tags feature
  --  TASK-0003  left UNCOMMITTED on purpose: staged, unstaged, untracked and deleted states,
                 plus a sensitive .env and a secret-looking module, so every exclusion and
                 classification path is exercised.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # ccc-mcp checkout root

FILES_TASK1 = {
    ".gitignore": "__pycache__/\n*.db\nnotes.json\n",
    "notes/__init__.py": "",
    "notes/models.py": (
        "from dataclasses import dataclass\n\n\n"
        '@dataclass\nclass Note:\n    title: str\n    body: str\n    created: str = ""\n'
    ),
    "notes/storage.py": (
        "import json\nfrom pathlib import Path\n\n\n"
        "class NotesStore:\n"
        '    """Notes kept in one JSON file."""\n\n'
        "    def __init__(self, path):\n        self.path = Path(path)\n\n"
        "    def load_json(self):\n"
        "        if not self.path.exists():\n            return []\n"
        '        return json.loads(self.path.read_text(encoding="utf-8"))\n\n'
        "    def save_json(self, notes):\n"
        '        self.path.write_text(json.dumps(notes, indent=2), encoding="utf-8")\n'
    ),
    "notes/search.py": (
        "def search_notes(notes, query):\n"
        "    needle = query.lower()\n"
        '    return [n for n in notes if needle in n["title"].lower() or needle in n["body"].lower()]\n'
    ),
    "notes/util.py": (
        "from datetime import datetime, timezone\n\n\n"
        'def now_iso():\n    return datetime.now(timezone.utc).isoformat(timespec="seconds")\n'
    ),
    "notes/cli.py": (
        "import argparse\n\n"
        "from .search import search_notes\n"
        "from .storage import NotesStore\n"
        "from .util import now_iso\n\n\n"
        "def cmd_add(store, title, body):\n"
        "    notes = store.load_json()\n"
        '    notes.append({"title": title, "body": body, "created": now_iso()})\n'
        "    store.save_json(notes)\n\n\n"
        "def cmd_list(store):\n    return store.load_json()\n\n\n"
        "def cmd_search(store, query):\n    return search_notes(store.load_json(), query)\n\n\n"
        'def main(argv=None, path="notes.json"):\n'
        '    parser = argparse.ArgumentParser(prog="notes")\n'
        '    sub = parser.add_subparsers(dest="command", required=True)\n'
        '    add = sub.add_parser("add")\n    add.add_argument("title")\n    add.add_argument("body")\n'
        '    sub.add_parser("list")\n'
        '    search = sub.add_parser("search")\n    search.add_argument("query")\n'
        "    args = parser.parse_args(argv)\n    store = NotesStore(path)\n"
        '    if args.command == "add":\n        cmd_add(store, args.title, args.body)\n'
        '    elif args.command == "list":\n        for note in cmd_list(store):\n            print(note["title"])\n'
        '    else:\n        for note in cmd_search(store, args.query):\n            print(note["title"])\n'
        "    return 0\n"
    ),
    "tests/test_search.py": (
        "from notes.search import search_notes\n\n\n"
        "def test_search_matches_title_and_body_case_insensitively():\n"
        '    notes = [{"title": "Shopping", "body": "milk"}, {"title": "Work", "body": "report"}]\n'
        '    assert [n["title"] for n in search_notes(notes, "MILK")] == ["Shopping"]\n'
    ),
}

FILES_TASK2 = {
    "notes/models.py": (
        "from dataclasses import dataclass, field\n\n\n"
        '@dataclass\nclass Note:\n    title: str\n    body: str\n    created: str = ""\n'
        "    tags: list = field(default_factory=list)\n"
    ),
    "notes/search.py": (
        "def search_notes(notes, query, tag=None):\n"
        "    needle = query.lower()\n"
        '    hits = [n for n in notes if needle in n["title"].lower() or needle in n["body"].lower()]\n'
        '    if tag is not None:\n        hits = [n for n in hits if tag in n.get("tags", [])]\n'
        "    return hits\n"
    ),
    "notes/tags.py": (
        "def normalize_tags(raw):\n"
        '    """Lower-case, strip, de-duplicate, keep order."""\n'
        "    seen = []\n"
        "    for item in raw:\n"
        "        tag = item.strip().lower()\n"
        "        if tag and tag not in seen:\n            seen.append(tag)\n"
        "    return seen\n"
    ),
    "notes/cli.py": (
        "import argparse\n\n"
        "from .search import search_notes\n"
        "from .storage import NotesStore\n"
        "from .tags import normalize_tags\n"
        "from .util import now_iso\n\n\n"
        "def cmd_add(store, title, body, tags=()):\n"
        "    notes = store.load_json()\n"
        '    notes.append({"title": title, "body": body, "created": now_iso(), "tags": normalize_tags(tags)})\n'
        "    store.save_json(notes)\n\n\n"
        "def cmd_list(store, tag=None):\n"
        "    notes = store.load_json()\n"
        "    if tag is None:\n        return notes\n"
        '    return [n for n in notes if tag in n.get("tags", [])]\n\n\n'
        "def cmd_search(store, query, tag=None):\n    return search_notes(store.load_json(), query, tag)\n\n\n"
        'def main(argv=None, path="notes.json"):\n'
        '    parser = argparse.ArgumentParser(prog="notes")\n'
        '    sub = parser.add_subparsers(dest="command", required=True)\n'
        '    add = sub.add_parser("add")\n    add.add_argument("title")\n    add.add_argument("body")\n'
        '    add.add_argument("--tag", action="append", default=[])\n'
        '    listing = sub.add_parser("list")\n    listing.add_argument("--tag")\n'
        '    search = sub.add_parser("search")\n    search.add_argument("query")\n    search.add_argument("--tag")\n'
        "    args = parser.parse_args(argv)\n    store = NotesStore(path)\n"
        '    if args.command == "add":\n        cmd_add(store, args.title, args.body, args.tag)\n'
        '    elif args.command == "list":\n        for note in cmd_list(store, args.tag):\n            print(note["title"])\n'
        '    else:\n        for note in cmd_search(store, args.query, args.tag):\n            print(note["title"])\n'
        "    return 0\n"
    ),
    "tests/test_tags.py": (
        "from notes.tags import normalize_tags\n\n\n"
        "def test_normalize_tags_strips_lowercases_and_deduplicates():\n"
        '    assert normalize_tags([" Work ", "work", "home", ""]) == ["work", "home"]\n'
    ),
}

FILES_TASK3 = {
    "notes/migrate.py": (
        "import json\nfrom pathlib import Path\n\n"
        "from .sqlite_store import SqliteStore\n\n\n"
        "class JsonImporter:\n"
        '    """One-time import of a legacy JSON notes file."""\n\n'
        "    def __init__(self, path):\n        self.path = Path(path)\n\n"
        "    def read(self):\n"
        "        if not self.path.exists():\n            return []\n"
        '        records = json.loads(self.path.read_text(encoding="utf-8"))\n'
        "        if not isinstance(records, list):\n"
        '            raise ValueError("expected a JSON list of notes")\n'
        "        return records\n\n\n"
        "def import_into_sqlite(json_path, db_path):\n"
        "    records = JsonImporter(json_path).read()\n"
        "    store = SqliteStore(db_path)\n"
        "    store.save(records)\n"
        "    return len(records)\n"
    ),
    "notes/sqlite_store.py": (
        "import json\nimport sqlite3\n\n\n"
        "class SqliteStore:\n"
        '    """Notes kept in one SQLite table."""\n\n'
        "    def __init__(self, path):\n"
        "        self.conn = sqlite3.connect(str(path))\n"
        '        self.conn.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, doc TEXT NOT NULL)")\n\n'
        "    def save(self, notes):\n"
        '        self.conn.executemany("INSERT INTO notes (doc) VALUES (?)", [(json.dumps(n),) for n in notes])\n'
        "        self.conn.commit()\n\n"
        "    def load(self):\n"
        '        rows = self.conn.execute("SELECT doc FROM notes ORDER BY id").fetchall()\n'
        "        return [json.loads(row[0]) for row in rows]\n"
    ),
    "notes/cli.py": (
        "import argparse\n\n"
        "from .search import search_notes\n"
        "from .sqlite_store import SqliteStore\n"
        "from .tags import normalize_tags\n"
        "from .util import now_iso\n\n\n"
        "def cmd_add(store, title, body, tags=()):\n"
        '    store.save([{"title": title, "body": body, "created": now_iso(), "tags": normalize_tags(tags)}])\n\n\n'
        "def cmd_list(store, tag=None):\n"
        "    notes = store.load()\n"
        "    if tag is None:\n        return notes\n"
        '    return [n for n in notes if tag in n.get("tags", [])]\n\n\n'
        "def cmd_search(store, query, tag=None):\n    return search_notes(store.load(), query, tag)\n\n\n"
        'def main(argv=None, path="notes.db"):\n'
        '    parser = argparse.ArgumentParser(prog="notes")\n'
        '    sub = parser.add_subparsers(dest="command", required=True)\n'
        '    add = sub.add_parser("add")\n    add.add_argument("title")\n    add.add_argument("body")\n'
        '    add.add_argument("--tag", action="append", default=[])\n'
        '    listing = sub.add_parser("list")\n    listing.add_argument("--tag")\n'
        '    search = sub.add_parser("search")\n    search.add_argument("query")\n    search.add_argument("--tag")\n'
        "    args = parser.parse_args(argv)\n    store = SqliteStore(path)\n"
        '    if args.command == "add":\n        cmd_add(store, args.title, args.body, args.tag)\n'
        '    elif args.command == "list":\n        for note in cmd_list(store, args.tag):\n            print(note["title"])\n'
        '    else:\n        for note in cmd_search(store, args.query, args.tag):\n            print(note["title"])\n'
        "    return 0\n"
    ),
    "tests/test_migrate.py": (
        "import json\n\n"
        "from notes.migrate import JsonImporter, import_into_sqlite\n"
        "from notes.sqlite_store import SqliteStore\n\n\n"
        "def test_json_importer_reads_list(tmp_path):\n"
        '    path = tmp_path / "legacy.json"\n'
        '    path.write_text(json.dumps([{"title": "a", "body": "b"}]), encoding="utf-8")\n'
        '    assert JsonImporter(path).read() == [{"title": "a", "body": "b"}]\n\n\n'
        "def test_import_into_sqlite_roundtrip(tmp_path):\n"
        '    legacy = tmp_path / "legacy.json"\n'
        '    legacy.write_text(json.dumps([{"title": "a", "body": "b"}]), encoding="utf-8")\n'
        '    assert import_into_sqlite(legacy, tmp_path / "notes.db") == 1\n'
        '    assert SqliteStore(tmp_path / "notes.db").load() == [{"title": "a", "body": "b"}]\n'
    ),
    "notes/schema.sql": "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, doc TEXT NOT NULL);\n",
    "notes/local_settings.py": '# local, never committed\nAPI_KEY = "sk-demo-0000000000000000"\n',
    ".env": "API_TOKEN=demo-env-value-0001\n",
}


def _git(repo, *args, check=True):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, env=env, check=False
    )
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.decode()[:300]}")
    return result.stdout.decode().strip()


def _write(repo, files):
    for relative, text in files.items():
        target = Path(repo) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def build(destination):
    repo = Path(destination).resolve()
    if repo == ROOT or ROOT in repo.parents:
        raise SystemExit("refusing to build the demo inside the ccc-mcp checkout")
    repo.mkdir(parents=True, exist_ok=False)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "archgraph-demo")
    _git(repo, "config", "user.email", "archgraph-demo@example.invalid")

    _write(repo, FILES_TASK1)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "TASK-0001 notes-cli baseline")
    c1 = _git(repo, "rev-parse", "HEAD")

    _write(repo, FILES_TASK2)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "TASK-0002 tags")
    c2 = _git(repo, "rev-parse", "HEAD")

    # TASK-0003: deliberately left uncommitted.
    os.remove(repo / "notes/storage.py")  # unstaged deletion
    _write(repo, {k: v for k, v in FILES_TASK3.items() if k != "notes/sqlite_store.py"})
    _write(repo, {"notes/sqlite_store.py": FILES_TASK3["notes/sqlite_store.py"]})
    _git(repo, "add", "notes/sqlite_store.py")  # staged addition
    _write(repo, {"notes/cli.py": FILES_TASK3["notes/cli.py"]})
    _git(repo, "add", "notes/cli.py")  # staged modification
    with open(repo / "notes/cli.py", "a", encoding="utf-8") as handle:
        handle.write("# edited after staging: the staged and working copies now differ\n")
    _write(
        repo,
        {
            "notes/models.py": (
                "from dataclasses import dataclass, field\n\n\n"
                '@dataclass\nclass Note:\n    title: str\n    body: str\n    created: str = ""\n'
                "    tags: list = field(default_factory=list)\n    id: int | None = None\n"
            )
        },
    )  # unstaged, not in the plan
    _write(
        repo,
        {
            "notes/migrate.py": FILES_TASK3["notes/migrate.py"],
            "tests/test_migrate.py": FILES_TASK3["tests/test_migrate.py"],
            "notes/schema.sql": FILES_TASK3["notes/schema.sql"],
            "notes/local_settings.py": FILES_TASK3["notes/local_settings.py"],
            ".env": FILES_TASK3[".env"],
        },
    )  # untracked

    refs = {"C1": c1, "C2": c2, "repo": str(repo)}
    (repo.parent / f"{repo.name}.refs.json").write_text(
        json.dumps(refs, indent=2), encoding="utf-8"
    )
    return refs


if __name__ == "__main__":
    print(json.dumps(build(sys.argv[1]), indent=2))
