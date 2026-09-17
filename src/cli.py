import os
from pathlib import Path
import subprocess
import sys

# Ensure workspace root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import typer
from rich.console import Console
from rich.table import Table

from src.connectors.bybit import BybitConnector
from src.connectors.yahoo_finance import YahooFinanceConnector
from src.core.database import SessionLocal, init_db
from src.core.enums import PlaybookMode
from src.core.models import InstrumentRecord, MarketObservation, ModuleSignal, OpportunityVersion
from src.core.time import utc_now
from src.intelligence.base import ModuleContext
from src.intelligence.technical import TechnicalScanner
from src.opportunities.assembly import OpportunityAssembler

app = typer.Typer(help="Trading Automations CLI")
console = Console()


@app.command()
def init():
    """Initialize database schemas and tables."""
    init_db()
    console.print("[green]Database initialized successfully.[/green]")


@app.command()
def ingest(
    source: str = typer.Option("all", help="Source to ingest: 'bybit', 'yahoo', or 'all'"),
    limit: int = typer.Option(60, help="Number of closed bars to fetch per interval"),
):
    """Ingest market observations from Bybit and Yahoo Finance."""
    init_db()
    session = SessionLocal()
    total_saved = 0

    if source in ("bybit", "all"):
        console.print("[cyan]Ingesting Bybit crypto perpetuals...[/cyan]")
        bybit = BybitConnector()
        insts = bybit.fetch_instruments()
        bybit.save_instruments(session, insts)
        for inst in insts[:10]:
            for interval in ["15m", "1h", "4h", "1d", "1w", "1M"]:
                bars = bybit.fetch_bars(inst, interval=interval, limit=limit)
                saved = bybit.save_bars(session, bars)
                total_saved += saved
        console.print(f"[green]Bybit sync complete. Saved {total_saved} bars.[/green]")

    if source in ("yahoo", "all"):
        console.print("[cyan]Ingesting Yahoo Finance equities & ETFs...[/cyan]")
        yf = YahooFinanceConnector()
        y_insts = yf.fetch_instruments()
        yf.save_instruments(session, y_insts)
        for y_inst in y_insts:
            for interval in ["1h", "1d", "1w", "1M"]:
                bars = yf.fetch_bars(y_inst, interval=interval, limit=limit)
                saved = yf.save_bars(session, bars)
                total_saved += saved
        console.print(f"[green]Yahoo Finance sync complete.[/green]")

    session.close()
    console.print(f"[bold green]Total new observations saved: {total_saved}[/bold green]")


@app.command()
def sync(
    horizons: str = typer.Option("15m,1h,4h,1d", help="Comma-separated horizons to scan after live feed ingestion"),
):
    """Sync newest closed bars from Bybit and Yahoo Finance, then run the scanner pipeline."""
    init_db()
    session = SessionLocal()
    from src.connectors.sync import sync_and_rescan_all

    h_list = [h.strip() for h in horizons.split(",") if h.strip()]
    console.print("[cyan]Pulling live closed bars from Bybit and Yahoo Finance, then rescanning...[/cyan]")
    res = sync_and_rescan_all(session, horizons=h_list, log_fn=lambda msg: console.print(f"[dim]{msg}[/dim]"))
    session.close()

    console.print(
        f"[bold green]Sync & Scan Complete: Ingested {res['new_bars']} new closed bars "
        f"({res['bybit_bars']} Bybit, {res['yahoo_bars']} Yahoo), "
        f"emitted {res['signals_emitted']} signals, assembled {res['opportunities_assembled']} opportunities.[/bold green]"
    )


@app.command()
def scan(
    horizon: str = typer.Option("1h", help="Horizon to evaluate: 15m, 1h, 4h, 1d, 1w, 1M, or all"),
):
    """Run technical scanner and assemble scored opportunities."""
    init_db()
    session = SessionLocal()

    instruments = session.query(InstrumentRecord).filter_by(is_active=True).all()
    inst_ids = [i.id for i in instruments]
    if not inst_ids:
        console.print("[yellow]No active instruments found. Run 'ingest' first.[/yellow]")
        session.close()
        return

    horizons = ["15m", "1h", "4h", "1d", "1w", "1M"] if horizon == "all" else [horizon]
    now = utc_now()
    scanner = TechnicalScanner(mode=PlaybookMode.FILTERED)
    assembler = OpportunityAssembler()

    total_signals = 0
    total_opps = 0

    for h in horizons:
        ctx = ModuleContext(
            instrument_ids=inst_ids,
            horizon=h,
            decision_cutoff=now,
            run_id=f"cli_{now.strftime('%Y%m%d%H%M%S')}",
        )
        res = scanner.evaluate(ctx, session)
        total_signals += len(res.signals)

        opps = assembler.assemble_opportunities(session, playbook_name=f"playbook_{h}", horizon=h)
        total_opps += len(opps)

    session.close()
    console.print(f"[bold green]Scan complete: {total_signals} signals emitted, {total_opps} opportunities assembled.[/bold green]")


