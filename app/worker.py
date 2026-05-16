from __future__ import annotations

import asyncio

import typer

from app.config import load_settings
from app.services.worker import run_worker


app = typer.Typer(no_args_is_help=False)


@app.callback(invoke_without_command=True)
def main(
    once: bool = typer.Option(False, help="Process one queued job and exit."),
    poll_seconds: float = typer.Option(2.0, min=0.2, help="Polling interval when no job is queued."),
) -> None:
    asyncio.run(run_worker(settings=load_settings(), once=once, poll_seconds=poll_seconds))


if __name__ == "__main__":
    app()
