"""URL 업로드 안전 검증 + DNS rebinding 방어.

W2 명세 v0.3 §3.E + DE-18 + W3 v0.5 §3.E (Day 4) — 클라우드 환경에서 SSRF 공격 (내부
메타데이터 endpoint 169.254.169.254, Kubernetes 내부 서비스 등) 을 차단한다.

체크 항목
1. 스킴이 http/https 인가 (file://, gopher://, ftp:// 등 거절)
2. host 가 명시적으로 위험한 이름인가 (localhost, 0.0.0.0)
3. host 의 **모든** resolved IP 가 안전한가 (multi-IP round-robin attack 차단)
4. (옵션) 응답 fetch 후 DNS 재조회 → 재조회 결과가 첫 resolve 와 동일 + 여전히 안전한가
   (DNS rebinding 방어 — fetch 시점에 공격자가 DNS 를 사설 IP 로 변경해도 차단)

multi-IP round-robin attack
    공격자가 DNS A 레코드에 [공인 IP, 169.254.169.254] 두 개를 응답하면, 단순히 첫
    IP 만 검증하던 v0.4 구현에선 검증을 통과 후 fetch 단계에서 OS resolver 가 사설
    IP 를 골라 호출하는 시나리오가 가능. 본 모듈은 모든 resolved IP 를 검증한다.

DNS rebinding
    검증 시점 (T1) DNS → 공인 IP, fetch 시점 (T2) DNS → 사설 IP 로 TTL 짧게 바꾸는 공격.
    `recheck_dns_consistency()` 가 fetch 직후 재조회하여 IP 집합이 변하지 않았는지
    확인. 변경됐거나 사설 IP 가 섞여 있으면 fetch 결과를 폐기하고 400.

레퍼런스
- https://www.terminusdb.com/blog/2021/10/20/dns-rebinding-attacks
- https://cloud.google.com/security/compute/sec-best-practices  (메타데이터 169.254 차단)
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# 이름으로 명시적 차단할 호스트 (IP resolve 불요)
_BLOCKED_NAMES: frozenset[str] = frozenset(
    {"localhost", "0.0.0.0", "::", "::1"}
)

# 각 IP 안전성 판정 — `True` 면 차단 사유 문자열, `None` 이면 안전.
_IpAddr = ipaddress.IPv4Address | ipaddress.IPv6Address


def validate_url_safety(url: str) -> tuple[bool, str, frozenset[str]]:
    """URL 안전 검증. 반환: (안전 여부, 사유 문자열, resolved IP 문자열 집합).

    호출자 동작
    - 안전: True · 사유 "OK" · IP 집합 (DNS rebinding recheck 입력) — fetch 진행
    - 불안: False · 사유 (한국어, 사용자 노출 가능) · IP 집합 (참고용, 차단 사유 진단)
      → `HTTPException(400, detail=f"안전하지 않은 URL: {reason}")` 로 래핑

    3-tuple 반환은 caller (documents.py:upload_url) 1곳뿐이라 영향 최소.
    DNS rebinding recheck 은 부산물 IP 집합을 caller 가 들고 있어야 가능 → 함께 반환이
    자연스러움 (단일 책임 원칙은 약간 양보).
    """
    parsed = urlparse(url)

    # 1) 스킴
    if parsed.scheme not in ("http", "https"):
        return (
            False,
            f"지원하지 않는 스킴입니다: {parsed.scheme!r} (http/https 만 허용)",
            frozenset(),
        )

    # 2) host 존재
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "URL 에 호스트가 없습니다.", frozenset()

    # 3) 명시적 차단 이름
    if host in _BLOCKED_NAMES:
        return False, f"내부 호스트 차단: {host}", frozenset()

    # 4) IP 분석 — 직접 IP 입력 또는 DNS resolve 후 검증.
    #    DNS resolve 시 모든 IP 를 검증 (multi-IP round-robin attack 차단).
    ip_objs, resolve_error = _resolve_all(host)
    if resolve_error:
        return False, resolve_error, frozenset()
    if not ip_objs:
        return False, f"DNS 조회 결과가 없습니다: {host}", frozenset()

    for ip_obj in ip_objs:
        verdict = _classify_ip(ip_obj)
        if verdict is not None:
            # multi-IP 중 하나라도 사설/loopback 이면 즉시 차단.
            return (
                False,
                verdict,
                frozenset(str(ip) for ip in ip_objs),
            )

    return True, "OK", frozenset(str(ip) for ip in ip_objs)


def recheck_dns_consistency(
    url: str, original_ips: frozenset[str]
) -> tuple[bool, str]:
    """fetch 직후 호출 — DNS 가 fetch 사이에 변경되지 않았는지 확인 (rebinding 방어).

    동작
    - URL 의 host 를 다시 resolve.
    - 새 IP 집합이 `original_ips` 의 부분집합이고 모두 안전하면 통과.
    - 새 IP 가 추가됐거나 (DNS 변경 의심), 추가된 IP 가 사설이면 차단.

    엄격 정책 — original_ips 의 superset 도 차단 (TTL 짧게 회전하는 정상 CDN 도 영향
    받을 수 있으나, MVP 단일 사용자 시나리오에서 보안 우선).
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "recheck: 호스트 없음"

    # 직접 IP 입력 → DNS resolve 안 함 (변경 가능성 없음 → 통과).
    try:
        ipaddress.ip_address(host)
        return True, "OK (직접 IP 입력 — recheck 불필요)"
    except ValueError:
        pass

    new_ips, resolve_error = _resolve_all(host)
    if resolve_error:
        return False, f"recheck DNS 실패: {resolve_error}"
    new_ip_strs = frozenset(str(ip) for ip in new_ips)

    # 1) 기존 집합과 정확히 동일한지 확인 — 다르면 rebinding 의심.
    if new_ip_strs != original_ips:
        added = new_ip_strs - original_ips
        removed = original_ips - new_ip_strs
        return (
            False,
            f"DNS rebinding 의심: 검증 시점 IP={sorted(original_ips)} "
            f"→ fetch 직후 IP={sorted(new_ip_strs)} "
            f"(추가={sorted(added)}, 제거={sorted(removed)})",
        )

    # 2) 재조회 결과 자체에 사설 IP 가 섞였는지 (혹시 검증 단계에서 놓친 케이스).
    for ip_obj in new_ips:
        verdict = _classify_ip(ip_obj)
        if verdict is not None:
            return False, f"recheck: {verdict}"

    return True, "OK"


