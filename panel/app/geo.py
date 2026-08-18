from __future__ import annotations

import re

# Common VPS node countries/regions. Labels are Chinese for the admin searchable
# datalist; only the ISO 3166-1 alpha-2 code is stored internally.
COUNTRY_OPTIONS = [
    {"code": "JP", "name": "日本"},
    {"code": "HK", "name": "香港"},
    {"code": "SG", "name": "新加坡"},
    {"code": "TW", "name": "台湾"},
    {"code": "KR", "name": "韩国"},
    {"code": "MY", "name": "马来西亚"},
    {"code": "TH", "name": "泰国"},
    {"code": "ID", "name": "印度尼西亚"},
    {"code": "VN", "name": "越南"},
    {"code": "PH", "name": "菲律宾"},
    {"code": "IN", "name": "印度"},
    {"code": "AE", "name": "阿联酋"},
    {"code": "US", "name": "美国"},
    {"code": "CA", "name": "加拿大"},
    {"code": "GB", "name": "英国"},
    {"code": "DE", "name": "德国"},
    {"code": "FR", "name": "法国"},
    {"code": "NL", "name": "荷兰"},
    {"code": "FI", "name": "芬兰"},
    {"code": "SE", "name": "瑞典"},
    {"code": "NO", "name": "挪威"},
    {"code": "CH", "name": "瑞士"},
    {"code": "ES", "name": "西班牙"},
    {"code": "IT", "name": "意大利"},
    {"code": "PL", "name": "波兰"},
    {"code": "CZ", "name": "捷克"},
    {"code": "AT", "name": "奥地利"},
    {"code": "AU", "name": "澳大利亚"},
    {"code": "NZ", "name": "新西兰"},
    {"code": "BR", "name": "巴西"},
]

_COUNTRY_BY_CODE = {row["code"]: row["name"] for row in COUNTRY_OPTIONS}
_COUNTRY_BY_NAME = {row["name"].casefold(): row["code"] for row in COUNTRY_OPTIONS}
_COUNTRY_ALIASES = {
    "japan": "JP", "tokyo": "JP", "osaka": "JP", "日本": "JP", "东京": "JP", "大阪": "JP",
    "hong kong": "HK", "hongkong": "HK", "香港": "HK",
    "singapore": "SG", "新加坡": "SG",
    "taiwan": "TW", "taipei": "TW", "台湾": "TW", "台北": "TW",
    "korea": "KR", "south korea": "KR", "seoul": "KR", "韩国": "KR", "首尔": "KR",
    "malaysia": "MY", "kuala lumpur": "MY", "马来西亚": "MY", "吉隆坡": "MY",
    "thailand": "TH", "bangkok": "TH", "泰国": "TH", "曼谷": "TH",
    "indonesia": "ID", "jakarta": "ID", "印度尼西亚": "ID", "雅加达": "ID",
    "vietnam": "VN", "ho chi minh": "VN", "越南": "VN", "胡志明": "VN",
    "philippines": "PH", "manila": "PH", "菲律宾": "PH", "马尼拉": "PH",
    "india": "IN", "mumbai": "IN", "印度": "IN", "孟买": "IN",
    "uae": "AE", "dubai": "AE", "阿联酋": "AE", "迪拜": "AE",
    "usa": "US", "united states": "US", "美国": "US", "los angeles": "US", "new york": "US", "san jose": "US", "dallas": "US", "seattle": "US",
    "canada": "CA", "toronto": "CA", "vancouver": "CA", "加拿大": "CA", "多伦多": "CA", "温哥华": "CA",
    "uk": "GB", "united kingdom": "GB", "london": "GB", "英国": "GB", "伦敦": "GB",
    "germany": "DE", "frankfurt": "DE", "德国": "DE", "法兰克福": "DE",
    "france": "FR", "paris": "FR", "法国": "FR", "巴黎": "FR",
    "netherlands": "NL", "amsterdam": "NL", "荷兰": "NL", "阿姆斯特丹": "NL",
    "finland": "FI", "helsinki": "FI", "芬兰": "FI", "赫尔辛基": "FI",
    "sweden": "SE", "stockholm": "SE", "瑞典": "SE", "斯德哥尔摩": "SE",
    "norway": "NO", "oslo": "NO", "挪威": "NO", "奥斯陆": "NO",
    "switzerland": "CH", "zurich": "CH", "瑞士": "CH", "苏黎世": "CH",
    "spain": "ES", "madrid": "ES", "西班牙": "ES", "马德里": "ES",
    "italy": "IT", "milan": "IT", "意大利": "IT", "米兰": "IT",
    "poland": "PL", "warsaw": "PL", "波兰": "PL", "华沙": "PL",
    "czech": "CZ", "prague": "CZ", "捷克": "CZ", "布拉格": "CZ",
    "austria": "AT", "vienna": "AT", "奥地利": "AT", "维也纳": "AT",
    "australia": "AU", "sydney": "AU", "melbourne": "AU", "澳大利亚": "AU", "悉尼": "AU",
    "new zealand": "NZ", "auckland": "NZ", "新西兰": "NZ", "奥克兰": "NZ",
    "brazil": "BR", "sao paulo": "BR", "巴西": "BR", "圣保罗": "BR",
}

