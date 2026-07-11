"""Per-run provider + cache statistics (thread-safe)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ProviderStats:
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    calls: Dict[str, int] = field(default_factory=dict)
    latencies: List[float] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    breaker_trips: int = 0

    def record_call(self, provider: str, latency: float) -> None:
        with self._lock:
            self.calls[provider] = self.calls.get(provider, 0) + 1
            self.latencies.append(latency)

    def hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def miss(self) -> None:
        with self._lock:
            self.cache_misses += 1

    def breaker_tripped(self) -> None:
        with self._lock:
            self.breaker_trips += 1

    def snapshot(self) -> dict:
        with self._lock:
            lat = self.latencies
            return {
                "calls_by_provider": dict(self.calls),
                "network_calls": sum(self.calls.values()),
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "breaker_trips": self.breaker_trips,
                "avg_latency_s": round(sum(lat) / len(lat), 3) if lat else 0.0,
                "total_network_time_s": round(sum(lat), 2),
            }

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()
            self.latencies.clear()
            self.cache_hits = self.cache_misses = 0
            self.breaker_trips = 0


STATS = ProviderStats()
