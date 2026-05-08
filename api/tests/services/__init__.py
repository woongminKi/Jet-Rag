"""S3 D1 — services 단위 테스트 서브패키지.

`unittest discover tests` 가 본 패키지를 재귀 탐색해 `test_*.py` 를 수집한다.
서브패키지로 분리한 의도 — service 계층 테스트가 7~8 개 누적 시 flat tests/
폴더의 시각 노이즈 회피 (S3 sprint 진입 기준 services 18개).
"""
