from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal
from .models import BalanceLedger, ChainTransaction, RechargeOrder, SiteSetting, User
from .notifications import queue_notification
from .runtime_config import runtime_secret

USDT_DECIMALS = 6
USDT_SCALE = 10 ** USDT_DECIMALS
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
# Polygon PoS's canonical USDT address. Polygon upgraded the asset to USDT0 in
# 2025 while retaining the existing contract address for integrations.
POLYGON_USDT0_CONTRACT = "0xc2132D05D31c914a87C6611C10748AEb04B58e8F"
POLYGON_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {ch: i for i, ch in enumerate(_BASE58_ALPHABET)}
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def valid_tron_address(value: str) -> bool:
    """Validate a TRON Base58Check account/contract address (0x41 payload prefix)."""
    value = (value or "").strip()
    if len(value) != 34 or not value.startswith("T"):
        return False
    try:
        number = 0
        for ch in value:
            number = number * 58 + _BASE58_INDEX[ch]
        raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
        leading_zeroes = len(value) - len(value.lstrip("1"))
        raw = (b"\x00" * leading_zeroes) + raw
        if len(raw) != 25 or raw[0] != 0x41:
            return False
        payload, checksum = raw[:-4], raw[-4:]
        expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        return checksum == expected
    except (KeyError, ValueError, OverflowError):
        return False


def valid_evm_address(value: str) -> bool:
    return bool(_EVM_ADDRESS_RE.fullmatch((value or "").strip()))


def validate_payment_addresses(cfg: dict) -> None:
    if cfg.get("tron_enabled"):
        if not valid_tron_address(cfg.get("tron_wallet", "")):
            raise ValueError("TRON 收款地址格式错误")
        if not valid_tron_address(cfg.get("tron_contract", "")):
            raise ValueError("TRON USDT 合约地址格式错误")
    if cfg.get("polygon_enabled"):
        if not valid_evm_address(cfg.get("polygon_wallet", "")):
            raise ValueError("Polygon 收款地址格式错误")
        if not valid_evm_address(cfg.get("polygon_contract", "")):
            raise ValueError("Polygon USDT 合约地址格式错误")


def _setting(db, key: str, default: str = "") -> str:
    row = db.get(SiteSetting, key)
    return row.value if row else default


