# `assets/public/` — 공개 라이센스 fixture

## 목적

Jet-Rag 의 **테스트 정확도 보강 (E2 sprint)** 을 위한 공개 라이센스 자료 fixture 디렉토리. `git pull` 만으로 모든 컴퓨터·CI 에 자동 동기화되어, mock + 메모리 합성 binary 로는 잡히지 않는 회귀를 실 파일로 보호한다.

`/assets/` 직속(private) 자료는 `.gitignore` 의 `/assets/*` 패턴으로 ignore 되며, 본 `public/` 하위만 negative pattern (`!/assets/public/**`) 으로 추적된다.

## 현재 자료 (8건, 총 약 11 MB)

| 파일 | 형식 | 크기 | 라이센스 | 출처 / 발행기관 |
|---|---|---|---|---|
| `(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf` | PDF | 1.0 MB | KOGL 1유형 (출처표시) | 산업통상자원부 추정 — 2025년 사업 통합 안내서 (정부 공공데이터) |
| `보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf` | PDF | 951 KB | KOGL 1유형 (출처표시) | 보건복지부 — 보건의료 빅데이터 플랫폼 시범사업 추진계획(안) |
| `sample-report.pdf` | PDF | 8.6 MB | 사용자 명시 공공데이터 | 사용자가 본 디렉토리 이전 시 "공공데이터" 명시 |
| `law sample3.pdf` | PDF | 258 KB | 한국 저작권법 §7 (비보호) | 대법원 2026. 2. 26. 선고 2025다215255 판결 (소멸시효) |
| `law_sample2.pdf` | PDF | 165 KB | 한국 저작권법 §7 (비보호) | 대법원 두-2025-35490 결정 (상속증여세 평가) |
| `직제_규정(2024.4.30.개정).hwpx` | HWPX | 76 KB | KOGL 1유형 추정 (출처표시) | 대전광역시시설관리공단 — 직제 규정 |
| `한마음생활체육관_운영_내규(2024.4.30.개정).hwpx` | HWPX | 48 KB | KOGL 1유형 추정 (출처표시) | 대전광역시시설관리공단 — 한마음생활체육관 운영 내규 |
| `law_sample1.hwp` | HWP | 48 KB | 한국 저작권법 §7 (비보호) | 대법원 두-2025-34754 결정 (국승, 세목) |

라이센스 한 줄 인용:

- **KOGL 1유형**: 공공누리 제1유형 (출처표시) — <https://www.kogl.or.kr/info/license.do>
- **한국 저작권법 §7**: "헌법·법률·조약·법령·조례·규칙 및 국가나 지방자치단체의 고시·공고·훈령 등 그 밖의 이와 유사한 것" 및 "법원의 판결·결정·명령 및 심판이나 행정심판절차 그 밖의 이와 유사한 절차에 의한 의결·결정 등" 은 저작권 보호 대상에서 제외됨. 대법원 판결문·결정문은 자유 이용 가능.

## 새 자료 추가 절차

새 자료를 본 디렉토리에 추가할 때는 다음 순서를 반드시 지킨다.

1. **라이센스 검토** — 다음 중 하나에 해당해야 한다.
   - KOGL 1유형 (출처표시) 또는 그 이상으로 자유로운 공공누리 유형
   - Creative Commons (CC0 / CC BY / CC BY-SA 등)
   - 퍼블릭 도메인 (저작권 만료, 정부 저작물 중 라이센스 명시 자료 등)
   - 한국 저작권법 §7 비보호 자료 (대법원 판결·결정문, 법령, 행정 고시 등)
   - 사용자가 명시적으로 "공공데이터" 임을 보증한 자료
2. **파일 이동** — `assets/public/` 으로 복사 또는 이동
3. **본 README 표 갱신** — 파일명·형식·크기·라이센스·출처를 추가 (의무)
4. **영향 테스트 갱신** — 신규 자료를 사용할 unit test 의 fixture 변수 갱신 (senior-developer 의무)
   - PDF: `api/tests/test_pymupdf_heading.py` `_PUBLIC_PDF_FILES`
   - HWPX: `api/tests/test_hwpx_heading.py` `_PUBLIC_HWPX_FILES`
   - HWP: `api/tests/test_hwp_heading.py` `_PUBLIC_HWP_FILES`
5. **회귀 검증** — `cd api && uv run python -m unittest discover tests` 통과 확인

## 비공개 자료 — `assets/` 직속

라이센스 검토 미완 / 사용자 사적 자료 / 회사 자료 등은 `assets/` 직속에 두면 `.gitignore` 의 `/assets/*` 패턴이 자동 ignore 한다 (단 `/assets/public/` 만 예외).

사용자 PC 에서는 다음 5단계 우선순위로 fixture 가 자동 해석되므로 ENV 0 줄로 자동 회귀 진입:

| 우선순위 | 위치 | 정합 정책 |
|---|---|---|
| 1 | `<repo>/assets/public/<name>` | git 추적 (모든 컴퓨터·CI 자동) |
| 2 | `<repo>/assets/<name>` | `.gitignore` `/assets/*` (사용자 PC raw) |
| 3 | `<repo>/<name>` | `.gitignore` `/*.{pdf,hwp,hwpx,docx,pptx}` (다른 컴퓨터 패턴) |
| 4 | `$JETRAG_TEST_*_DIR/<name>` | ENV 폴백 (외장 디스크) |
| 5 | 부재 → skipTest | CI 호환 |

ENV 변수 (4순위, 외장 디스크 등 보강 시):
- `JETRAG_TEST_PDF_DIR` — `test_pymupdf_heading.py`
- `JETRAG_TEST_HWPX_DIR` — `test_hwpx_heading.py`
- `JETRAG_TEST_HWP_DIR` — `test_hwp_heading.py`

## 다른 컴퓨터 진입

`git pull` 만으로 본 디렉토리가 동기화된다. 별도 LFS·외부 storage 의존성 없음.

| 시나리오 | 자동 동작 | 추가 조치 |
|---|---|---|
| 자료가 `<repo>/assets/` 직속에 있는 컴퓨터 (사용자 PC 패턴) | 2순위 자동 진입 | 0 |
| 자료가 `<repo>/` 루트 직속에 있는 컴퓨터 (다른 컴퓨터 패턴) | 3순위 자동 진입 | 0 |
| 자료가 외장 디스크·별 위치 | ENV 폴백 | `export JETRAG_TEST_PDF_DIR=<dir>` 1줄 |
| 자료가 없는 컴퓨터 (CI 포함) | public 만 회귀, private 자동 skip | 0 |

직속 비공개 자료를 다른 컴퓨터로 옮기는 경우 — `scp` / 사용자 클라우드 / 인제스트 후 폐기 중 택1.
