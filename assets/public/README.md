# `assets/public/` — 공개 라이센스 fixture

## 목적

Jet-Rag 의 **테스트 정확도 보강 (E2 sprint)** 을 위한 공개 라이센스 자료 fixture 디렉토리. `git pull` 만으로 모든 컴퓨터·CI 에 자동 동기화되어, mock + 메모리 합성 binary 로는 잡히지 않는 회귀를 실 파일로 보호한다.

`/assets/` 직속(private) 자료는 `.gitignore` 의 `/assets/*` 패턴으로 ignore 되며, 본 `public/` 하위만 negative pattern (`!/assets/public/**`) 으로 추적된다.

## 현재 자료 (3건, 총 약 10.6 MB)

| 파일 | 크기 | 라이센스 | 출처 / 발행기관 |
|---|---|---|---|
| `(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf` | 1.0 MB | KOGL 1유형 (출처표시) | 산업통상자원부 추정 — 2025년 사업 통합 안내서 (정부 공공데이터) |
| `보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf` | 951 KB | KOGL 1유형 (출처표시) | 보건복지부 — 보건의료 빅데이터 플랫폼 시범사업 추진계획(안) |
| `sample-report.pdf` | 8.6 MB | 사용자 명시 공공데이터 | 사용자가 본 디렉토리 이전 시 "공공데이터" 명시 |

라이센스 한 줄 인용:

- **KOGL 1유형**: 공공누리 제1유형 (출처표시) — <https://www.kogl.or.kr/info/license.do>

## 새 자료 추가 절차

새 자료를 본 디렉토리에 추가할 때는 다음 순서를 반드시 지킨다.

1. **라이센스 검토** — 다음 중 하나에 해당해야 한다.
   - KOGL 1유형 (출처표시) 또는 그 이상으로 자유로운 공공누리 유형
   - Creative Commons (CC0 / CC BY / CC BY-SA 등)
   - 퍼블릭 도메인 (저작권 만료, 정부 저작물 중 라이센스 명시 자료 등)
   - 사용자가 명시적으로 "공공데이터" 임을 보증한 자료
2. **파일 이동** — `assets/public/` 으로 복사 또는 이동
3. **본 README 표 갱신** — 파일명·크기·라이센스·출처를 추가
4. **영향 테스트 갱신** — 신규 자료를 사용할 unit test (예: `api/tests/test_pymupdf_heading.py`) 의 `_PDF_FILES` 또는 동등 fixture 목록에 추가
5. **회귀 검증** — `cd api && uv run python -m unittest discover tests` 통과 확인

## 비공개 자료 — `assets/` 직속

라이센스 검토 미완 / 사용자 사적 자료 / 회사 자료 등은 `assets/` 직속에 두면 `.gitignore` 의 `/assets/*` 패턴이 자동 ignore 한다 (단 `/assets/public/` 만 예외).

사용자 PC 에서 정밀 회귀를 돌리려면 ENV 로 경로를 추가 지정한다.

- `JETRAG_TEST_PDF_DIR`  — `test_pymupdf_heading.py` 가 추가 PDF 자료를 찾을 베이스 디렉토리
- `JETRAG_TEST_HWPX_DIR` — `test_hwpx_heading.py` 가 HWPX 자료를 찾을 베이스 디렉토리

ENV 가 없으면 해당 테스트 케이스는 자동 skip 된다 (CI 안전).

## 다른 컴퓨터 진입

`git pull` 만으로 본 디렉토리가 동기화된다. 별도 LFS·외부 storage 의존성 없음. 단위 테스트 (`uv run python -m unittest discover tests`) 가 `assets/public/` 의 자료를 자동 발견 → 회귀 자동 보호.

직속 비공개 자료는 컴퓨터별로 따로 보유한다 (`scp` 또는 사용자 클라우드).
