from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal
from .models import BalanceLedger, ChainTransaction, RechargeOrder, SiteSetting, User
from .notifications import queue_notification
from .runtime_config import runtime_secret

USDT_DECIMALS = 6
USDT_SCALE = 10 ** USDT_DECIMALS
USDT_ORDER_DECIMALS = 3
USDT_ORDER_QUANTUM_UNITS = 10 ** (USDT_DECIMALS - USDT_ORDER_DECIMALS)
USDT_ORDER_QUANTUM = Decimal("0.001")
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
        "tron_mode": _setting(db, "payment_tron_mode", "auto").strip().lower() if _setting(db, "payment_tron_mode", "auto").strip().lower() in {"auto", "manual"} else "auto",
        "tron_wallet": _setting(db, "payment_tron_wallet", "").strip(),
        # Token contracts are system-owned security constants. Legacy DB values are intentionally ignored.
        "tron_contract": TRON_USDT_CONTRACT,
        "polygon_enabled": _enabled(db, "payment_polygon_enabled", True),
        "polygon_mode": _setting(db, "payment_polygon_mode", "auto").strip().lower() if _setting(db, "payment_polygon_mode", "auto").strip().lower() in {"auto", "manual"} else "auto",
        "polygon_wallet": _setting(db, "payment_polygon_wallet", "").strip(),
        "polygon_rpc": _setting(db, "payment_polygon_rpc", os.getenv("POLYGON_RPC_URL", "https://polygon.drpc.org")).strip(),
        "polygon_contract": POLYGON_USDT0_CONTRACT,
        "polygon_confirmations": max(1, int(_setting(db, "payment_polygon_confirmations", "20") or 20)),
    }


def usdt_units_to_text(units: int) -> str:
    """Render new orders with 3 decimals while preserving legacy exact amounts.

    v1.0.2 creates amounts on a 0.001 USDT grid. Older pending orders can still
    contain six-decimal values; those must remain visible exactly as originally
    created so users are never instructed to send a different amount.
    """
    units = int(units)
    value = Decimal(units) / Decimal(USDT_SCALE)
    if units % USDT_ORDER_QUANTUM_UNITS == 0:
        return f"{value:.3f}"
    return f"{value:.6f}"


def rate_text(rate_micros: int) -> str:
    return f"{Decimal(rate_micros) / Decimal(1_000_000):.4f}".rstrip("0").rstrip(".")


