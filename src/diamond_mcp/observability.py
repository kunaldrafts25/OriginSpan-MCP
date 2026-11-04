"""Low-cardinality operation telemetry. No queries, paths, values or source bodies."""
from collections import defaultdict
from contextlib import contextmanager
import threading
import time
import uuid
from opentelemetry import trace, metrics
from . import __version__

tracer=trace.get_tracer("originspan.discovery",__version__)
meter=metrics.get_meter("originspan.discovery",__version__)
calls=meter.create_counter("diamond_tool_calls")
latency=meter.create_histogram("diamond_tool_duration_seconds",unit="s")
_lock=threading.Lock()
_counts=defaultdict(int)
_durations=defaultdict(float)


@contextmanager
def operation(name):
    request_id=uuid.uuid4().hex
    started=time.perf_counter()
    with tracer.start_as_current_span("discovery."+name) as span:
        span.set_attribute("diamond.request_id",request_id)
        try:
            yield request_id
        finally:
            seconds=time.perf_counter()-started
            calls.add(1,{"operation":name})
            latency.record(seconds,{"operation":name})
            with _lock:
                _counts[name]+=1
                _durations[name]+=seconds


def prometheus():
    with _lock:
        rows=["# TYPE diamond_tool_calls_total counter","# TYPE diamond_tool_duration_seconds_sum counter"]
        for name in sorted(_counts):
            rows.append(f'diamond_tool_calls_total{{operation="{name}"}} {_counts[name]}')
            rows.append(f'diamond_tool_duration_seconds_sum{{operation="{name}"}} {_durations[name]:.9f}')
        return "\n".join(rows)+"\n"
