from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth import login_required
from .db import SessionLocal
from .models import Server
from .providers.incus import IncusProvider
from .providers.mock import MockProvider
from .providers.remote import RemoteHostProvider

router = APIRouter(tags=["live-metrics"])
PROVIDER_NAME = os.getenv("VPS_PROVIDER", "mock").strip().lower()
_ALLOWED_FIELDS = {
    "available",
    "status",
    "cpu_percent",
    "memory_used_bytes",
    "memory_total_bytes",
    "memory_percent",
    "disk_used_bytes",
    "disk_total_bytes",
    "disk_percent",
    "network_rx_bps",
    "network_tx_bps",
    "sampled_at",
    "sampling",
}


class _LiveMetricsAccessFilter(logging.Filter):
    def filter(self, record):
        try:
            return "/metrics HTTP/" not in record.getMessage()
        except Exception:
            return True


logging.getLogger("uvicorn.access").addFilter(_LiveMetricsAccessFilter())


def _provider():
    if PROVIDER_NAME == "remote":
        return RemoteHostProvider()
    if PROVIDER_NAME == "incus":
        return IncusProvider()
    return MockProvider()


def _response(payload: dict) -> JSONResponse:
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.get("/servers/{server_id}/metrics")
def server_live_metrics(request: Request, server_id: int):
    with SessionLocal() as db:
        user = login_required(request, db)
        server = db.get(Server, server_id)
        if not server or server.user_id != user.id or server.deleted_at is not None:
            raise HTTPException(404, "服务器不存在")
        if not server.provider_instance_id:
            return _response({"available": False, "status": server.status or "provisioning"})
        try:
            data = _provider().instance_metrics(server.provider_instance_id)
        except Exception:
            return _response({"available": False, "status": "unavailable"})
        return _response({key: value for key, value in dict(data or {}).items() if key in _ALLOWED_FIELDS})
