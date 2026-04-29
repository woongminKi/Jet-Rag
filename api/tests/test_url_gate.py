"""W3 v0.5 §3.E (Day 4) — URL gate 강화 단위 테스트.

검증 범위
- 3-tuple 반환 시그니처 — (safe, reason, resolved_ips)
- multi-IP round-robin attack 차단 — 모든 resolved IP 검증
- recheck_dns_consistency — 검증/fetch 사이 DNS 변경 시 차단
- 직접 IP 입력 시 recheck 통과 (DNS resolve 안 함)
- 기존 v0.4 동작 회귀 차단 (스킴/명시 차단 이름/사설 IP)

stdlib unittest + getaddrinfo mock — 외부 네트워크 호출 0.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


class ValidateUrlSafetyShapeTest(unittest.TestCase):
    """3-tuple 반환 시그니처 — (bool, str, frozenset[str])."""

    def test_returns_three_tuple(self) -> None:
        from app.routers._url_gate import validate_url_safety

        result = validate_url_safety("https://1.1.1.1/")
        self.assertEqual(len(result), 3)
        safe, reason, ips = result
        self.assertIsInstance(safe, bool)
        self.assertIsInstance(reason, str)
        self.assertIsInstance(ips, frozenset)

    def test_invalid_scheme_returns_empty_ips(self) -> None:
        from app.routers._url_gate import validate_url_safety

        safe, reason, ips = validate_url_safety("file:///etc/passwd")
        self.assertFalse(safe)
        self.assertIn("스킴", reason)
        self.assertEqual(ips, frozenset())

    def test_blocked_name_returns_empty_ips(self) -> None:
        from app.routers._url_gate import validate_url_safety

        safe, reason, ips = validate_url_safety("http://localhost/x")
        self.assertFalse(safe)
        self.assertIn("내부 호스트", reason)
        self.assertEqual(ips, frozenset())


class DirectIpInputTest(unittest.TestCase):
    """직접 IP 입력 — DNS resolve 안 거치고 IP 그대로 검증."""

    def test_public_ip_passes(self) -> None:
        from app.routers._url_gate import validate_url_safety

        safe, reason, ips = validate_url_safety("https://1.1.1.1/path")
        self.assertTrue(safe, f"공인 IP 차단됨: {reason}")
        self.assertEqual(ips, frozenset({"1.1.1.1"}))

    def test_private_ip_blocked(self) -> None:
        from app.routers._url_gate import validate_url_safety

        for ip in ("10.0.0.1", "192.168.1.1", "172.16.0.1"):
            with self.subTest(ip=ip):
                safe, reason, _ = validate_url_safety(f"https://{ip}/")
                self.assertFalse(safe, f"사설 IP {ip} 통과")
                self.assertIn("private", reason.lower())

    def test_link_local_metadata_ip_blocked(self) -> None:
        """169.254.169.254 — AWS/GCP 메타데이터 endpoint.

        ipaddress 표준 라이브러리에서 169.254/16 은 is_private + is_link_local 모두 True.
        _classify_ip 의 분기 순서상 is_private 이 먼저 매칭되어 "private" 사유로 차단됨.
        차단 자체가 정상 — 사유 문자열은 "private" 또는 "link-local" 둘 다 허용.
        """
        from app.routers._url_gate import validate_url_safety

        safe, reason, _ = validate_url_safety("http://169.254.169.254/")
        self.assertFalse(safe)
        # 169.254/16 은 ipaddress 가 private + link-local 둘 다 True 로 분류 → 어느 사유든 OK.
        self.assertTrue(
            "link-local" in reason.lower() or "private" in reason.lower(),
            f"메타데이터 IP 차단 사유가 예상과 다름: {reason}",
        )


class MultiIpRoundRobinAttackTest(unittest.TestCase):
    """multi-IP round-robin attack — 응답 IP 중 하나라도 사설이면 차단."""

    def _mock_getaddrinfo(self, ips: list[str]):
        """getaddrinfo 의 응답 형태 mock — [(family, type, proto, canonname, (ip, port))]."""
        return [(2, 1, 6, "", (ip, 0)) for ip in ips]

    def test_all_public_ips_pass(self) -> None:
        from app.routers import _url_gate

        with patch.object(
            _url_gate.socket, "getaddrinfo",
            return_value=self._mock_getaddrinfo(["1.1.1.1", "8.8.8.8"]),
        ):
            safe, reason, ips = _url_gate.validate_url_safety(
                "https://example.test/"
            )
            self.assertTrue(safe, f"전부 공인 IP 인데 차단: {reason}")
            self.assertEqual(ips, frozenset({"1.1.1.1", "8.8.8.8"}))

    def test_mixed_public_and_private_blocked(self) -> None:
        """공인 + 사설 혼재 → 차단 (round-robin attack).

        169.254/16 은 ipaddress 가 private + link-local 둘 다 True 로 분류.
        _classify_ip 분기 순서상 is_private 이 먼저 매칭되어 "private" 사유 반환.
        """
        from app.routers import _url_gate

        with patch.object(
            _url_gate.socket, "getaddrinfo",
            return_value=self._mock_getaddrinfo(
                ["1.1.1.1", "169.254.169.254"]
            ),
        ):
            safe, reason, ips = _url_gate.validate_url_safety(
                "https://attacker.test/"
            )
            self.assertFalse(safe)
            self.assertTrue(
                "link-local" in reason.lower() or "private" in reason.lower(),
                f"공인+사설 혼재 차단 사유 예상과 다름: {reason}",
            )
            # IP 집합은 두 개 모두 포함 (진단용)
            self.assertEqual(
                ips, frozenset({"1.1.1.1", "169.254.169.254"})
            )

    def test_mixed_public_and_loopback_blocked(self) -> None:
        from app.routers import _url_gate

        with patch.object(
            _url_gate.socket, "getaddrinfo",
            return_value=self._mock_getaddrinfo(["1.1.1.1", "127.0.0.1"]),
        ):
            safe, reason, _ = _url_gate.validate_url_safety(
                "https://attacker.test/"
            )
            self.assertFalse(safe)
            self.assertIn("loopback", reason.lower())


class RecheckDnsConsistencyTest(unittest.TestCase):
    """DNS rebinding 방어 — 검증 시점 IP 와 fetch 직후 IP 비교."""

    def _mock_getaddrinfo(self, ips: list[str]):
        return [(2, 1, 6, "", (ip, 0)) for ip in ips]

    def test_same_ip_set_passes(self) -> None:
        """recheck 결과가 원본과 동일하면 통과."""
        from app.routers import _url_gate

        with patch.object(
            _url_gate.socket, "getaddrinfo",
            return_value=self._mock_getaddrinfo(["1.1.1.1"]),
        ):
            ok, reason = _url_gate.recheck_dns_consistency(
                "https://example.test/", frozenset({"1.1.1.1"})
            )
            self.assertTrue(ok, f"동일 IP 인데 차단: {reason}")

    def test_changed_ip_set_blocks_rebinding(self) -> None:
        """검증 시점 1.1.1.1 → fetch 후 169.254.169.254 — rebinding 의심 → 차단."""
        from app.routers import _url_gate

        with patch.object(
            _url_gate.socket, "getaddrinfo",
            return_value=self._mock_getaddrinfo(["169.254.169.254"]),
        ):
            ok, reason = _url_gate.recheck_dns_consistency(
                "https://example.test/", frozenset({"1.1.1.1"})
            )
            self.assertFalse(ok)
            self.assertIn("rebinding", reason.lower())

    def test_added_safe_ip_still_blocks(self) -> None:
        """엄격 정책 — original 의 superset 이어도 차단 (TTL 회전 CDN 도 영향)."""
        from app.routers import _url_gate

        with patch.object(
            _url_gate.socket, "getaddrinfo",
            return_value=self._mock_getaddrinfo(["1.1.1.1", "8.8.8.8"]),
        ):
            ok, reason = _url_gate.recheck_dns_consistency(
                "https://example.test/", frozenset({"1.1.1.1"})
            )
            self.assertFalse(ok)
            self.assertIn("rebinding", reason.lower())

    def test_direct_ip_input_skips_recheck(self) -> None:
        """직접 IP 입력은 DNS resolve 안 거치므로 recheck 자체가 무의미 → 통과."""
        from app.routers._url_gate import recheck_dns_consistency

        ok, reason = recheck_dns_consistency(
            "https://1.1.1.1/", frozenset({"1.1.1.1"})
        )
        self.assertTrue(ok)
        self.assertIn("직접 IP", reason)


if __name__ == "__main__":
    unittest.main()