def _next_unique_units(db, chain: str, base_units: int) -> int:
    # Never reuse an exact amount on the same chain. v1.0.2 keeps all newly
    # generated amounts on a 0.001 USDT grid, so even collision adjustments
    # remain human-friendly: 1.389 -> 1.390 -> 1.391 instead of six decimals.
    units = max(USDT_ORDER_QUANTUM_UNITS, int(base_units))
    remainder = units % USDT_ORDER_QUANTUM_UNITS
    if remainder:
        units += USDT_ORDER_QUANTUM_UNITS - remainder
    while db.scalar(select(RechargeOrder.id).where(
        RechargeOrder.chain == chain,
        RechargeOrder.expected_usdt_units == units,
    )):
        units += USDT_ORDER_QUANTUM_UNITS
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
        wallet = cfg["tron_wallet"]
        contract = TRON_USDT_CONTRACT
        start_block = None
        mode = cfg["tron_mode"]
        if mode == "auto" and not runtime_secret(db, "trongrid_api_key_enc", env_name="TRONGRID_API_KEY", default="").strip():
            raise ValueError("TRON 自动充值需要先配置 TronGrid API Key")
    else:
        if not cfg["polygon_enabled"] or not cfg["polygon_wallet"]:
            raise ValueError("Polygon 充值通道尚未配置")
        if not valid_evm_address(cfg["polygon_wallet"]):
            raise ValueError("Polygon 收款地址格式错误")
        wallet = cfg["polygon_wallet"]
        contract = POLYGON_USDT0_CONTRACT
        mode = cfg["polygon_mode"]
        if mode == "auto":
            if not cfg["polygon_rpc"]:
                raise ValueError("Polygon 自动充值需要配置 RPC")
            start_block = _polygon_latest_block(cfg["polygon_rpc"])
        else:
            start_block = None

    requested_cents = int((cny_amount * 100).quantize(Decimal("1"), rounding=ROUND_CEILING))
    exact_cny = Decimal(requested_cents) / Decimal(100)
    usdt = exact_cny / cfg["rate"]
    rounded_usdt = usdt.quantize(USDT_ORDER_QUANTUM, rounding=ROUND_HALF_UP)
    base_units = int((rounded_usdt * USDT_SCALE).quantize(Decimal("1")))
    expected_units = _next_unique_units(db, chain, base_units)

    row = RechargeOrder(
        user_id=user.id,
        chain=chain,
        requested_cny_cents=requested_cents,
        rate_micros=int((cfg["rate"] * Decimal(1_000_000)).quantize(Decimal("1"))),
        expected_usdt_units=expected_units,
        deposit_address=wallet,
        token_contract=contract,
        status="pending" if mode == "auto" else "manual",
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


def test_tron_configuration(db) -> dict:
    cfg = payment_config(db)
    if not cfg["tron_enabled"]:
        raise ValueError("TRON 充值通道当前已停用")
    if not valid_tron_address(cfg["tron_wallet"]):
        raise ValueError("TRON 收款地址格式错误")
    if cfg["tron_mode"] == "manual":
        return {"mode": "manual", "message": "TRON 人工充值配置有效；人工模式不需要 TronGrid API Key。"}

    key = runtime_secret(db, "trongrid_api_key_enc", env_name="TRONGRID_API_KEY", default="").strip()
    if not key:
        raise ValueError("尚未配置 TronGrid API Key")
    url = f"https://api.trongrid.io/v1/accounts/{cfg['tron_wallet']}/transactions/trc20"
    response = httpx.get(
        url,
        params={"only_confirmed": "true", "limit": 1, "contract_address": TRON_USDT_CONTRACT},
        headers={"TRON-PRO-API-KEY": key},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or "data" not in payload:
        raise RuntimeError("TronGrid 返回格式异常")
    return {"mode": "auto", "message": "TRON 收款地址与 TronGrid API Key 连接正常。"}


def test_polygon_configuration(db) -> dict:
    cfg = payment_config(db)
    if not cfg["polygon_enabled"]:
        raise ValueError("Polygon 充值通道当前已停用")
    if not valid_evm_address(cfg["polygon_wallet"]):
        raise ValueError("Polygon 收款地址格式错误")
    if cfg["polygon_mode"] == "manual":
        return {"mode": "manual", "message": "Polygon 人工充值配置有效；人工模式不需要 RPC 扫链。"}
    if not cfg["polygon_rpc"]:
        raise ValueError("尚未配置 Polygon RPC")

    chain_resp = httpx.post(
        cfg["polygon_rpc"],
        json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
        timeout=15,
    )
    chain_resp.raise_for_status()
    chain_payload = chain_resp.json()
    if chain_payload.get("error"):
        raise RuntimeError(str(chain_payload["error"]))
    chain_id = int(chain_payload.get("result") or "0x0", 16)
    if chain_id != 137:
        raise RuntimeError(f"RPC 网络不正确：检测到 Chain ID {chain_id}，Polygon PoS 应为 137")

    code_resp = httpx.post(
        cfg["polygon_rpc"],
        json={"jsonrpc": "2.0", "id": 2, "method": "eth_getCode", "params": [POLYGON_USDT0_CONTRACT, "latest"]},
        timeout=15,
    )
    code_resp.raise_for_status()
    code_payload = code_resp.json()
    if code_payload.get("error"):
        raise RuntimeError(str(code_payload["error"]))
    if str(code_payload.get("result") or "0x") in {"0x", "0x0", "0x00"}:
        raise RuntimeError("Polygon RPC 无法读取系统内置 USDT0 合约")
    return {"mode": "auto", "message": "Polygon RPC、网络与系统内置 USDT0 合约检查正常。"}




def normalize_tx_hash(chain: str, tx_hash: str) -> str:
    value = (tx_hash or "").strip()
    if chain == "polygon" and value.lower().startswith("0x"):
        value = value[2:]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("交易哈希格式不正确")
    return ("0x" + value.lower()) if chain == "polygon" else value.lower()


def _ensure_repair_tx_unused(db, order: RechargeOrder, tx_hash: str) -> None:
    """A chain transaction can credit at most one recharge order."""
    normalized = normalize_tx_hash(order.chain, tx_hash)
    other_order = db.scalar(
        select(RechargeOrder.id).where(
            RechargeOrder.id != order.id,
            RechargeOrder.chain == order.chain,
            func.lower(RechargeOrder.tx_hash) == normalized.lower(),
        )
    )
    if other_order:
        raise ValueError(f"该 TxHash 已用于充值订单 #{other_order}")

    tx = db.scalar(
        select(ChainTransaction).where(
            ChainTransaction.chain == order.chain,
            func.lower(ChainTransaction.tx_hash) == normalized.lower(),
        )
    )
    if tx:
        # A transaction already recorded by the scanner must never be reused.
        if order.tx_hash != normalized:
            raise ValueError("该 TxHash 已经被系统处理，不能重复补单")


def _verify_tron_repair(db, order: RechargeOrder, tx_hash: str) -> dict:
    cfg = payment_config(db)
    key = runtime_secret(db, "trongrid_api_key_enc", env_name="TRONGRID_API_KEY", default="").strip()
    if not key:
        raise RuntimeError("未配置 TronGrid API Key，无法自动校验；可人工核对后使用强制补单")

    normalized = normalize_tx_hash("tron", tx_hash)
    min_time = order.created_at - timedelta(minutes=5)
    url = f"https://api.trongrid.io/v1/accounts/{order.deposit_address}/transactions/trc20"
    params = {
        "only_confirmed": "true",
        "limit": 200,
        "min_timestamp": int(min_time.timestamp() * 1000),
    }
    fingerprint = None
    matched = None

    for _ in range(8):
        if fingerprint:
            params["fingerprint"] = fingerprint
        response = httpx.get(
            url,
            params=params,
            headers={"TRON-PRO-API-KEY": key},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or []
        for item in rows:
            item_hash = str(item.get("transaction_id") or item.get("transactionId") or "").lower()
            if item_hash == normalized:
                matched = item
                break
        if matched:
            break
        meta = payload.get("meta") or {}
        fingerprint = meta.get("fingerprint")
        if not fingerprint or len(rows) < 200:
            break

    if not matched:
        raise ValueError("TronGrid 未在该收款地址的已确认 TRC20 交易中找到此 TxHash")

    token = matched.get("token_info") or {}
    contract = (token.get("address") or "").strip()
    to_addr = (matched.get("to") or "").strip()
    try:
        amount = int(matched.get("value") or 0)
    except Exception as exc:
        raise ValueError("链上交易金额格式异常") from exc

    if contract != TRON_USDT_CONTRACT:
        raise ValueError("该交易不是系统内置官方 TRON USDT 合约")
    if to_addr != order.deposit_address:
        raise ValueError("该交易收款地址与订单收款地址不一致")
    if amount != order.expected_usdt_units:
        raise ValueError(
            f"到账金额不匹配：链上 {usdt_units_to_text(amount)} USDT，订单应付 {usdt_units_to_text(order.expected_usdt_units)} USDT"
        )

    return {
        "chain": "tron",
        "tx_hash": normalized,
        "event_index": "0",
        "from_address": matched.get("from"),
        "to_address": to_addr,
        "amount_units": amount,
        "block_number": None,
        "confirmations": 1,
        "confirmed": True,
        "raw": matched,
    }


def _verify_polygon_repair(db, order: RechargeOrder, tx_hash: str) -> dict:
    cfg = payment_config(db)
    rpc = (cfg.get("polygon_rpc") or "").strip()
    if not rpc:
        raise RuntimeError("未配置 Polygon RPC，无法自动校验；可人工核对后使用强制补单")

    normalized = normalize_tx_hash("polygon", tx_hash)

    chain_resp = httpx.post(
        rpc,
        json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
        timeout=15,
    )
    chain_resp.raise_for_status()
    chain_payload = chain_resp.json()
    if chain_payload.get("error"):
        raise RuntimeError(str(chain_payload["error"]))
    if int(chain_payload.get("result") or "0x0", 16) != 137:
        raise RuntimeError("当前 RPC 不是 Polygon PoS 主网")

    receipt_resp = httpx.post(
        rpc,
        json={"jsonrpc": "2.0", "id": 2, "method": "eth_getTransactionReceipt", "params": [normalized]},
        timeout=20,
    )
    receipt_resp.raise_for_status()
    receipt_payload = receipt_resp.json()
    if receipt_payload.get("error"):
        raise RuntimeError(str(receipt_payload["error"]))
    receipt = receipt_payload.get("result")
    if not receipt:
        raise ValueError("Polygon RPC 尚未找到该 TxHash")
    if int(receipt.get("status") or "0x0", 16) != 1:
        raise ValueError("该 Polygon 交易执行失败")

    target_topic = _address_topic(order.deposit_address).lower()
    match = None
    for log in receipt.get("logs") or []:
        topics = [str(x).lower() for x in (log.get("topics") or [])]
        if str(log.get("address") or "").lower() != POLYGON_USDT0_CONTRACT.lower():
            continue
        if len(topics) < 3 or topics[0] != POLYGON_TRANSFER_TOPIC.lower() or topics[2] != target_topic:
            continue
        try:
            amount = int(log.get("data") or "0x0", 16)
        except Exception:
            continue
        match = (log, topics, amount)
        if amount == order.expected_usdt_units:
            break

    if not match:
        raise ValueError("交易中没有找到转入订单收款地址的官方 Polygon USDT0 Transfer 事件")

    log, topics, amount = match
    if amount != order.expected_usdt_units:
        raise ValueError(
            f"到账金额不匹配：链上 {usdt_units_to_text(amount)} USDT，订单应付 {usdt_units_to_text(order.expected_usdt_units)} USDT"
        )

    block_number = int(receipt.get("blockNumber") or "0x0", 16)
    latest = _polygon_latest_block(rpc)
    confirmations = max(0, latest - block_number + 1)
    required = int(cfg.get("polygon_confirmations") or 1)
    if confirmations < required:
        raise ValueError(f"确认数不足：当前 {confirmations}，需要 {required}")

    return {
        "chain": "polygon",
        "tx_hash": normalized,
        "event_index": str(int(log.get("logIndex") or "0x0", 16)),
        "from_address": ("0x" + topics[1][-40:]) if len(topics) >= 2 else None,
        "to_address": order.deposit_address,
        "amount_units": amount,
        "block_number": block_number,
        "confirmations": confirmations,
        "confirmed": True,
        "raw": receipt,
    }


def verify_recharge_transaction(db, order: RechargeOrder, tx_hash: str) -> dict:
    if order.status == "paid":
        raise ValueError("该充值订单已经入账，无需补单")
    normalized = normalize_tx_hash(order.chain, tx_hash)
    _ensure_repair_tx_unused(db, order, normalized)
    if order.chain == "tron":
        return _verify_tron_repair(db, order, normalized)
    if order.chain == "polygon":
        return _verify_polygon_repair(db, order, normalized)
    raise ValueError("不支持的充值网络")


def repair_credit_order(
    db,
    order: RechargeOrder,
    *,
    tx_hash: str,
    verified: dict | None = None,
    force: bool = False,
) -> bool:
    """Credit any unpaid recharge order exactly once for operations recovery."""
    if order.status == "paid":
        return False

    normalized = normalize_tx_hash(order.chain, tx_hash)
    _ensure_repair_tx_unused(db, order, normalized)

    if verified is not None:
        if normalize_tx_hash(order.chain, str(verified.get("tx_hash") or "")) != normalized:
            raise ValueError("校验交易与补单交易不一致")
        if int(verified.get("amount_units") or 0) != int(order.expected_usdt_units):
            raise ValueError("校验到账金额与订单应付金额不一致")
        return _credit_order(
            db,
            order,
            tx_hash=normalized,
            event_index=str(verified.get("event_index") or "0"),
            from_address=verified.get("from_address"),
            amount_units=int(verified["amount_units"]),
            block_number=verified.get("block_number"),
            raw={"source": "admin_repair_verified", "verification": verified.get("raw") or {}},
        )

    if not force:
        raise ValueError("未完成链上校验")

    now = datetime.utcnow()
    claimed = db.execute(
        update(RechargeOrder)
        .where(RechargeOrder.id == order.id, RechargeOrder.status != "paid")
        .values(
            status="paid",
            tx_hash=normalized,
            confirmations=max(order.confirmations or 0, 1),
            detected_at=order.detected_at or now,
            paid_at=now,
        )
    )
    if claimed.rowcount != 1:
        return False

    # Reserve the TxHash in the chain transaction ledger as an operational
    # repair record. It is intentionally marked as force-repair in raw_json.
    db.add(ChainTransaction(
        chain=order.chain,
        tx_hash=normalized,
        event_index="force-repair",
        from_address=None,
        to_address=order.deposit_address,
        token_contract=order.token_contract,
        amount_units=order.expected_usdt_units,
        block_number=None,
        raw_json=json.dumps(
            {"source": "admin_force_repair", "order_id": order.id},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    ))

    user = db.get(User, order.user_id)
    if not user:
        raise RuntimeError("充值订单用户不存在")
    user.balance_cents += order.requested_cny_cents
    db.flush()
    db.add(BalanceLedger(
        user_id=user.id,
        delta_cents=order.requested_cny_cents,
        balance_after_cents=user.balance_cents,
        kind="usdt_recharge_repair_force",
        reference_type="recharge_order",
        reference_id=order.id,
        note=f"FORCE repair {order.chain.upper()} USDT {normalized}",
    ))
    queue_notification(
        db,
        user,
        title="充值到账",
        body=f"充值订单 #{order.id} 已由管理员补单，账户余额增加 ¥{order.requested_cny_cents / 100:.2f}。",
        kind="payment",
        severity="success",
        event_key=f"recharge-repair-paid:{order.id}",
    )
    return True



def force_credit_order_without_tx(
    db,
    order: RechargeOrder,
    *,
    reason: str,
) -> bool:
    """Last-resort administrative credit without chain evidence.

    This intentionally does NOT create a ChainTransaction and does NOT invent a
    TxHash. The recharge order itself remains the idempotency boundary: once the
    order is paid, it cannot be credited a second time.
    """
    if order.status == "paid":
        return False

    reason = re.sub(r"\s+", " ", (reason or "").strip())
    if len(reason) < 5:
        raise ValueError("请填写至少 5 个字符的补单原因")
    if len(reason) > 300:
        raise ValueError("补单原因最多 300 个字符")

    now = datetime.utcnow()
    claimed = db.execute(
        update(RechargeOrder)
        .where(RechargeOrder.id == order.id, RechargeOrder.status != "paid")
        .values(
            status="paid",
            confirmations=0,
            detected_at=order.detected_at or now,
            paid_at=now,
        )
    )
    if claimed.rowcount != 1:
        return False

    user = db.get(User, order.user_id)
    if not user:
        raise RuntimeError("充值订单用户不存在")

    user.balance_cents += order.requested_cny_cents
    db.flush()

    db.add(BalanceLedger(
        user_id=user.id,
        delta_cents=order.requested_cny_cents,
        balance_after_cents=user.balance_cents,
        kind="usdt_recharge_repair_no_tx",
        reference_type="recharge_order",
        reference_id=order.id,
        note=f"NO-TX repair {order.chain.upper()} | reason={reason}",
    ))

    queue_notification(
        db,
        user,
        title="充值到账",
        body=f"充值订单 #{order.id} 已由管理员人工补单，账户余额增加 ¥{order.requested_cny_cents / 100:.2f}。",
        kind="payment",
        severity="success",
        event_key=f"recharge-no-tx-repair-paid:{order.id}",
    )
    return True

def manual_credit_order(db, order: RechargeOrder, *, tx_hash: str | None = None) -> bool:
    """Credit a manual-mode recharge order exactly once after administrator verification."""
    if order.status == "paid":
        return False
    if order.status != "manual":
        raise ValueError("只有人工充值订单可以使用人工确认入账")

    now = datetime.utcnow()
    final_tx_hash = (tx_hash or order.tx_hash or "").strip()[:128] or None
    claimed = db.execute(
        update(RechargeOrder)
        .where(RechargeOrder.id == order.id, RechargeOrder.status == "manual")
        .values(
            status="paid",
            tx_hash=final_tx_hash,
            confirmations=1,
            detected_at=order.detected_at or now,
            paid_at=now,
        )
    )
    if claimed.rowcount != 1:
        return False

    user = db.get(User, order.user_id)
    if not user:
        raise RuntimeError("充值订单用户不存在")
    user.balance_cents += order.requested_cny_cents
    db.flush()
    db.add(BalanceLedger(
        user_id=user.id,
        delta_cents=order.requested_cny_cents,
        balance_after_cents=user.balance_cents,
        kind="usdt_recharge_manual",
        reference_type="recharge_order",
        reference_id=order.id,
        note=f"Manual {order.chain.upper()} USDT recharge confirmation",
    ))
    queue_notification(
        db,
        user,
        title="充值到账",
        body=f"充值订单 #{order.id} 已由管理员确认，账户余额增加 ¥{order.requested_cny_cents / 100:.2f}。",
        kind="payment",
        severity="success",
        event_key=f"recharge-manual-paid:{order.id}",
    )
    return True


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
