"""Index markdown notes using only the Python standard library."""
import json
import re
import os
import tempfile
from pathlib import Path
import urllib.parse
import note_store
import vault_files

ROOT = Path(__file__).resolve().parent
NOTES_DIR = ROOT / "notes"
GRAPH_PATH = ROOT / "viewer" / "graph-data.js"
EXCERPT_CHARS = 700
JSON_INDENT = 2
CAPTURE_HUB_TITLE = "Captures"
NOTES_MISSING = "Your notes folder is missing. Restore the notes folder beside server.py, then restart Jarvis."


def capture_hub(node_id):
    return {"id": node_id, "label": CAPTURE_HUB_TITLE, "group": "captures",
            "excerpt": "Saved captures without another related note.",
            "text": "Saved captures without another related note.", "capture_hub": True}


WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def normalize(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def read_notes(directory=None):
    notes, _, _ = vault_files.read_notes(NOTES_DIR if directory is None else directory)
    if (any(f"[[{CAPTURE_HUB_TITLE}]]" in n["text"] for n in notes)
            and not any(n["label"] == CAPTURE_HUB_TITLE for n in notes)):
        notes.append(capture_hub(len(notes)))
    return notes


def make_graph(notes):
    nodes = [{key: note[key] for key in ("id", "label", "group", "excerpt")} for note in notes]
    for node, note in zip(nodes, notes):
        if note.get('_path'):
            node['stable_id'] = note_store.stable_id(note['_path'])
        if isinstance(note.get("edited_at"), (float, int)):
            node["edited_at"] = note["edited_at"]
        if note.get("capture_hub"):
            node["capture_hub"] = True
    bodies = [" " + normalize(n["text"]) + " " for n in notes]
    titles = [" " + normalize(n["label"]) + " " for n in notes]
    wikilinks = [{normalize(link.split("|")[0].split("#")[0])
                  for link in WIKILINK.findall(n["text"])} - {""} for n in notes]
    # Resolve Obsidian folder paths/aliases only when the target is unambiguous.
    identities = {}
    for note in notes:
        names = [note['label'], *note.get('aliases', [])]
        if note.get('_path'):
            names.append(str(Path(note['_path']).with_suffix('')))
        for name in names:
            identities.setdefault(normalize(name), set()).add(note['id'])
    explicit = set()
    for note in notes:
        targets = [link.split('|')[0].split('#')[0] for link in WIKILINK.findall(note['text'])]
        targets += [urllib.parse.unquote(link.split('#')[0]) for link in vault_files.MARKDOWN_LINK.findall(note['text'])]
        for target in targets:
            name = str(Path(target).with_suffix('')) if target.casefold().endswith('.md') else target
            matches = identities.get(normalize(name), set())
            if len(matches) == 1:
                identifier = next(iter(matches))
                if identifier != note['id']:
                    explicit.add(tuple(sorted((note['id'], identifier))))
    links = []
    for left in range(len(notes)):
        for right in range(left + 1, len(notes)):
            if ((notes[left]['id'], notes[right]['id']) in explicit
                    or titles[left] in bodies[right] or titles[right] in bodies[left]
                    or wikilinks[left] & wikilinks[right]):
                links.append({"source": left, "target": right})
    return {"nodes": nodes, "links": links}


def graph_bytes(graph):
    return ("const GRAPH = " + json.dumps(graph, ensure_ascii=True, indent=JSON_INDENT) + ";\n").encode("utf-8")


def write_graph(graph, output=GRAPH_PATH):
    """Publish atomically so readers never see a partially written script."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(graph_bytes(graph))
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build(directory=None, output=None):
    notes = read_notes(directory)
    graph = make_graph(notes)
    write_graph(graph, GRAPH_PATH if output is None else output)
    return notes, graph


if __name__ == "__main__":
    try:
        _, graph = build()
    except FileNotFoundError:
        raise SystemExit(NOTES_MISSING) from None
    print(f"Indexed {len(graph['nodes'])} notes and {len(graph['links'])} links.")
