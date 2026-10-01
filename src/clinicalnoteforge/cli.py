"""CLI errors are explicit; rejected generations and invalid artifacts return nonzero."""

import json
from pathlib import Path
from typing import Annotated

import typer

from .config import load_config
from .ingestion import SyntheaCSVAdapter
from .pipeline import build as build_dataset
from .reporting import report as create_report

app = typer.Typer(no_args_is_help=True, help="Grounded synthetic clinical-note datasets.")


def fail(exc: Exception) -> None:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(1) from exc


@app.command()
def inspect(input: Annotated[Path, typer.Option("--input", exists=True, file_okay=False)]):
    """Inspect schemas, references, counts, and optional-table warnings."""
    try:
        data = SyntheaCSVAdapter().load(input)
        typer.echo(
            json.dumps(
                {
                    "detected_csv_files": sorted(p.name for p in input.glob("*.csv")),
                    "supported_files": data.files,
                    "patient_count": len(data.patients),
                    "encounter_count": len(data.encounters),
                    "schema_warnings": data.warnings,
                },
                indent=2,
            )
        )
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command()
def build(
    input: Annotated[Path, typer.Option("--input", exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option("--output")],
    config: Annotated[Path | None, typer.Option("--config", exists=True)] = None,
    renderer: Annotated[str | None, typer.Option("--renderer")] = None,
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
):
    """Build a new artifact; default template renderer needs no network or credentials."""
    try:
        settings = load_config(config, renderer=renderer, seed=seed, max_records=limit, model=model)
        result = build_dataset(input, output, settings)
        typer.echo(json.dumps(result, indent=2))
        if result["rejected_records"]:
            raise typer.Exit(2)
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command()
def validate(directory: Annotated[Path, typer.Argument(exists=True, file_okay=False)]):
    """Reconstruct facts, spans and targets; check file integrity and patient leakage."""
    try:
        result = create_report(directory)
        typer.echo(json.dumps(result, indent=2))
        if not result["passed"]:
            raise typer.Exit(1)
    except (ValueError, OSError, TypeError) as exc:
        fail(exc)


@app.command()
def report(directory: Annotated[Path, typer.Argument(exists=True, file_okay=False)]):
    """Regenerate measured statistics and an escaped HTML sample report."""
    validate(directory)


if __name__ == "__main__":
    app()