_REGION_CODE_ALIASES = {
    "tokyo": "TYO", "东京": "TYO",
    "osaka": "OSA", "大阪": "OSA",
    "hong kong": "HKG", "hongkong": "HKG", "香港": "HKG",
    "singapore": "SIN", "新加坡": "SIN",
    "seoul": "SEL", "首尔": "SEL",
    "taipei": "TPE", "台北": "TPE",
    "kuala lumpur": "KUL", "吉隆坡": "KUL",
    "bangkok": "BKK", "曼谷": "BKK",
    "jakarta": "JKT", "雅加达": "JKT",
    "ho chi minh": "SGN", "胡志明": "SGN",
    "manila": "MNL", "马尼拉": "MNL",
    "mumbai": "BOM", "孟买": "BOM",
    "dubai": "DXB", "迪拜": "DXB",
    "los angeles": "LAX", "洛杉矶": "LAX",
    "san jose": "SJC", "圣何塞": "SJC",
    "new york": "NYC", "纽约": "NYC",
    "dallas": "DFW", "达拉斯": "DFW",
    "seattle": "SEA", "西雅图": "SEA",
    "toronto": "YYZ", "多伦多": "YYZ",
    "vancouver": "YVR", "温哥华": "YVR",
    "london": "LON", "伦敦": "LON",
    "frankfurt": "FRA", "法兰克福": "FRA",
    "paris": "PAR", "巴黎": "PAR",
    "amsterdam": "AMS", "阿姆斯特丹": "AMS",
    "helsinki": "HEL", "赫尔辛基": "HEL",
    "stockholm": "STO", "斯德哥尔摩": "STO",
    "oslo": "OSL", "奥斯陆": "OSL",
    "zurich": "ZRH", "苏黎世": "ZRH",
    "madrid": "MAD", "马德里": "MAD",
    "milan": "MIL", "米兰": "MIL",
    "warsaw": "WAW", "华沙": "WAW",
    "prague": "PRG", "布拉格": "PRG",
    "vienna": "VIE", "维也纳": "VIE",
    "sydney": "SYD", "悉尼": "SYD",
    "melbourne": "MEL", "墨尔本": "MEL",
    "auckland": "AKL", "奥克兰": "AKL",
    "sao paulo": "SAO", "圣保罗": "SAO",
}


def country_name(code: str | None) -> str:
    code = (code or "").strip().upper()
    return _COUNTRY_BY_CODE.get(code, code or "-")