def _enabled(db, key: str, default: bool = False) -> bool:
    return _setting(db, key, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


def payment_config(db) -> dict:
    rate = Decimal(_setting(db, "usdt_cny_rate", "7.20"))
    return {
        "enabled": _enabled(db, "payment_enabled", False),
        "rate": rate,
        "rate_micros": int((rate * Decimal(1_000_000)).quantize(Decimal("1"))),
        "min_cny": Decimal(_setting(db, "recharge_min_cny", "10")),
        "max_cny": Decimal(_setting(db, "recharge_max_cny", "10000")),
        "expire_minutes": max(5, int(_setting(db, "payment_expire_minutes", "30") or 30)),
        "late_grace_hours": max(0, int(_setting(db, "payment_late_grace_hours", "24") or 24)),
        "tron_enabled": _enabled(db, "payment_tron_enabled", True),
        "tron_wallet": _setting(db, "payment_tron_wallet", "").strip(),
        "tron_contract": _setting(db, "payment_tron_contract", TRON_USDT_CONTRACT).strip() or TRON_USDT_CONTRACT,
        "polygon_enabled": _enabled(db, "payment_polygon_enabled", True),
        "polygon_wallet": _setting(db, "payment_polygon_wallet", "").strip(),
        "polygon_rpc": _setting(db, "payment_polygon_rpc", os.getenv("POLYGON_RPC_URL", "https://polygon.drpc.org")).strip(),
        "polygon_contract": _setting(db, "payment_polygon_contract", POLYGON_USDT0_CONTRACT).strip() or POLYGON_USDT0_CONTRACT,
        "polygon_confirmations": max(1, int(_setting(db, "payment_polygon_confirmations", "20") or 20)),
    }


def usdt_units_to_text(units: int) -> str:
    value = Decimal(int(units)) / Decimal(USDT_SCALE)
    return f"{value:.6f}"


def rate_text(rate_micros: int) -> str:
    return f"{Decimal(rate_micros) / Decimal(1_000_000):.4f}".rstrip("0").rstrip(".")


def _next_unique_units(db, chain: str, base_units: int) -> int:
    # Never reuse an exact amount on the same chain. This makes late payments
    # deterministic and prevents an old transfer from crediting a newer order.
    units = max(1, int(base_units))
    while db.scalar(select(RechargeOrder.id).where(
        RechargeOrder.chain == chain,
        RechargeOrder.expected_usdt_units == units,
    )):
        units += 1
    return units


def _polygon_latest_block(rpc_url: str) -> int:
    response = httpx.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return int(payload["result"], 16)


def create_recharge_order(db, user: User, *, chain: str, cny_amount: Decimal) -> RechargeOrder:
    cfg = payment_config(db)
    if not cfg["enabled"]:
        raise ValueError("在线充值当前未启用")
    if chain not in {"tron", "polygon"}:
        raise ValueError("不支持的充值网络")
    if cny_amount < cfg["min_cny"] or cny_amount > cfg["max_cny"]:
        raise ValueError(f"充值金额必须在 ¥{cfg['min_cny']} - ¥{cfg['max_cny']} 之间")

    if chain == "tron":
        if not cfg["tron_enabled"] or not cfg["tron_wallet"]:
            raise ValueError("TRON 充值通道尚未配置")
        if not valid_tron_address(cfg["tron_wallet"]):
            raise ValueError("TRON 收款地址格式错误")
        if not valid_tron_address(cfg["tron_contract"]):
            raise ValueError("TRON USDT 合约地址格式错误")
        wallet = cfg["tron_wallet"]
        contract = cfg["tron_contract"]
        start_block = None
    else:
        if not cfg["polygon_enabled"] or not cfg["polygon_wallet"] or not cfg["polygon_rpc"]:
            raise ValueError("Polygon 充值通道尚未配置")
        if not valid_evm_address(cfg["polygon_wallet"]):
            raise ValueError("Polygon 收款地址格式错误")
        if not valid_evm_address(cfg["polygon_contract"]):
            raise ValueError("Polygon USDT 合约地址格式错误")
        wallet = cfg["polygon_wallet"]
        contract = cfg["polygon_contract"]
        start_block = _polygon_latest_block(cfg["polygon_rpc"])

    requested_cents = int((cny_amount * 100).quantize(Decimal("1"), rounding=ROUND_CEILING))
    exact_cny = Decimal(requested_cents) / Decimal(100)
    usdt = exact_cny / cfg["rate"]
    base_units = int((usdt * USDT_SCALE).quantize(Decimal("1"), rounding=ROUND_CEILING))
    expected_units = _next_unique_units(db, chain, base_units)

    row = RechargeOrder(
        user_id=user.id,
        chain=chain,
        requested_cny_cents=requested_cents,
        rate_micros=int((cfg["rate"] * Decimal(1_000_000)).quantize(Decimal("1"))),
        expected_usdt_units=expected_units,
        deposit_address=wallet,
        token_contract=contract,
        status="pending",
        start_block=start_block,
        expires_at=datetime.utcnow() + timedelta(minutes=cfg["expire_minutes"]),
    )
    db.add(row)
    db.flush()
    return row


def _credit_order(db, order: RechargeOrder, *, tx_hash: str, event_index: str, from_address: str | None, amount_units: int, block_number: int | None, raw: dict):
    if order.status == "paid":
        return False

    existing = db.scalar(select(ChainTransaction).where(
        ChainTransaction.chain == order.chain,
        ChainTransaction.tx_hash == tx_hash,
        ChainTransaction.event_index == str(event_index),
    ))
    if existing:
        # Transaction already processed elsewhere. Do not double-credit.
        return False

    tx = ChainTransaction(
        chain=order.chain,
        tx_hash=tx_hash,
        event_index=str(event_index),
        from_address=from_address,
        to_address=order.deposit_address,
        token_contract=order.token_contract,
        amount_units=int(amount_units),
        block_number=block_number,
        raw_json=json.dumps(raw, ensure_ascii=False, separators=(",", ":"))[:20000],
    )
    db.add(tx)

    user = db.get(User, order.user_id)
    if not user:
        raise RuntimeError("充值订单用户不存在")
    user.balance_cents += order.requested_cny_cents
    db.flush()
    db.add(BalanceLedger(
        user_id=user.id,
        delta_cents=order.requested_cny_cents,
        balance_after_cents=user.balance_cents,
        kind="usdt_recharge",
        reference_type="recharge_order",
        reference_id=order.id,
        note=f"{order.chain.upper()} USDT recharge {tx_hash}",
    ))

    order.status = "paid"
    order.tx_hash = tx_hash
    order.tx_event_index = str(event_index)
    order.from_address = from_address
    order.confirmations = max(order.confirmations or 0, 1)
    order.detected_at = order.detected_at or datetime.utcnow()
    order.paid_at = datetime.utcnow()

    queue_notification(
        db,
        user,
        title="充值到账",
        body=f"充值订单 #{order.id} 已确认，账户余额增加 ¥{order.requested_cny_cents / 100:.2f}。",
        kind="payment",
        severity="success",
        event_key=f"recharge-paid:{order.id}",
    )
    return True


def _tron_headers(db) -> dict:
    key = runtime_secret(db, "trongrid_api_key_enc", env_name="TRONGRID_API_KEY", default="").strip()
    return {"TRON-PRO-API-KEY": key} if key else {}


def _scan_tron(db, orders: list[RechargeOrder], cfg: dict) -> int:
    if not orders or not cfg["tron_wallet"]:
        return 0
    min_time = min(o.created_at for o in orders) - timedelta(minutes=2)
    url = f"https://api.trongrid.io/v1/accounts/{cfg['tron_wallet']}/transactions/trc20"
    params = {
        "only_confirmed": "true",
        "limit": 200,
        "min_timestamp": int(min_time.timestamp() * 1000),
    }
    seen = 0
    credited = 0
    fingerprint = None
    for _ in range(5):
        if fingerprint:
            params["fingerprint"] = fingerprint
        response = httpx.get(url, params=params, headers=_tron_headers(db), timeout=20)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        if not data:
            break
        for item in data:
            seen += 1
            token = item.get("token_info") or {}
            contract = (token.get("address") or "").strip()
            to_addr = (item.get("to") or "").strip()
            if contract != cfg["tron_contract"] or to_addr != cfg["tron_wallet"]:
                continue
            try:
                amount = int(item.get("value") or 0)
            except Exception:
                continue
            order = next((o for o in orders if o.expected_usdt_units == amount and o.status != "paid"), None)
            if not order:
                continue
            tx_hash = item.get("transaction_id") or item.get("transactionId") or ""
            if not tx_hash:
                continue
            if _credit_order(
                db,
                order,
                tx_hash=tx_hash,
                event_index="0",
                from_address=item.get("from"),
                amount_units=amount,
                block_number=None,
                raw=item,
            ):
                credited += 1

        meta = payload.get("meta") or {}
        fingerprint = meta.get("fingerprint")
        if not fingerprint or len(data) < 200:
            break
    return credited


def _address_topic(address: str) -> str:
    if not valid_evm_address(address):
        raise ValueError("Polygon 收款地址格式错误")
    clean = address[2:].lower()
    return "0x" + ("0" * 24) + clean


def _scan_polygon(db, orders: list[RechargeOrder], cfg: dict) -> int:
    if not orders or not cfg["polygon_wallet"] or not cfg["polygon_rpc"]:
        return 0
    latest = _polygon_latest_block(cfg["polygon_rpc"])
    safe_block = max(0, latest - cfg["polygon_confirmations"] + 1)
    credited = 0
    to_topic = _address_topic(cfg["polygon_wallet"])

    # Scan from the earliest order's creation block, but cap the range so a
    # misconfigured ancient order cannot cause an unbounded RPC query.
    starts = [o.start_block for o in orders if o.start_block is not None]
    if not starts:
        return 0
    start = max(min(starts), safe_block - 100_000)
    if safe_block < start:
        return 0

    request_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(start),
            "toBlock": hex(safe_block),
            "address": cfg["polygon_contract"],
            "topics": [POLYGON_TRANSFER_TOPIC, None, to_topic],
        }],
    }
    response = httpx.post(cfg["polygon_rpc"], json=request_payload, timeout=25)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"])

    for log in payload.get("result") or []:
        try:
            amount = int(log.get("data", "0x0"), 16)
            block_number = int(log.get("blockNumber", "0x0"), 16)
            log_index = str(int(log.get("logIndex", "0x0"), 16))
            topics = log.get("topics") or []
            from_addr = "0x" + topics[1][-40:] if len(topics) >= 2 else None
        except Exception:
            continue
        order = next((o for o in orders if o.expected_usdt_units == amount and o.status != "paid" and (o.start_block or 0) <= block_number), None)
        if not order:
            continue
        order.confirmations = max(0, latest - block_number + 1)
        tx_hash = log.get("transactionHash") or ""
        if not tx_hash:
            continue
        if _credit_order(
            db,
            order,
            tx_hash=tx_hash,
            event_index=log_index,
            from_address=from_addr,
            amount_units=amount,
            block_number=block_number,
            raw=log,
        ):
            order.confirmations = latest - block_number + 1
            credited += 1
    return credited


def poll_pending_payments() -> tuple[int, int]:
    credited = 0
    failed = 0
    now = datetime.utcnow()
    with SessionLocal() as db:
        cfg = payment_config(db)
        if not cfg["enabled"]:
            return 0, 0

        grace_cutoff = now - timedelta(hours=cfg["late_grace_hours"])
        candidates = db.scalars(
            select(RechargeOrder).where(
                RechargeOrder.status.in_(["pending", "expired"]),
                RechargeOrder.created_at >= grace_cutoff,
            ).order_by(RechargeOrder.id)
        ).all()
        for order in candidates:
            if order.status == "pending" and order.expires_at <= now:
                order.status = "expired"

        tron = [o for o in candidates if o.chain == "tron" and o.status != "paid"]
        polygon = [o for o in candidates if o.chain == "polygon" and o.status != "paid"]
        try:
            if cfg["tron_enabled"]:
                credited += _scan_tron(db, tron, cfg)
        except Exception as exc:
            failed += 1
            print(f"[payment] tron: {exc}")
        try:
            if cfg["polygon_enabled"]:
                credited += _scan_polygon(db, polygon, cfg)
        except Exception as exc:
            failed += 1
            print(f"[payment] polygon: {exc}")

        db.commit()
    return credited, failed