# ---------------------- 내부 헬퍼 ----------------------


def _resolve_all(host: str) -> tuple[list[_IpAddr], str | None]:
    """host 를 모든 IP 로 resolve. (ip_list, error_msg) — 에러 시 ip_list 빈 list.

    - host 가 직접 IP 면 단일 원소 list.
    - 도메인이면 `socket.getaddrinfo` 로 모든 응답 (IPv4 + IPv6 혼재 가능).
    """
    try:
        ip_obj = ipaddress.ip_address(host)
        return [ip_obj], None
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return [], f"DNS 조회 실패: {host} ({exc})"

    seen: set[str] = set()
    ips: list[_IpAddr] = []
    for info in infos:
        raw_ip = info[4][0]
        if raw_ip in seen:
            continue
        seen.add(raw_ip)
        try:
            ips.append(ipaddress.ip_address(raw_ip))
        except ValueError as exc:
            return [], f"DNS 결과 IP 파싱 실패: {exc}"
    return ips, None


def _classify_ip(ip_obj: _IpAddr) -> str | None:
    """IP 가 차단 대상이면 사유 문자열, 아니면 None."""
    if ip_obj.is_loopback:
        return f"loopback IP 차단: {ip_obj}"
    if ip_obj.is_private:
        return f"private IP 차단: {ip_obj}"
    if ip_obj.is_link_local:
        return f"link-local IP 차단: {ip_obj} (예: 169.254.0.0/16 메타데이터)"
    if ip_obj.is_reserved or ip_obj.is_multicast:
        return f"reserved/multicast IP 차단: {ip_obj}"
    return None