@app.command()
@app.command()
def opps(
    aggregate: bool = typer.Option(True, "--aggregate/--individual", help="Group opportunities by ticker with multi-timeframe confluence"),
    exclude_15m: bool = typer.Option(False, "--exclude-15m", help="Exclude noisy 15m intraday scalps"),
    limit: int = typer.Option(20, help="Maximum number of candidates to display"),
):
    """Display active ranked opportunities."""
    init_db()
    session = SessionLocal()

    from src.opportunities import get_latest_active_opportunities, aggregate_opportunities_by_ticker

    opportunities = get_latest_active_opportunities(session)

    if not opportunities:
        console.print("[yellow]No active opportunities found. Run 'scan' first.[/yellow]")
        session.close()
        return

    if aggregate:
        aggregates = aggregate_opportunities_by_ticker(opportunities, exclude_sub_hourly=exclude_15m)[:limit]
        table = Table(title=f"Ranked Ticker Opportunities ({len(aggregates)} Unique Instruments)")
        table.add_column("Ticker", style="bold cyan")
        table.add_column("Peak Merit", justify="right", style="bold yellow")
        table.add_column("Confluence Direction", style="bold")
        table.add_column("Horizons", style="magenta")
        table.add_column("Triggered Systems", style="green")
        table.add_column("Primary Trigger", justify="right")
        table.add_column("Invalidation", justify="right")

        for a in aggregates:
            dir_style = "green" if a.reconciled_direction == "long" else ("red" if a.reconciled_direction == "short" else "yellow")
            systems = ", ".join(s.system_acronym for s in a.triggered_signals)
            horizons_str = ", ".join(a.active_horizons)
            table.add_row(
                a.ticker,
                f"{a.peak_merit_score:.1f}",
                f"[{dir_style}]{a.directional_confluence}[/{dir_style}]",
                horizons_str,
                systems,
                f"${a.trigger_price:.4f}" if a.trigger_price else "—",
                f"${a.invalidation_price:.4f}" if a.invalidation_price else "—",
            )
        console.print(table)
    else:
        opps_subset = opportunities[:limit]
        table = Table(title=f"Individual Ranked Setups ({len(opps_subset)} Setups)")
        table.add_column("ID", style="cyan")
        table.add_column("Ticker", style="bold")
        table.add_column("Horizon", style="magenta")
        table.add_column("Direction", style="green")
        table.add_column("Merit Score", justify="right")
        table.add_column("Confidence", justify="right")
        table.add_column("Trigger", justify="right")
        table.add_column("Invalidation", justify="right")

        for o in opps_subset:
            dir_color = "green" if o.direction == "long" else "red"
            merit_str = f"{o.merit_score:.1f}" if o.merit_score is not None else "Unscored"
            inst_clean = o.instrument_id.split(":")[1] if ":" in o.instrument_id else o.instrument_id
            table.add_row(
                o.opportunity_id,
                inst_clean,
                o.horizon,
                f"[{dir_color}]{o.direction.upper()}[/{dir_color}]",
                merit_str,
                f"{int(o.confidence_score * 100)}%",
                f"${o.trigger_price:.4f}" if o.trigger_price else "—",
                f"${o.invalidation_price:.4f}" if o.invalidation_price else "—",
            )
        console.print(table)

    session.close()


