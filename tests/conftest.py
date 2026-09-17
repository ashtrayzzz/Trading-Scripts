"""Pytest fixtures: in-memory database, synthetic bar generation, and test accounts."""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.accounts.base import AccountState
from src.accounts.breakoutprop import BreakoutpropAccount
from src.accounts.personal import PersonalAccount
from src.core.enums import InstrumentType, QualityStatus, TimeInterval
from src.core.models import Base, InstrumentRecord, MarketObservation
from src.core.time import utc_now


@pytest.fixture
def db_session() -> Session:
    """Create an isolated in-memory SQLite database session for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def test_instrument(db_session: Session) -> InstrumentRecord:
    """Create and persist a canonical test instrument."""
    inst = InstrumentRecord(
        id="bybit:BTCUSDT:perpetual",
        asset_id="BTC",
        venue="bybit",
        symbol="BTC/USDT:USDT",
        instrument_type=InstrumentType.PERPETUAL.value,
        base_currency="BTC",
        quote_currency="USDT",
        contract_multiplier=1.0,
        tick_size=0.1,
        lot_size=0.001,
        is_active=True,
        extra_metadata={"breakoutprop_symbol": "BTCUSD"},
        producer="test",
        producer_version="0.1.0",
        as_of=utc_now(),
        available_at=utc_now(),
        quality_status=QualityStatus.VALID.value,
    )
    db_session.add(inst)
    db_session.commit()
    return inst


@pytest.fixture
def synthetic_bars(test_instrument: InstrumentRecord, db_session: Session) -> list[MarketObservation]:
    """Generate 60 closed synthetic bars exhibiting compression followed by an upside breakout."""
    base_time = utc_now() - timedelta(hours=65)
    bars = []
    base_price = 50000.0

    for i in range(60):
        open_time = base_time + timedelta(hours=i)
        close_time = open_time + timedelta(hours=1)

        # Bar 0-45: tight compression (low volatility)
        if i < 45:
            delta = (i % 3 - 1) * 20.0
            o = base_price + delta
            h = o + 30.0
            l = o - 30.0
            c = o + 10.0
            v = 100.0
        # Bar 45-59: upside breakout with high volume
        else:
            step = (i - 45) * 80.0
            o = base_price + step
            h = o + 120.0
            l = o - 10.0
            c = o + 100.0
            v = 350.0  # 3.5x volume surge

        obs = MarketObservation(
            id=f"synth_obs_{i}",
            instrument_id=test_instrument.id,
            interval=TimeInterval.H1.value,
            open_time=open_time,
            close_time=close_time,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v,
            is_closed=True,
            content_hash=f"hash_{i}",
            producer="synthetic_fixture",
            producer_version="0.1.0",
            as_of=close_time,
            available_at=close_time,
            quality_status=QualityStatus.VALID.value,
        )
        bars.append(obs)
        db_session.add(obs)

    db_session.commit()
    return bars


@pytest.fixture
def breakoutprop_account() -> BreakoutpropAccount:
    """Standard Breakoutprop Turbo 10k test account."""
    return BreakoutpropAccount(
        account_id="bp_test_10k",
        name="Breakoutprop Test 10k",
        starting_equity=10000.0,
        max_drawdown_floor=9700.0,
        daily_loss_limit=300.0,
        operational_buffer=50.0,
        per_trade_risk_limit=100.0,
    )


@pytest.fixture
def personal_account() -> PersonalAccount:
    """Standard Personal Account test profile."""
    return PersonalAccount(
        account_id="personal_test",
        name="Personal Test",
        per_trade_risk_pct=1.0,
        max_positions=5,
    )
