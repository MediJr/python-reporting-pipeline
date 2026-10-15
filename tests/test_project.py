"""Repository consistency: committed examples, packaging, documentation links, no secrets."""

from __future__ import annotations

import importlib.util
import json
import re
import tomllib

import pytest
from conftest import ROOT

import reporting_pipeline

PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md")), *sorted((ROOT / "powerbi").glob("*.md"))]
VOLATILE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}"  # run ids
    r"|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"  # timestamps
    r"|\b\d{8}T\d{6}Z_[0-9a-f]{8}\b"  # run folder names
)
DURATION = re.compile(r'("duration_s":\s*)[\d.]+|(\| )\d+\.\d{1,3}( \|)')


def _masked(text: str) -> str:
    text = VOLATILE.sub("<volatile>", text)
    return DURATION.sub(lambda m: (m.group(1) or m.group(2)) + "<s>" + (m.group(3) or ""), text)


def test_committed_examples_match_a_fresh_run(tmp_path):
    spec = importlib.util.spec_from_file_location("regen", ROOT / "scripts" / "regenerate_examples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.generate(tmp_path) == 0
    committed = ROOT / "reports" / "examples"
    fresh = {p.relative_to(tmp_path).as_posix(): p for p in tmp_path.rglob("*") if p.is_file()}
    assert sorted(fresh) == sorted(
        p.relative_to(committed).as_posix() for p in committed.rglob("*") if p.is_file() and p.name != "README.md"
    )
    for name, path in fresh.items():
        new, old = path.read_text(encoding="utf-8"), (committed / name).read_text(encoding="utf-8")
        if name == "run/run_report.json":
            new_json, old_json = json.loads(new), json.loads(old)
            for report in (new_json, old_json):
                for key in ("run_id", "started_at", "finished_at", "duration_s", "environment", "steps"):
                    report.pop(key)
            assert new_json == old_json
        elif name == "run/pipeline.log.jsonl":
            events = lambda text: [json.loads(line)["event"] for line in text.splitlines()]  # noqa: E731
            assert len(events(new)) == len(events(old))
        else:
            assert _masked(new) == _masked(old), name


def test_package_metadata():
    project = PYPROJECT["project"]
    assert project["version"] == reporting_pipeline.__version__
    assert project["dependencies"] == []  # the SQLite demo needs the standard library only
    assert project["scripts"] == {"reporting-pipeline": "reporting_pipeline.cli:main"}
    assert project["license"] == "MIT"
    assert "LUMU BAMANA MANASSE" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "2026" in (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_constraints_pin_every_dependency():
    pinned = {
        line.split("==")[0].lower()
        for line in (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines()
        if "==" in line
    }
    for spec in PYPROJECT["project"]["optional-dependencies"]["dev"]:
        assert re.split(r"[<>=\[]", spec)[0].lower() in pinned, spec


def _slug(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_relative_links_and_anchors_resolve(doc):
    text = re.sub(r"```.*?```", "", doc.read_text(encoding="utf-8"), flags=re.S)
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        dest = (doc.parent / path) if path else doc
        assert dest.exists(), f"{doc.name}: broken link {target}"
        if anchor and dest.suffix == ".md":
            headings = re.findall(r"^#{1,6} (.+)$", dest.read_text(encoding="utf-8"), flags=re.M)
            assert anchor in {_slug(h) for h in headings}, f"{doc.name}: missing anchor #{anchor}"


def test_no_secret_or_local_path_in_tracked_text_files():
    patterns = re.compile(
        r"password\s*=\s*\S+|ghp_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY|C:\\Users\\|D:\\Data", re.I
    )
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file() or rel.startswith(
            (".venv", ".git/", "output", "runs", ".pgdemo", ".pytest_cache", ".ruff_cache")
        ):
            continue
        if "__pycache__" in rel or ".egg-info/" in rel or path.suffix in {".png", ".pyc"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        found = patterns.findall(text)
        if rel.startswith("tests/"):  # tests contain fake credentials on purpose, to check their redaction
            found = [m for m in found if not m.lower().startswith("password")]
        assert not found, f"{rel}: {found}"
