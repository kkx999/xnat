from __future__ import annotations

import json
import threading
import time

_CACHE_SECONDS = 3.0
_DISK_FALLBACK_SECONDS = 30.0
_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict]] = {}
_SAMPLES: dict[str, tuple[float, int, int, int]] = {}
_DISK_SAMPLES: dict[str, tuple[float, int, int]] = {}


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _metadata(payload):
    if not isinstance(payload, dict):
        return {}
    meta = payload.get("metadata")
    return meta if isinstance(meta, dict) else payload


def _state(instance_id: str, run) -> dict:
    proc = run(["incus", "query", f"/1.0/instances/{instance_id}/state"], check=False, timeout=12)
    if proc.returncode != 0:
        return {}
    try:
        return _metadata(json.loads(proc.stdout or "{}"))
    except Exception:
        return {}


def _network_from_state(state: dict) -> tuple[int, int]:
    rx = 0
    tx = 0
    network = state.get("network") if isinstance(state, dict) else None
    if not isinstance(network, dict):
        return 0, 0
    for name, item in network.items():
        if str(name).lower() == "lo" or not isinstance(item, dict):
            continue
        counters = item.get("counters") if isinstance(item.get("counters"), dict) else item
        rx += _int(counters.get("bytes_received") or counters.get("rx_bytes"))
        tx += _int(counters.get("bytes_sent") or counters.get("tx_bytes"))
    return rx, tx


def _network(instance_id: str, run, state: dict) -> tuple[int, int, bool]:
    rx, tx = _network_from_state(state)
    if rx or tx:
        return rx, tx, True
    command = (
        "rx=0; tx=0; for d in /sys/class/net/*; do "
        "[ \"$(basename \"$d\")\" = lo ] && continue; "
        "r=$(cat \"$d/statistics/rx_bytes\" 2>/dev/null || echo 0); "
        "t=$(cat \"$d/statistics/tx_bytes\" 2>/dev/null || echo 0); "
        "rx=$((rx+r)); tx=$((tx+t)); done; "
        "printf '%s %s\\n' \"$rx\" \"$tx\""
    )
    proc = run(["incus", "exec", instance_id, "--", "sh", "-lc", command], check=False, timeout=12)
    if proc.returncode != 0:
        return 0, 0, False
    try:
        raw_rx, raw_tx = (proc.stdout or "").strip().split()
        return int(raw_rx), int(raw_tx), True
    except Exception:
        return 0, 0, False


def _disk_fallback(instance_id: str, run, now: float) -> tuple[int, int]:
    with _LOCK:
        cached = _DISK_SAMPLES.get(instance_id)
        if cached and now - cached[0] < _DISK_FALLBACK_SECONDS:
            return cached[1], cached[2]
    command = "df -P -k / | awk 'NR==2 {printf \"%.0f %.0f\\n\", $2*1024, $3*1024}'"
    proc = run(["incus", "exec", instance_id, "--", "sh", "-lc", command], check=False, timeout=12)
    if proc.returncode != 0:
        return 0, 0
    try:
        total_text, used_text = (proc.stdout or "").strip().split()
        total = max(0, int(total_text))
        used = max(0, int(used_text))
    except Exception:
        return 0, 0
    with _LOCK:
        _DISK_SAMPLES[instance_id] = (now, total, used)
    return total, used


def collect(instance_id: str, *, run, instance_exists, instance_status, resource_snapshot) -> dict:
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(instance_id)
        if cached and now - cached[0] < _CACHE_SECONDS:
            return dict(cached[1])

    if not instance_exists(instance_id):
        return {"available": False, "status": "missing", "sampled_at": int(time.time())}

    state = _state(instance_id, run)
    raw_status = str(state.get("status") or instance_status(instance_id) or "unknown").lower()
    status = "running" if raw_status == "running" else "stopped" if raw_status in {"stopped", "frozen"} else raw_status
    cfg = resource_snapshot(instance_id)
    cpu_limit = max(1, int(cfg.get("cpu") or 1))
    memory_limit = int(cfg.get("memory_mb") or 0) * 1024 * 1024
    disk_limit = int(float(cfg.get("disk_gb") or 0) * (1024 ** 3))

    if status != "running":
        with _LOCK:
            _SAMPLES.pop(instance_id, None)
        result = {
            "available": False, "status": status, "cpu_percent": None,
            "memory_used_bytes": 0, "memory_total_bytes": memory_limit, "memory_percent": None,
            "disk_used_bytes": 0, "disk_total_bytes": disk_limit, "disk_percent": None,
            "network_rx_bps": None, "network_tx_bps": None,
            "sampled_at": int(time.time()), "sampling": False,
        }
        with _LOCK:
            _CACHE[instance_id] = (now, result)
        return dict(result)

    cpu = state.get("cpu") if isinstance(state.get("cpu"), dict) else {}
    memory = state.get("memory") if isinstance(state.get("memory"), dict) else {}
    cpu_usage = _int(cpu.get("usage"))
    memory_used = _int(memory.get("usage"))
    memory_total = _int(memory.get("total")) or memory_limit

    disks = state.get("disk") if isinstance(state.get("disk"), dict) else {}
    root = disks.get("root") if isinstance(disks.get("root"), dict) else next((item for item in disks.values() if isinstance(item, dict)), {})
    disk_used = _int(root.get("usage"))
    disk_total = _int(root.get("total")) or disk_limit
    if disk_used <= 0 or disk_total <= 0:
        fallback_total, fallback_used = _disk_fallback(instance_id, run, now)
        disk_total = fallback_total or disk_total
        disk_used = fallback_used or disk_used

    rx, tx, network_ok = _network(instance_id, run, state)
    cpu_percent = None
    rx_bps = None
    tx_bps = None
    with _LOCK:
        previous = _SAMPLES.get(instance_id)
        if previous:
            elapsed = now - previous[0]
            if elapsed >= 0.25:
                if cpu_usage >= previous[1]:
                    cpu_percent = round(min(100.0, max(0.0, (cpu_usage - previous[1]) / (elapsed * 1_000_000_000 * cpu_limit) * 100.0)), 1)
                if network_ok and rx >= previous[2] and tx >= previous[3]:
                    rx_bps = max(0, int((rx - previous[2]) / elapsed))
                    tx_bps = max(0, int((tx - previous[3]) / elapsed))
        _SAMPLES[instance_id] = (now, cpu_usage, rx, tx)

    def percent(used: int, total: int):
        if total <= 0:
            return None
        return round(min(100.0, max(0.0, used / total * 100.0)), 1)

    result = {
        "available": True, "status": "running", "cpu_percent": cpu_percent,
        "memory_used_bytes": memory_used, "memory_total_bytes": memory_total,
        "memory_percent": percent(memory_used, memory_total),
        "disk_used_bytes": disk_used, "disk_total_bytes": disk_total,
        "disk_percent": percent(disk_used, disk_total),
        "network_rx_bps": rx_bps, "network_tx_bps": tx_bps,
        "sampled_at": int(time.time()),
        "sampling": cpu_percent is None or rx_bps is None or tx_bps is None,
    }
    with _LOCK:
        _CACHE[instance_id] = (now, result)
    return dict(result)
