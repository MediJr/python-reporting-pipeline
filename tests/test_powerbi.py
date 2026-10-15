"""Power BI deliverables: the committed project is the generator's output, and its content is consistent."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from conftest import ROOT

PROJECT = ROOT / "powerbi" / "project"


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _script("build_powerbi_project")


def _tree(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_committed_project_is_exactly_the_generator_output(generator, tmp_path):
    generator.write(tmp_path / "project", generator.PLACEHOLDER)
    assert _tree(tmp_path / "project") == _tree(PROJECT)
    assert (ROOT / "powerbi/measures.dax").read_text(encoding="utf-8") == generator.measures_dax()


def test_model_tables_measures_and_relationships(generator):
    model = PROJECT / f"{generator.NAME}.SemanticModel" / "definition"
    sales = (model / "tables" / "Sales.tmdl").read_text(encoding="utf-8")
    for name, *_ in generator.MEASURES:
        assert f"measure '{name}' = " in sales
    relationships = (model / "relationships.tmdl").read_text(encoding="utf-8")
    assert "fromColumn: Sales.date_key" in relationships and "toColumn: Product.product_id" in relationships
    calendar = (model / "tables" / "Calendar.tmdl").read_text(encoding="utf-8")
    assert "dataCategory: Time" in calendar and "\t\tisKey" in calendar
    # every table reads its CSV through the DataFolder parameter, as UTF-8, with en-US number parsing
    for table, (csv_file, columns) in generator.TABLES.items():
        text = (model / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
        assert f'DataFolder & "\\{csv_file}"' in text and "Encoding = 65001" in text and '"en-US"' in text
        for column, *_ in columns:
            assert f"\tcolumn {column}\n" in text


def test_model_columns_match_the_exported_files(generator, project):
    from reporting_pipeline.config import load_config
    from reporting_pipeline.pipeline import run_pipeline

    run_pipeline(load_config(project / "config/pipeline.toml"))
    for table, (csv_file, columns) in generator.TABLES.items():
        header = (project / "output/powerbi" / csv_file).read_text(encoding="utf-8").splitlines()[0].split(",")
        assert header == [c for c, *_ in columns], table


def test_report_pages_reference_existing_fields(generator):
    known = {f"{t}.{c}" for t, (_, cols) in generator.TABLES.items() for c, *_ in cols}
    known |= {f"Sales.{m[0]}" for m in generator.MEASURES}
    report = PROJECT / f"{generator.NAME}.Report" / "definition"
    pages = json.loads((report / "pages/pages.json").read_text(encoding="utf-8"))["pageOrder"]
    assert pages == ["overview", "trends", "quality"]
    refs = []
    for visual in (report / "pages").rglob("visual.json"):
        data = json.loads(visual.read_text(encoding="utf-8"))
        for state in data["visual"].get("query", {}).get("queryState", {}).values():
            refs += [p["queryRef"] for p in state["projections"]]
    assert refs and set(refs) <= known


def test_no_personal_or_absolute_path_is_committed(generator):
    for path, data in _tree(ROOT / "powerbi").items():
        text = data.decode("utf-8", errors="ignore")
        assert "Users\\" not in text and "Users/" not in text, path
        assert "D:\\Data" not in text and "D:/Data" not in text, path
    expressions = (PROJECT / f"{generator.NAME}.SemanticModel/definition/expressions.tmdl").read_text(encoding="utf-8")
    assert generator.PLACEHOLDER in expressions


def test_comparison_script_detects_differences(project):
    from reporting_pipeline.config import load_config
    from reporting_pipeline.pipeline import run_pipeline

    run_pipeline(load_config(project / "config/pipeline.toml"))
    compare = _script("compare_powerbi_results").compare
    folder = project / "output/powerbi"
    dax = json.loads((ROOT / "powerbi/validation/dax_results.json").read_text(encoding="utf-8-sig"))
    checked, problems = compare(dax, folder)
    assert problems == [] and checked > 150
    dax["totals"][0]["[net_sales]"] += 0.02
    dax["monthly"][3]["[orders]"] += 1
    _, problems = compare(dax, folder)
    assert len(problems) == 2 and any("net_sales" in p for p in problems)


def test_local_project_points_at_this_clone_exports(generator, tmp_path, monkeypatch, capsys):
    """--local writes a project that opens without editing the DataFolder parameter."""
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    (tmp_path / "powerbi").mkdir()
    monkeypatch.setattr("sys.argv", ["build_powerbi_project.py", "--local"])
    assert generator.main() == 1  # no exports yet: refused with a hint
    assert "run 'reporting-pipeline run" in capsys.readouterr().out
    exports = tmp_path / "output" / "powerbi"
    exports.mkdir(parents=True)
    (exports / "fact_sales.csv").write_text("x\n", encoding="utf-8")
    assert generator.main() == 0
    model = tmp_path / "output/powerbi-project" / f"{generator.NAME}.SemanticModel/definition/expressions.tmdl"
    assert f'expression DataFolder = "{exports}"' in model.read_text(encoding="utf-8")
    assert generator.PLACEHOLDER not in model.read_text(encoding="utf-8")
