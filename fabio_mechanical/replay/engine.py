"""Event-by-event replay: raw MarketEvent -> book/flow/profile/structure -> MarketFeatureSnapshot.

Single-threaded by design (event order is state). Snapshots are emitted only at packet
boundaries (Databento ``F_LAST``; every event when provider flags are absent) that contained at
least one trade, so every snapshot reflects a consistent book and only past information.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from fabio_mechanical.configs import load_config
from fabio_mechanical.contracts import (
    AuctionFeatureSnapshot,
    Direction,
    EventAction,
    MarketEvent,
    MarketFeatureSnapshot,
    Side,
    TradePrint,
    VolumeProfileSnapshot,
)
from fabio_mechanical.data.instruments import instrument_spec
from fabio_mechanical.market.features import AuctionFeatureEngine
from fabio_mechanical.market.session import SessionCalendar
from fabio_mechanical.market.structure import AuctionStructureEngine, StructureSettings
from fabio_mechanical.orderbook.book import MBOBook
from fabio_mechanical.orderflow.engine import OrderFlowEngine
from fabio_mechanical.orderflow.footprint import RollingFootprint
from fabio_mechanical.profile.volume_profile import VolumeProfileEngine
from fabio_mechanical.quality.engine import F_LAST, DataQualityEngine

NS = 1_000_000_000


@dataclass(slots=True)
class ReplayResult:
    events_processed: int
    trades_processed: int
    snapshots: list[MarketFeatureSnapshot]


class ReplayEngine:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config if config is not None else load_config()
        self.cfg = cfg
        m = cfg["market"]
        self.calendar = SessionCalendar.from_config(cfg)
        self.period_ns = int(cfg["structure"]["period_seconds"]) * NS
        self.book = MBOBook(top_n=int(m["top_n_depth"]))
        self.flow = OrderFlowEngine(int(m["rolling_trade_window"]), float(m["big_print_percentile"]))
        self.footprint = RollingFootprint(int(m["rolling_trade_window"]), float(m["imbalance_ratio"]))
        self.profile = VolumeProfileEngine(float(m["value_area_fraction"]), float(m["hvn_quantile"]), float(m["lvn_quantile"]))
        self.auction = AuctionFeatureEngine(dict(m["market_state"]), window=int(m["auction_window"]))
        self.structure = AuctionStructureEngine(StructureSettings.from_config(cfg))
        q = cfg["data"]["quality"]
        self.quality = DataQualityEngine(
            stale_ns=int(cfg["strategy"]["stale_data_ms_max"]) * 1_000_000,
            max_sequence_gap=int(q["max_sequence_gap"]),
            known_prefixes=tuple(cfg["data"]["known_contract_prefixes"]),
            dedupe_window=int(q["dedupe_window"]),
        )
        self.profile_refresh_ns = int(m["session_profile_refresh_ms"]) * 1_000_000
        self.absorption_ratio_scale = float(m["absorption_ratio_scale"])
        self.iceberg_scale = float(m["iceberg_replenishment_scale"])
        self.last_trade: TradePrint | None = None
        self.events_processed = 0
        self.trades_processed = 0
        self.event_index = 0
        self.session_boundaries = 0
        self.contract_rolls = 0
        self.current_trading_day: date | None = None
        self._day_start_ns = 0
        self._day_end_ns = 0
        self.prior_session_profile: VolumeProfileSnapshot | None = None
        self.news_days: set[str] = set()
        self.last_clock_ns = 0
        self._packet_has_trade = False
        self._session_profile: VolumeProfileSnapshot | None = None
        self._session_profile_ts = 0
        self._tick_size: Decimal | None = None
        self._instrument = ""
        self._contract = ""

    # ------------------------------------------------------------------ boundaries
    def _close_session(self, clock: int) -> None:
        if self.profile.volume_by_price:
            completed = self.profile.snapshot(
                clock, self._instrument, self._contract, profile_kind="session", period_index=clock // self.period_ns
            )
            self.auction.roll_profile(completed)
            self.structure.add_prior_session(completed)
            self.prior_session_profile = completed
        self.flow.reset()
        self.footprint.reset()
        self.profile.reset()
        self._session_profile = None
        self.session_boundaries += 1

    def _contract_roll(self, e: MarketEvent, clock: int) -> None:
        if self.profile.volume_by_price:
            completed = self.profile.snapshot(clock, self._instrument, self._contract or e.active_contract, profile_kind="contract")
            self.auction.roll_profile(completed)
        self.flow.reset()
        self.footprint.reset()
        self.profile.reset()
        self._session_profile = None
        self.last_trade = None
        self.prior_session_profile = None
        self.structure.on_contract_roll(e.active_contract)
        self.contract_rolls += 1

    # ------------------------------------------------------------------ processing
    def process(self, e: MarketEvent) -> MarketFeatureSnapshot | None:
        self.events_processed += 1
        clock = e.clock_ns
        self.last_clock_ns = max(self.last_clock_ns, clock)
        if self._tick_size is None:
            self._tick_size = instrument_spec(self.cfg, e.instrument).tick_size
            self.structure.instrument = e.instrument
            self.structure.contract = e.active_contract
        self._instrument = e.instrument
        if e.action == EventAction.SESSION_BOUNDARY:
            self.quality.observe_event(e)
            self._close_session(clock)
            self.current_trading_day, self._day_start_ns, self._day_end_ns = self.calendar.trading_day_bounds(clock)
            return None
        if not (self._day_start_ns <= clock < self._day_end_ns):
            td, self._day_start_ns, self._day_end_ns = self.calendar.trading_day_bounds(clock)
            if self.current_trading_day is None:
                self.current_trading_day = td
            elif td != self.current_trading_day:
                self._close_session(clock)
                self.current_trading_day = td
        if e.action == EventAction.CONTRACT_ROLL:
            self._contract_roll(e, clock)
        self._contract = e.active_contract
        self.quality.observe_event(e)
        self.book.apply(e)
        if e.action == EventAction.TRADE and e.price_ticks is not None and e.size > 0:
            trade = TradePrint.model_construct(
                schema_version=e.schema_version,
                ts_event_ns=e.ts_event_ns,
                sequence=e.sequence,
                instrument=e.instrument,
                active_contract=e.active_contract,
                price_ticks=e.price_ticks,
                size=e.size,
                aggressor_side=e.side,
                initiator_source="provider" if e.side != Side.NONE else "unknown",
            )
            self._on_trade(trade, clock)
        boundary = e.provider_flags is None or bool(e.provider_flags & F_LAST)
        if boundary:
            self.quality.observe_top_of_book(self.book.best_bid(), self.book.best_ask())
            if self._packet_has_trade:
                self._packet_has_trade = False
                return self.snapshot(clock)
        return None

    def _on_trade(self, trade: TradePrint, clock: int) -> None:
        self.trades_processed += 1
        self.last_trade = trade
        self.flow.consume(trade)
        self.footprint.consume(trade)
        self.profile.consume(trade)
        self.structure.on_trade(trade.price_ticks, trade.size, clock)
        profile = self._developing_profile(clock)
        self.auction.update_price(clock, trade.price_ticks, self.flow.rolling_delta(), self.flow.state.cvd, profile, self.flow.vwap_ticks())
        self._packet_has_trade = True

    def _developing_profile(self, clock: int) -> VolumeProfileSnapshot:
        if self._session_profile is None or clock - self._session_profile_ts >= self.profile_refresh_ns:
            self._session_profile = self.profile.snapshot(clock, self._instrument, self._contract, period_index=clock // self.period_ns)
            self._session_profile_ts = clock
        return self._session_profile

    # ------------------------------------------------------------------ snapshot
    def snapshot(self, clock: int) -> MarketFeatureSnapshot:
        inst, contract = self._instrument, self._contract
        book = self.book.snapshot()
        profile = self._developing_profile(clock)
        fp = self.footprint.snapshot(clock, inst, contract)
        last = self.last_trade.price_ticks if self.last_trade else None
        struct = self.structure.fields(last, clock)
        impulse_profile = struct.pop("impulse_profile")
        reclaim_profile = struct.pop("reclaim_profile")
        rejection_speed = struct.pop("rejection_speed")
        auction = AuctionFeatureSnapshot(ts_event_ns=clock, instrument=inst, active_contract=contract, **self.auction.fields(), **struct)
        q = self.quality.check_stale(clock)
        flow = self.flow
        absorption = flow.absorption_metrics()
        liq = self.book.liquidity_metrics()
        repl = liq["bid_replenishment"] + liq["ask_replenishment"]
        ratio = absorption["aggressive_volume_per_tick_progress"]
        ratio_score = ratio / (ratio + max(self.absorption_ratio_scale, 1e-9))
        components = [
            ratio_score,
            absorption["adverse_aggression_no_continuation"],
            absorption["repeated_aggression_same_level"],
            absorption["cvd_price_divergence"],
        ]
        absorption["absorption_score_0_1"] = sum(components) / len(components)
        absorption["displayed_replenishment_evidence"] = repl
        absorption["iceberg_evidence_score_0_1"] = min(1.0, repl / max(self.iceberg_scale, 1e-9))
        spread = float(book.spread_ticks or 0)
        dom = {
            "weighted_bid_depth": book.weighted_bid_depth,
            "weighted_ask_depth": book.weighted_ask_depth,
            "dom_imbalance": book.dom_imbalance,
            "spread_ticks": spread,
            "microprice_ticks": float(book.microprice_ticks or 0),
        }
        path = flow.price_path
        hi = float(max(path)) if path else 0.0
        lo = float(min(path)) if path else 0.0

        def dist(level: int | None) -> float:
            return float(last - level) if last is not None and level is not None else 0.0

        big_buy, big_sell = flow.directional_big_print_percentiles()
        agg = flow.directional_aggression()
        w_low, w_high = flow.window_range()
        bid_net, ask_net = liq["bid_net_liquidity"], liq["ask_net_liquidity"]
        denom = abs(bid_net) + abs(ask_net)
        expansion = auction.range_expansion_ratio
        volatility = "NORMAL" if expansion is None else "LOW" if expansion < 0.8 else "HIGH" if expansion > 1.5 else "NORMAL"
        regime = "BALANCE" if auction.balanced else "TREND" if auction.out_of_balance else "TRANSITION"
        trading_day = self.current_trading_day.isoformat() if self.current_trading_day else ""
        profiles = [profile] if self.prior_session_profile is None else [profile, self.prior_session_profile]
        self.event_index += 1
        assert self._tick_size is not None
        return MarketFeatureSnapshot(
            timestamp_ns=clock,
            event_index=self.event_index,
            structure_period_index=clock // self.period_ns,
            instrument=inst,
            active_contract=contract,
            tick_size=self._tick_size,
            session=self.calendar.label(clock),
            trading_day=trading_day,
            best_bid_ticks=book.best_bid.price_ticks if book.best_bid else None,
            best_ask_ticks=book.best_ask.price_ticks if book.best_ask else None,
            last_trade_ticks=last,
            structure_direction=auction.breakout_side if auction.out_of_balance else Direction.FLAT,
            structure_features={
                "spread_ticks": spread,
                "recent_high_ticks": hi,
                "recent_low_ticks": lo,
                "recent_range_ticks": hi - lo,
                "distance_to_poc_ticks": dist(profile.poc_ticks),
                "distance_to_vah_ticks": dist(profile.vah_ticks),
                "distance_to_val_ticks": dist(profile.val_ticks),
            },
            auction=auction,
            profiles=profiles,
            impulse_profile=impulse_profile,
            reclaim_profile=reclaim_profile,
            balance_areas=self.structure.balance_areas(),
            relevant_lvn_zones=profile.lvn_zones,
            relevant_hvn_zones=profile.hvn_zones,
            relevant_poc_candidates=self.structure.poc_candidates(),
            delta=flow.state.buy_volume - flow.state.sell_volume,
            cvd=flow.state.cvd,
            rolling_delta=flow.rolling_delta(),
            cvd_slope=flow.cvd_slope(),
            cvd_acceleration=flow.cvd_acceleration(),
            cvd_slope_normalized=flow.cvd_slope_normalized(),
            delta_zscore=flow.delta_zscore(),
            big_buy_print_percentile=big_buy,
            big_sell_print_percentile=big_sell,
            aggression_window_low_ticks=w_low,
            aggression_window_high_ticks=w_high,
            absorption_score=absorption["absorption_score_0_1"],
            rejection_speed=rejection_speed,
            pulling_stacking_score=0.0 if denom == 0 else (bid_net - ask_net) / denom,
            directional_aggression=Direction.LONG if agg > 0 else Direction.SHORT if agg < 0 else Direction.FLAT,
            footprint=fp,
            book=book,
            aggression_metrics=flow.aggression_metrics(),
            absorption_metrics=absorption,
            big_print_metrics=flow.big_print_metrics(),
            dom_depth_metrics=dom,
            liquidity_pulling_stacking=liq,
            data_quality=q,
            data_quality_score=0.0 if not q.trade_safe else max(0.0, 1.0 - flow.unknown_aggressor_fraction()),
            stale_ms=self.quality.stale_ms(clock),
            stale_data_flags={"stream_stale": q.stale_stream},
            news_day=(trading_day in self.news_days) if self.news_days else None,
            volatility_regime=volatility,  # type: ignore[arg-type]
            market_regime=regime,  # type: ignore[arg-type]
            provenance={
                "market_state_location_aggression": "feature_only",
                "price_units": "integer_ticks",
                "time_units": "utc_nanoseconds_replay_clock",
                "snapshot_trigger": "packet_boundary_with_trade",
            },
        )

    def snapshot_at(self, now_ns: int) -> MarketFeatureSnapshot:
        """Timer-driven view (e.g. live/heartbeat): identical features, staleness measured at ``now_ns``."""
        snap = self.snapshot(max(now_ns, self.last_clock_ns))
        return snap

    # ------------------------------------------------------------------ helpers
    def iter_snapshots(self, events: Iterable[MarketEvent]) -> Iterator[MarketFeatureSnapshot]:
        for e in events:
            snap = self.process(e)
            if snap is not None:
                yield snap

    def replay(self, events: Iterable[MarketEvent]) -> ReplayResult:
        snaps = list(self.iter_snapshots(events))
        return ReplayResult(self.events_processed, self.trades_processed, snaps)