@app.command()
def health():
    """Show database health, bar counts, and data freshness."""
    init_db()
    session = SessionLocal()

    obs_count = session.query(MarketObservation).count()
    inst_count = session.query(InstrumentRecord).count()
    sig_count = session.query(ModuleSignal).count()
    opp_count = session.query(OpportunityVersion).count()

    table = Table(title="System Health & Storage Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="bold green")

    table.add_row("Configured Instruments", str(inst_count))
    table.add_row("Canonical Closed Bars", str(obs_count))
    table.add_row("Signals Emitted", str(sig_count))
    table.add_row("Assembled Opportunities", str(opp_count))

@app.command()
def invalidations(
    limit: int = typer.Option(50, help="Number of telemetry logs to retrieve"),
    export_csv: str = typer.Option(None, help="Optional filepath to export logs as CSV"),
    export_json: str = typer.Option(None, help="Optional filepath to export logs as JSON"),
    horizon: str = typer.Option(None, help="Optional horizon filter (e.g., 15m, 1h, 4h, 1d)"),
):
    """View and export telemetry logs for invalidated, stopped-out, and dissolved setups."""
    import json
    import pandas as pd
    from src.opportunities.lifecycle import OpportunityLifecycleManager

    init_db()
    session = SessionLocal()
    logs = OpportunityLifecycleManager.get_invalidation_telemetry_logs(session, limit=limit)
    session.close()

    if horizon:
        logs = [l for l in logs if l.get("horizon") == horizon]

    if not logs:
        console.print("[yellow]No invalidated setup logs found matching criteria.[/yellow]")
        return

    df = pd.DataFrame(logs)
    stopped_count = len(df[df["reason"] == "stop_breached"])
    dissolved_count = len(df[df["reason"] == "criteria_dissolved"])
    mean_mfe = float(df["mfe_r"].mean()) if "mfe_r" in df and len(df) else 0.0

    table = Table(title=f"Invalidated & Busted Setups Telemetry ({len(logs)} Records)")
    table.add_column("Ticker", style="bold")
    table.add_column("Horizon", style="magenta")
    table.add_column("Dir", style="cyan")
    table.add_column("Playbook", style="white")
    table.add_column("Reason", style="yellow")
    table.add_column("Merit", justify="right")
    table.add_column("Trigger", justify="right")
    table.add_column("Breach Px", justify="right")
    table.add_column("MFE (R)", justify="right", style="green")
    table.add_column("MAE (R)", justify="right", style="red")
    table.add_column("Bars", justify="right")

    for l in logs[:limit]:
        dir_color = "green" if l.get("direction", "").lower() == "long" else "red"
        merit_val = f"{l.get('merit_score', 0):.1f}" if l.get("merit_score") is not None else "—"
        trig_val = f"${l.get('trigger_price', 0):.4f}" if l.get("trigger_price") else "—"
        breach_val = f"${l.get('breach_price', 0):.4f}" if l.get("breach_price") else "—"

        table.add_row(
            l.get("ticker", "—"),
            l.get("horizon", "—"),
            f"[{dir_color}]{l.get('direction', '—')}[/{dir_color}]",
            l.get("playbook_name", "—"),
            l.get("reason", "—"),
            merit_val,
            trig_val,
            breach_val,
            f"+{l.get('mfe_r', 0.0):.2f}R",
            f"-{l.get('mae_r', 0.0):.2f}R",
            str(l.get("bars_held", 0)),
        )

    console.print(table)
    console.print(
        f"[bold]Summary:[/bold] {len(logs)} Total Logs | "
        f"[red]{stopped_count} Stops Breached[/red] | "
        f"[yellow]{dissolved_count} Criteria Dissolved[/yellow] | "
        f"[green]Mean MFE: +{mean_mfe:.2f}R[/green]"
    )

    if export_csv:
        df.to_csv(export_csv, index=False)
        console.print(f"[bold green]Exported telemetry CSV to: {export_csv}[/bold green]")

    if export_json:
        with open(export_json, "w") as f:
            json.dump(logs, f, indent=2, default=str)
        console.print(f"[bold green]Exported telemetry JSON to: {export_json}[/bold green]")


@app.command()
def invalidate(
    opp_id: str = typer.Argument(..., help="Opportunity ID or Version ID to invalidate"),
    reason: str = typer.Option("manual_invalidation", help="Reason for invalidation (e.g. manual_invalidation, market_structure_broken, unfavorable_drift)"),
    notes: str = typer.Option("", help="Optional trader autopsy notes / commentary"),
):
    """Manually invalidate an active trade candidate and write failure telemetry."""
    from src.opportunities.lifecycle import OpportunityLifecycleManager

    init_db()
    session = SessionLocal()
    mgr = OpportunityLifecycleManager()
    opp = mgr.manually_invalidate_opportunity(
        session=session,
        opportunity_id=opp_id,
        reason=reason,
        user_notes=notes,
    )
    status = opp.lifecycle_status if opp else None
    session.close()

    if opp:
        console.print(f"[bold green]Successfully invalidated opportunity [cyan]{opp_id}[/cyan][/bold green]")
        console.print(f"Reason: [yellow]{reason}[/yellow] | Notes: [white]{notes or 'None'}[/white]")
        console.print(f"Status updated to: [red]{status}[/red]")
    else:
        console.print(f"[bold red]Opportunity {opp_id} not found.[/bold red]")


@app.command()
def gui():
    """Launch the Streamlit graphical user interface."""
    console.print("[bold cyan]Launching Trading Automations Cockpit GUI...[/bold cyan]")
    subprocess.run(["uv", "run", "streamlit", "run", "src/app/main.py"])


if __name__ == "__main__":
    app()