def normalize_country_code(value: str | None, *, allow_empty: bool = True) -> str:
    raw = (value or "").strip()
    if not raw:
        if allow_empty:
            return ""
        raise ValueError("请选择前端展示旗帜")
    upper = raw.upper()
    if upper in _COUNTRY_BY_CODE:
        return upper
    folded = raw.casefold()
    if folded in _COUNTRY_BY_NAME:
        return _COUNTRY_BY_NAME[folded]
    if folded in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[folded]
    # Accept labels such as "日本 (JP)" / "日本（JP）" from copied values.
    match = re.search(r"(?:\(|（)([A-Za-z]{2})(?:\)|）)\s*$", raw)
    if match and match.group(1).upper() in _COUNTRY_BY_CODE:
        return match.group(1).upper()
    raise ValueError("前端展示旗帜无效，请从下拉建议中选择")


def normalize_region_code(value: str | None, *, allow_empty: bool = True) -> str:
    raw = (value or "").strip().upper()
    if not raw:
        if allow_empty:
            return ""
        raise ValueError("请输入机器编号前缀")
    if not re.fullmatch(r"[A-Z0-9]{2,8}", raw):
        raise ValueError("机器编号前缀仅允许 2-8 位大写字母或数字，例如 TYO")
    return raw


def infer_country_code(region: str | None) -> str:
    raw = (region or "").strip().casefold()
    if not raw:
        return ""
    for key, code in _COUNTRY_ALIASES.items():
        if key in raw:
            return code
    return ""


def infer_region_code(region: str | None) -> str:
    raw = (region or "").strip().casefold()
    if not raw:
        return ""
    for key, code in _REGION_CODE_ALIASES.items():
        if key in raw:
            return code
    # Last-resort readable prefix for legacy hosts, never based on internal ID.
    ascii_letters = re.sub(r"[^A-Za-z0-9]", "", region or "").upper()
    return ascii_letters[:3] if len(ascii_letters) >= 2 else ""


def server_display_id(server) -> str:
    saved = (getattr(server, "display_id", None) or "").strip().upper()
    if saved:
        return saved
    host = getattr(server, "host", None)
    # The machine-number prefix belongs to the actual Host only. Plan metadata
    # is presentation-only and must never change a server identifier.
    prefix = normalize_region_code(getattr(host, "region_code", ""), allow_empty=True) if host else ""
    if not prefix and host is not None:
        prefix = infer_region_code(getattr(host, "region", ""))
    if not prefix:
        prefix = "VPS"
    number = int(getattr(server, "id", 0) or 0)
    return f"{prefix}-{number:04d}" if number else f"{prefix}-NEW"


def assign_server_display_id(server) -> str:
    value = server_display_id(server)
    if not (getattr(server, "display_id", None) or "").strip() and getattr(server, "id", None):
        server.display_id = value
    return value


def server_country_code(server) -> str:
    # Country/region selection on the Host is used only to choose the front-end flag.
    host = getattr(server, "host", None)
    return (getattr(host, "country_code", "") or "").strip().upper() if host else ""


def server_region(server) -> str:
    # Opened servers keep a snapshot so later catalog edits do not rewrite history.
    saved = (getattr(server, "server_region_snapshot", None) or "").strip()
    if saved:
        return saved
    plan = getattr(server, "plan", None)
    return (getattr(plan, "server_region", "") or "").strip() if plan else ""


def server_region_code(server) -> str:
    # Kept for Mobile API compatibility; this is the Host machine-number prefix.
    host = getattr(server, "host", None)
    if not host:
        return ""
    return (getattr(host, "region_code", "") or infer_region_code(getattr(host, "region", ""))).strip().upper()


def server_network_line(server) -> str:
    # Opened servers keep a snapshot so later catalog edits do not rewrite history.
    saved = (getattr(server, "network_line_snapshot", None) or "").strip()
    if saved:
        return saved
    plan = getattr(server, "plan", None)
    return (getattr(plan, "network_line", "") or "").strip() if plan else ""


def confirmation_matches(server, value: str | None) -> bool:
    entered = (value or "").strip()
    if not entered:
        return False
    # Current UI uses the stable Panel display ID. The provider instance name is
    # retained as a compatibility alias for old clients/bookmarks only.
    return entered in {server_display_id(server), (getattr(server, "name", "") or "").strip()}
