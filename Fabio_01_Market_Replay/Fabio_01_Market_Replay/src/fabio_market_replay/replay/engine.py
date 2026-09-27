from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from fabio_market_replay.config import EngineConfig
from fabio_market_replay.contracts import EventAction, MarketEvent, MarketFeatureSnapshot, Side, TradePrint
from fabio_market_replay.market.features import AuctionFeatureEngine
from fabio_market_replay.market.session import session_label
from fabio_market_replay.orderbook.book import MBOBook
from fabio_market_replay.orderflow.engine import OrderFlowEngine
from fabio_market_replay.orderflow.footprint import FootprintEngine
from fabio_market_replay.profile.volume_profile import VolumeProfileEngine
from fabio_market_replay.quality.engine import DataQualityEngine


@dataclass(slots=True)
class ReplayResult:
    events_processed: int
    trades_processed: int
    snapshots: list[MarketFeatureSnapshot]


class ReplayEngine:
    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self.book = MBOBook(top_n=self.config.top_n_depth)
        self.flow = OrderFlowEngine(self.config.rolling_trade_window, self.config.big_print_percentile)
        self.footprint = FootprintEngine(self.config.imbalance_ratio, self.config.stacked_levels)
        self.profile = VolumeProfileEngine(self.config.value_area_fraction, self.config.hvn_quantile, self.config.lvn_quantile)
        self.auction = AuctionFeatureEngine(self.config.market_state)
        self.quality = DataQualityEngine(self.config.stale_ns, self.config.max_sequence_gap, self.config.known_contract_prefixes)
        self.last_trade: TradePrint | None = None
        self.events_processed = 0
        self.trades_processed = 0

    def _trade_from_event(self, e: MarketEvent) -> TradePrint | None:
        if e.action != EventAction.TRADE or e.price_ticks is None or e.size <= 0:
            return None
        return TradePrint(ts_event_ns=e.ts_event_ns, sequence=e.sequence, instrument=e.instrument,
                          active_contract=e.active_contract, price_ticks=e.price_ticks, size=e.size,
                          aggressor_side=e.side,
                          initiator_source="provider" if e.side != Side.NONE else "unknown")

    def process(self, e: MarketEvent, emit_snapshot_on_trade: bool = True) -> MarketFeatureSnapshot | None:
        self.events_processed += 1
        self.quality.observe_event(e)
        if e.action == EventAction.SESSION_BOUNDARY:
            completed = self.profile.snapshot(e.ts_event_ns, e.instrument, e.active_contract, profile_kind="session")
            self.auction.roll_profile(completed)
            self.flow.reset(); self.footprint.reset(); self.profile.reset()
            self.book.apply(e)
            return None
        if e.action == EventAction.CONTRACT_ROLL:
            completed = self.profile.snapshot(e.ts_event_ns, e.instrument, self.book.active_contract or e.active_contract, profile_kind="contract")
            self.auction.roll_profile(completed)
            self.flow.reset(); self.footprint.reset(); self.profile.reset()
            self.last_trade = None
        self.book.apply(e)
        book_snapshot = self.book.snapshot()
        self.quality.observe_book(book_snapshot)
        trade = self._trade_from_event(e)
        if trade is None:
            return None
        self.trades_processed += 1
        self.last_trade = trade
        self.flow.consume(trade)
        self.footprint.consume(trade)
        self.profile.consume(trade)
        profile = self.profile.snapshot(e.ts_event_ns, e.instrument, e.active_contract)
        self.auction.update_price(e.ts_event_ns, trade.price_ticks, self.flow.rolling_delta(), self.flow.state.cvd, profile, self.flow.vwap_ticks())
        if not emit_snapshot_on_trade:
            return None
        return self.snapshot(e.ts_event_ns, e.instrument, e.active_contract)

    def snapshot(self, ts_ns: int, instrument: str, active_contract: str) -> MarketFeatureSnapshot:
        book = self.book.snapshot()
        profile = self.profile.snapshot(ts_ns, instrument, active_contract)
        fp = self.footprint.snapshot(ts_ns, instrument, active_contract)
        auction = self.auction.snapshot(ts_ns, instrument, active_contract)
        q = self.quality.check_stale(ts_ns)
        dom = {
            "weighted_bid_depth": book.weighted_bid_depth,
            "weighted_ask_depth": book.weighted_ask_depth,
            "dom_imbalance": book.dom_imbalance,
            "spread_ticks": float(book.spread_ticks or 0),
            "microprice_ticks": float(book.microprice_ticks or 0),
        }
        absorption = self.flow.absorption_metrics()
        liq = self.book.liquidity_metrics()
        repl = liq.get("bid_replenishment", 0.0) + liq.get("ask_replenishment", 0.0)
        ratio = absorption.get("aggressive_volume_per_tick_progress", 0.0)
        ratio_score = ratio / (ratio + max(self.config.absorption_ratio_scale, 1e-9))
        evidence_components = [ratio_score, absorption.get("adverse_aggression_no_continuation", 0.0),
                               absorption.get("repeated_aggression_same_level", 0.0),
                               absorption.get("cvd_price_divergence", 0.0)]
        absorption["absorption_score_0_1"] = sum(evidence_components) / len(evidence_components)
        absorption["displayed_replenishment_evidence"] = repl
        absorption["iceberg_evidence_score_0_1"] = min(1.0, repl / max(self.config.iceberg_replenishment_scale, 1e-9))
        return MarketFeatureSnapshot(
            timestamp_ns=ts_ns, instrument=instrument, active_contract=active_contract,
            session=session_label(ts_ns, self.config.timezone),
            best_bid_ticks=book.best_bid.price_ticks if book.best_bid else None,
            best_ask_ticks=book.best_ask.price_ticks if book.best_ask else None,
            last_trade_ticks=self.last_trade.price_ticks if self.last_trade else None,
            structure_features={
                "spread_ticks": float(book.spread_ticks or 0),
                "recent_high_ticks": float(max(self.flow.price_path[-100:]) if self.flow.price_path else 0),
                "recent_low_ticks": float(min(self.flow.price_path[-100:]) if self.flow.price_path else 0),
                "recent_range_ticks": float((max(self.flow.price_path[-100:]) - min(self.flow.price_path[-100:])) if self.flow.price_path else 0),
                "distance_to_poc_ticks": float((self.last_trade.price_ticks - profile.poc_ticks) if self.last_trade and profile.poc_ticks is not None else 0),
                "distance_to_vah_ticks": float((self.last_trade.price_ticks - profile.vah_ticks) if self.last_trade and profile.vah_ticks is not None else 0),
                "distance_to_val_ticks": float((self.last_trade.price_ticks - profile.val_ticks) if self.last_trade and profile.val_ticks is not None else 0),
            },
            auction=auction, profiles=[profile], relevant_lvn_zones=profile.lvn_zones,
            relevant_hvn_zones=profile.hvn_zones, relevant_poc_candidates=[profile.poc_ticks] if profile.poc_ticks is not None else [],
            delta=self.flow.state.buy_volume - self.flow.state.sell_volume, cvd=self.flow.state.cvd,
            rolling_delta=self.flow.rolling_delta(), cvd_slope=self.flow.cvd_slope(), cvd_acceleration=self.flow.cvd_acceleration(),
            footprint=fp, aggression_metrics=self.flow.aggression_metrics(), absorption_metrics=absorption,
            big_print_metrics=self.flow.big_print_metrics(), dom_depth_metrics=dom,
            liquidity_pulling_stacking=liq, data_quality=q,
            stale_data_flags={"stream_stale": q.stale_stream},
            provenance={"market_state_location_aggression": "feature_only", "price_units": "integer_ticks", "time_units": "utc_nanoseconds"},
        )

    def replay(self, events: Iterable[MarketEvent], emit_snapshot_on_trade: bool = True) -> ReplayResult:
        snapshots: list[MarketFeatureSnapshot] = []
        for e in events:
            snap = self.process(e, emit_snapshot_on_trade=emit_snapshot_on_trade)
            if snap is not None:
                snapshots.append(snap)
        return ReplayResult(self.events_processed, self.trades_processed, snapshots)

    def iter_snapshots(self, events: Iterable[MarketEvent]) -> Iterator[MarketFeatureSnapshot]:
        for e in events:
            snap = self.process(e, emit_snapshot_on_trade=True)
            if snap is not None:
                yield snap
