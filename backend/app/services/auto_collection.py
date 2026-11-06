"""Auto data collection scheduler.

Periodically fetches new market data and runs backtests to keep research current.
Uses APScheduler for cron-like scheduling.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.providers.crypto_registry import get_crypto_provider
from app.services.strategy_research import run_study, ResearchSettings
from app.services.strategy_lab import LabBar
from app.models.strategy_study import StrategyStudyRun
from app.services.strategy_study_store import persist_study

logger = logging.getLogger(__name__)

# Configuration (read from environment)
AUTO_COLLECTION_ENABLED = False
AUTO_COLLECTION_INTERVAL_HOURS = 4
USER_ID = UUID('96f9ea41-5a7a-403c-8d9b-decdc8cb54a5')

INSTRUMENTS = [
    'CRYPTO:KRAKEN:BTC-USD',
    'CRYPTO:KRAKEN:ETH-USD',
    'CRYPTO:KRAKEN:SOL-USD',
]
TIMEFRAMES = [15, 60, 240, 1440]
BAR_LIMIT = 720
FOLDS = 5

_scheduler: Optional[BackgroundScheduler] = None
_job_id = 'auto_collection_job'


def init_scheduler(database_url: str, enabled: bool = False, interval_hours: int = 4):
    """Initialize the auto-collection scheduler."""
    global _scheduler, AUTO_COLLECTION_ENABLED, AUTO_COLLECTION_INTERVAL_HOURS
    
    AUTO_COLLECTION_ENABLED = enabled
    AUTO_COLLECTION_INTERVAL_HOURS = interval_hours
    
    if not enabled:
        logger.info("Auto-collection scheduler disabled by configuration")
        return
    
    if _scheduler is not None:
        logger.warning("Scheduler already initialized")
        return
    
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        run_collection_cycle,
        trigger=IntervalTrigger(hours=interval_hours),
        id=_job_id,
        name='Auto Data Collection',
        replace_existing=True,
        kwargs={'database_url': database_url},
    )
    _scheduler.start()
    logger.info(f"Auto-collection scheduler started (every {interval_hours} hours)")


def stop_scheduler():
    """Stop the scheduler if running."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Auto-collection scheduler stopped")


def get_scheduler_status() -> dict:
    """Get scheduler status."""
    if _scheduler is None:
        return {
            'enabled': AUTO_COLLECTION_ENABLED,
            'running': False,
            'interval_hours': AUTO_COLLECTION_INTERVAL_HOURS,
            'next_run': None,
        }
    
    job = _scheduler.get_job(_job_id)
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    
    return {
        'enabled': AUTO_COLLECTION_ENABLED,
        'running': _scheduler.running,
        'interval_hours': AUTO_COLLECTION_INTERVAL_HOURS,
        'next_run': next_run,
    }


def run_collection_cycle(database_url: str):
    """Run one collection cycle: fetch new data and backtest."""
    logger.info(f"Starting auto-collection cycle at {datetime.now(timezone.utc).isoformat()}")
    
    engine = create_engine(database_url)
    provider = get_crypto_provider()
    
    total_runs = 0
    total_trades = 0
    errors = []
    
    for instrument_id in INSTRUMENTS:
        for interval_minutes in TIMEFRAMES:
            try:
                # Fetch bars
                series = provider.get_candle_series(
                    instrument_id,
                    interval_minutes=interval_minutes,
                    limit=BAR_LIMIT,
                )
                
                bars = [
                    LabBar(
                        open_time=candle.open_time,
                        interval_minutes=interval_minutes,
                        open=candle.open,
                        high=candle.high,
                        low=candle.low,
                        close=candle.close,
                        volume=candle.volume,
                    )
                    for candle in series.completed
                ]
                
                if len(bars) < 120:
                    logger.warning(f"Insufficient bars for {instrument_id} @ {interval_minutes}m: {len(bars)}")
                    continue
                
                # Run study
                settings = ResearchSettings(
                    starting_cash=Decimal('100000'),
                    notional_per_trade=Decimal('10000'),
                    commission_rate=Decimal('0.001'),
                    slippage_rate=Decimal('0.0005'),
                )
                
                result = run_study(
                    bars,
                    settings=settings,
                    folds=FOLDS,
                    include_trades=True,
                )
                
                # Store results
                with Session(engine) as db:
                    dataset = {
                        'instrument_id': instrument_id,
                        'interval_minutes': interval_minutes,
                        'provider_id': 'kraken',
                        'data_nature': 'live',
                        'bars': len(bars),
                        'first_open_time': bars[0].open_time.isoformat(),
                        'last_open_time': bars[-1].open_time.isoformat(),
                        'fingerprint': series.fingerprint,
                        'integrity_ok': series.integrity_ok,
                        'gap_count': len(series.gaps),
                        'missing_intervals': series.missing_intervals,  # Already an int
                        'fetched_at': series.fetched_at.isoformat(),
                        'is_stale': series.is_stale,
                        'source': 'auto_collection',
                    }
                    
                    run = persist_study(
                        db,
                        user_id=USER_ID,
                        result=result,
                        bars=bars,
                        dataset=dataset,
                    )
                    
                    db.commit()
                    
                    total_runs += 1
                    total_trades += len(run.trades)
                    
                    logger.info(f"✓ {instrument_id} @ {interval_minutes}m: {len(run.trades)} trades")
            
            except Exception as exc:
                error_msg = f"{instrument_id} @ {interval_minutes}m: {exc}"
                logger.error(f"Collection failed: {error_msg}")
                errors.append(error_msg)
    
    logger.info(f"Collection cycle complete: {total_runs} runs, {total_trades} trades, {len(errors)} errors")
    
    return {
        'runs': total_runs,
        'trades': total_trades,
        'errors': errors,
        'completed_at': datetime.now(timezone.utc).isoformat(),
    }


def trigger_manual_collection(database_url: str) -> dict:
    """Manually trigger a collection cycle."""
    logger.info("Manual collection triggered")
    return run_collection_cycle(database_url)


def get_collection_history(database_url: str, limit: int = 20) -> list:
    """Get history of collection runs."""
    engine = create_engine(database_url)
    
    with engine.connect() as conn:
        result = conn.execute(text('''
            SELECT id, instrument_id, interval_minutes, trade_count, created_at
            FROM strategy_study_runs
            WHERE settings_json->>'source' = 'auto_collection'
               OR disclosures_json @> '["auto_collection"]'
            ORDER BY created_at DESC
            LIMIT :limit
        '''), {'limit': limit})
        
        runs = []
        for row in result.fetchall():
            runs.append({
                'id': str(row[0]),
                'instrument_id': row[1],
                'interval_minutes': row[2],
                'trade_count': row[3],
                'created_at': row[4].isoformat() if row[4] else None,
            })
        
        return runs
