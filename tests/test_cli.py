from typer.testing import CliRunner

from app.cli import app


def test_cli_exposes_zendrop_search_subcommand():
    runner = CliRunner()

    result = runner.invoke(app, ["zendrop-search", "--help"])

    assert result.exit_code == 0
    assert "Keyword to search in Zendrop catalog" in result.output
