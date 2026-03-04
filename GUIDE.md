# 📚 upbit-trader 완전 이해 가이드
### — 코딩을 전혀 몰라도 시스템을 이해하고, 직접 수정하고, 운영할 수 있도록 —

> 이 문서는 단순한 사용 설명서가 아닙니다. 이 봇이 **왜**, **어떻게** 작동하는지부터,
> 여러분이 직접 **전략을 바꾸고**, **리스크를 조정하고**, **문제를 해결**할 수 있도록
> 개발 지식 없이도 시스템 전체를 이해하는 것을 목표로 합니다.

---

## 목차

### 1부 — 이 시스템이 무엇인가
1. [이 봇은 어떤 존재인가?](#1-이-봇은-어떤-존재인가)
2. [거대한 그림: 5가지 핵심 역할](#2-거대한-그림-5가지-핵심-역할)
3. [폴더 구조 — 각 파일이 하는 일](#3-폴더-구조--각-파일이-하는-일)

### 2부 — 봇이 결정을 내리는 방법
4. [데이터 흐름: 봇이 매수/매도를 결정하는 과정](#4-데이터-흐름-봇이-매수매도를-결정하는-과정)
5. [현재 전략: TFVB — 트리플 스크린 변동성 돌파](#5-현재-전략-tfvb--트리플-스크린-변동성-돌파)
6. [비활성 전략: 변동성 돌파 / RSI 볼린저 / MACD](#6-비활성-전략-변동성-돌파--rsi-볼린저--macd)
7. [리스크 관리: 4가지 안전장치](#7-리스크-관리-4가지-안전장치)
8. [paper 모드 vs live 모드의 기술적 차이](#8-paper-모드-vs-live-모드의-기술적-차이)

### 3부 — 설정 파일 수정하기
9. [strategies.yaml 완전 해설](#9-strategiesyaml-완전-해설)
10. [risk.yaml 완전 해설](#10-riskyaml-완전-해설)
11. [.env 파일 완전 해설](#11-env-파일-완전-해설)

### 4부 — 텔레그램으로 봇 제어하기 (외부에서)
12. [텔레그램 명령어 전체 목록](#12-텔레그램-명령어-전체-목록)
13. [전략 파라미터 실시간 변경](#13-전략-파라미터-실시간-변경)
14. [모드 전환 (paper ↔ live)](#14-모드-전환-paper--live)

### 5부 — 서버 운영
15. [서버 접속 방법](#15-서버-접속-방법)
16. [서비스 관리 (시작/중지/재시작/상태확인)](#16-서비스-관리-시작중지재시작상태확인)
17. [로그 읽기 — 봇이 지금 뭘 하는지 파악하기](#17-로그-읽기--봇이-지금-뭘-하는지-파악하기)
18. [텔레그램 알림 해석하기](#18-텔레그램-알림-해석하기)
19. [코드 업데이트 방법](#19-코드-업데이트-방법)

### 6부 — 피드백과 수정
20. [전략 파라미터 조정하기](#20-전략-파라미터-조정하기)
21. [리스크 한도 조정하기](#21-리스크-한도-조정하기)
22. [거래 코인 추가/제거하기](#22-거래-코인-추가제거하기)
23. [자주 발생하는 문제와 해결법 (FAQ)](#23-자주-발생하는-문제와-해결법-faq)
24. [빠른 참조 카드](#24-빠른-참조-카드)

---

# 1부 — 이 시스템이 무엇인가

---

## 1. 이 봇은 어떤 존재인가?

### 한 줄 요약
> **24시간 쉬지 않고 암호화폐 시세를 감시하다가, 정해진 규칙에 따라 자동으로 매수/매도하는 프로그램.**

---

### 쉬운 비유: "자동 주식 트레이더"

여러분이 직접 거래한다면 이런 과정을 거칩니다.

```
1. 매일 아침 어제 비트코인 가격 변동폭을 확인한다.
2. "오늘 기준가 = 오늘 시가 + 어제 변동폭 × 0.5" 를 계산한다.
3. 현재가가 기준가를 넘으면 → 매수!
4. 리스크 한도 초과 여부 확인 후 실제 주문을 넣는다.
5. 텔레그램으로 결과를 보고한다.
```

이 봇은 이 과정을 **1분(live)~5초(paper) 주기로 자동으로** 반복합니다. 여러분은 자다가도 텔레그램 알림을 받을 수 있습니다.

---

### 현재 시스템 상태

| 항목 | 현재 설정 |
|------|-----------|
| 실행 모드 | **paper** (가상 거래, 텔레그램 /mode 로 전환 가능) |
| 가상 초기 자본 | **1,000,000 KRW** (100만원) |
| 감시 코인 | **KRW-ETH** (이더리움만) ← BTC는 장기 보유용으로 제외 |
| 전략 | **TFVB** — 트리플 스크린 변동성 돌파 (EMA+RSI+돌파) |
| 슬리피지 | **0.05%** (실제 네트워크 지연 반영, 보수적) |
| 서버 | Oracle Cloud (168.107.52.195, 한국 춘천) |
| 주기 | 5초마다 한 번씩 판단 (live 모드는 60초) |

---

## 2. 거대한 그림: 5가지 핵심 역할

이 시스템은 크게 5가지 역할이 서로 협력합니다. 마치 회사의 부서처럼 생각하세요.

```
┌─────────────────────────────────────────────────────────────┐
│                    upbit-trader 시스템                       │
│                                                             │
│  ① 데이터 수집부  →  ② 전략팀  →  ③ 리스크팀  →  ④ 실행팀   │
│  (업비트 API)       (신호 생성)   (안전 검토)   (주문 처리)   │
│                                                             │
│                 ↕ 모든 부서가 보고                           │
│           ⑤ 데이터베이스 + 텔레그램                          │
└─────────────────────────────────────────────────────────────┘
```

### ① 데이터 수집부 — `src/api/upbit_client.py`
- 업비트 API에서 매 주기마다 최신 캔들(봉차트) 데이터를 가져옵니다.
- 최근 100개의 일봉(또는 시간봉) 데이터를 수집합니다.
- Upbit API에는 `high_price`, `low_price`, `opening_price` 같은 형식으로 데이터가 옵니다.

### ② 전략팀 — `src/strategy/`
- 수집된 데이터를 보고 "**지금 사야 하나, 팔아야 하나, 기다려야 하나?**"를 판단합니다.
- 현재 활성화된 전략: **TFVB** (trend_filtered_breakout) — EMA추세 + RSI모멘텀 + 변동성 돌파 3중 검증
- 결과물: `BUY 신호`, `SELL 신호`, 또는 `HOLD (관망)` 신호

### ③ 리스크팀 — `src/risk/`
- 전략팀이 "사자!"고 해도 **안전 기준을 초과하면 거부**합니다.
- 4가지 안전 규칙을 검사합니다 (상세 내용은 섹션 7 참조).
- 결과물: `APPROVE (승인)` 또는 `REJECT (거부)`

### ④ 실행팀 — `src/execution/`
- 리스크팀이 승인하면 **실제 주문을 처리**합니다.
- paper 모드: 가상으로 처리 (실제 API 호출 없음)
- live 모드: 업비트 API로 실제 주문 전송

### ⑤ 기록/알림 — `src/data/database.py` + `src/notification/`
- 모든 거래를 SQLite 데이터베이스에 저장합니다.
- 중요 이벤트를 텔레그램으로 알림 발송합니다.

---

## 3. 폴더 구조 — 각 파일이 하는 일

```
upbit-trader/
│
├── .env                    ← ⭐ API 키, 거래 모드 설정 (가장 중요)
├── GUIDE.md                ← 지금 읽고 있는 이 파일
│
├── src/                    ← 모든 소스 코드
│   │
│   ├── main.py             ← 봇의 시작점. 모든 것을 조립하고 실행
│   │
│   ├── config/             ← ⭐ 설정 파일들 (수정하는 곳)
│   │   ├── settings.py     ← .env 파일을 읽어오는 코드
│   │   ├── strategies.yaml ← 어떤 전략으로, 어떤 코인을, 어떤 설정으로?
│   │   └── risk.yaml       ← 리스크 한도 설정
│   │
│   ├── api/                ← 업비트 서버와 통신
│   │   └── upbit_client.py ← 시세 조회, 주문 접수
│   │
│   ├── strategy/           ← 매수/매도 판단 로직
│   │   ├── base.py         ← 전략의 공통 틀
│   │   ├── trend_filtered_breakout.py ← ⭐ 현재 사용 중인 전략 (TFVB)
│   │   ├── volatility_breakout.py  ← 비활성 전략 (구 버전)
│   │   ├── rsi_bollinger.py        ← 비활성 전략
│   │   └── macd_momentum.py        ← 비활성 전략
│   │
│   ├── risk/               ← 안전 규칙 (손실 방지)
│   │   ├── engine.py       ← 4가지 규칙을 순서대로 검사
│   │   └── rules/
│   │       ├── max_position_size.py    ← 규칙1: 한 코인에 너무 많이 투자 금지
│   │       ├── daily_loss_limit.py     ← 규칙2: 하루 손실 한도
│   │       ├── mdd_circuit_breaker.py  ← 규칙3: 전체 손실 한도 (서킷브레이커)
│   │       └── consecutive_loss.py     ← 규칙4: 연속 손실 시 거래 중단
│   │
│   ├── execution/          ← 주문 처리
│   │   ├── backtest_executor.py  ← paper 모드용 (가상 거래)
│   │   └── live_executor.py      ← live 모드용 (실제 거래)
│   │
│   ├── core/
│   │   └── trading_engine.py  ← ⭐ 전체 루프 조율 (심장부)
│   │
│   ├── data/
│   │   ├── database.py     ← SQLite 데이터베이스 관리
│   │   └── models.py       ← 거래 기록 데이터 구조
│   │
│   ├── indicators/
│   │   └── technical.py    ← RSI, 볼린저밴드 등 기술적 지표 계산
│   │
│   └── notification/
│       ├── telegram_bot.py    ← 텔레그램 메시지 발송 (알림 메서드 모음)
│       └── command_handler.py ← ⭐ 텔레그램 명령어 수신/처리 (/mode, /set 등)
│
├── tests/                  ← 자동 테스트 (234개 통과)
├── scripts/
│   └── deploy.sh           ← 서버 배포 스크립트
└── data/
    └── trading.db          ← 거래 기록 저장 파일 (서버에만 있음)
```

---

# 2부 — 봇이 결정을 내리는 방법

---

## 4. 데이터 흐름: 봇이 매수/매도를 결정하는 과정

봇이 한 번 "깨어나면" (한 사이클) 다음 순서로 움직입니다.

```
[1단계] 업비트 API 호출
         ↓
   "KRW-BTC 최근 100일 일봉 데이터를 가져와줘"
   → 오픈가, 고가, 저가, 종가, 거래량 (100개 행)
         ↓
[2단계] 데이터 정규화
         ↓
   업비트는 한국어로 돌려줌 (high_price, low_price...)
   → 전략이 쓸 수 있게 영어로 바꿈 (high, low, open, close, volume)
         ↓
[3단계] 전략 판단 (volatility_breakout)
         ↓
   어제 데이터:  고가 = 95,000,000원 / 저가 = 93,000,000원
   어제 변동폭:  95,000,000 - 93,000,000 = 2,000,000원
   오늘 시가:    93,500,000원
   목표가:       93,500,000 + 2,000,000 × 0.5 = 94,500,000원
   현재가:       94,600,000원

   현재가 94,600,000 >= 목표가 94,500,000 → ✅ BUY 신호!
         ↓
[4단계] 리스크 검사 (4가지 규칙)
         ↓
   규칙1: 한 코인 비중 20% 이하? → ✅
   규칙2: 오늘 하루 손실 5% 이하? → ✅
   규칙3: 전체 손실 15% 이하? → ✅
   규칙4: 연속 손실 5번 미만? → ✅
   → 전부 통과 → APPROVE!
         ↓
[5단계] 주문 실행 (paper 모드)
         ↓
   가상 잔고에서 95만원 차감
   0.01 BTC 매수 기록
   수수료 0.05% 적용 (475원)
         ↓
[6단계] 기록 & 알림
         ↓
   SQLite DB에 거래 저장
   텔레그램으로 매수 알림 발송
```

---

## 5. 현재 전략: TFVB — 트리플 스크린 변동성 돌파

TFVB(Trend-Filtered Volatility Breakout)는 단순 변동성 돌파에 **추세 필터**와 **모멘텀 게이트**를 추가해 손실을 최소화하도록 설계된 전략입니다. 3개의 독립적인 조건이 **모두 동시에** 참일 때만 매수합니다.

> **핵심 철학: "돈을 덜 버는 것보다 잃지 않는 것이 우선"**

---

### 스크린 1: EMA 추세 필터

```
조건: EMA(20) > EMA(60)

EMA(N) = 지수이동평균 — 최근 N일 종가의 가중 평균
         최근 데이터일수록 더 높은 가중치 부여
```

- **EMA(20)**: 최근 20일 단기 추세
- **EMA(60)**: 최근 60일 중기 추세
- **EMA(20) > EMA(60)** = 단기 추세가 중기 추세보다 강함 = **상승 추세**

| 상황 | EMA20 vs EMA60 | 판단 |
|------|---------------|------|
| 상승장 | EMA20 **>** EMA60 | ✅ 스크린 1 통과 |
| 하락장 | EMA20 **<** EMA60 | ❌ 매수 금지 |

> **왜 중요한가?** 하락하는 시장에서 매수하면 계속 손실이 납니다. 이 필터가 하락장 진입을 원천 차단합니다. (근거: Faber 2007 "A Quantitative Approach to Tactical Asset Allocation")

---

### 스크린 2: RSI 모멘텀 게이트

```
조건: 45 ≤ RSI(14) ≤ 70

RSI = Relative Strength Index (상대강도지수)
      0~100 사이 숫자로 상승/하락 모멘텀 측정
```

- **RSI < 45**: 모멘텀이 약함 → 아직 추세가 시작되지 않았음 → 매수 금지
- **RSI 45~70**: 건강한 상승 모멘텀 → ✅ 매수 가능 구간
- **RSI > 70**: 과매수 상태 → 이미 너무 많이 오름 → 매수 금지

```
    0        45       70      100
    |--------|  매수  |--------|
   과매도  약한모멘텀  건강   과매수
    ❌          ❌       ✅      ❌
```

> **왜 중요한가?** 과매수 구간(RSI>70)에서 매수하면 고점에서 물리기 쉽습니다. 이 게이트가 고점 추격을 방지합니다. (근거: Wilder 1978)

---

### 스크린 3: 변동성 돌파 진입

```
조건: 현재가 ≥ 오늘 시가 + 어제 변동폭 × K값(0.4)

어제 변동폭 = 어제 고가 - 어제 저가
```

스크린 1, 2를 통과한 상태에서만 이 조건을 검사합니다.

**실제 이더리움 예시:**
```
어제 이더리움:
  고가: 3,100,000원 / 저가: 2,950,000원
  어제 변동폭: 150,000원

오늘:
  시가: 2,980,000원
  목표가: 2,980,000 + 150,000 × 0.4 = 3,040,000원

현재가 3,050,000원 → 목표가 초과 → ✅ 스크린 3 통과!
현재가 3,020,000원 → 목표가 미달 → ❌ 관망
```

**K값(0.4)의 의미:**
- K가 낮을수록 (0.3): 목표가 낮아져 신호 빈번 (공격적)
- **현재 K=0.4**: 균형 (구버전 0.5보다 10% 더 진입하기 쉬움)
- K가 높을수록 (0.6): 목표가 높아져 신호 드묾 (보수적)

> **텔레그램으로 즉시 변경 가능:** `/set trend_filtered_breakout k_value 0.35`

---

### 포지션 사이징: ATR 리스크 규칙

3개 스크린을 모두 통과하면, **얼마만큼 살지**를 ATR로 계산합니다.

```
ATR(14) = Average True Range = 최근 14일 평균 변동폭

리스크 예산 = 자본 × 1% (기본설정)
스톱 거리  = ATR × 2
포지션(KRW) = 리스크 예산 ÷ (스톱 거리 / 현재가)
최대 한도   = 자본의 20%
```

**실제 예시 (자본 100만원, ETH 3,000,000원, ATR=90,000원):**
```
리스크 예산  = 1,000,000 × 0.01 = 10,000원
스톱 거리    = 90,000 × 2 = 180,000원
코인 리스크  = 180,000 / 3,000,000 = 6%
포지션(KRW)  = 10,000 / 0.06 = 166,667원 → 17% 투자
```

> ATR이 클수록(변동성↑) 포지션이 작아지고, ATR이 작을수록(안정적) 포지션이 커집니다. 자동으로 시장 상황에 맞게 조절됩니다. (근거: Turtle Traders, Dennis & Eckhardt 1983)

---

### 이 전략의 특징

| 항목 | 설명 |
|------|------|
| **거래 대상** | KRW-ETH (이더리움만, BTC 제외) |
| **시간 기준** | 일봉 (하루 단위 판단) |
| **매도 시점** | 다음 날 시가에 자동 매도 (오버나이트 청산) |
| **K값** | 0.4 (실시간 변경: `/k 0.4`) |
| **1회 최대 투자** | 자본의 20% |
| **설계 목표** | 하락장 제외 + 과매수 제외 → 손실 최소화 |

---

## 6. 비활성 전략: 변동성 돌파 / RSI 볼린저 / MACD

`strategies.yaml`에 존재하지만 `enabled: false`로 꺼져 있습니다.

### 구 변동성 돌파 전략 (`volatility_breakout`)

- TFVB의 전신 버전: K값(0.5)만 적용한 단순 변동성 돌파
- 추세/모멘텀 필터 없음 → 하락장에서도 매수 → 손실 위험 높음
- **현재 비활성 (TFVB로 대체됨)**

### RSI 볼린저 밴드 전략 (`rsi_bollinger`)

- RSI 과매도 + 볼린저 하단 터치 → 반등 기대 매수
- 시간봉(1h) 기준으로 동작

### MACD 모멘텀 전략 (`macd_momentum`)

- 단기/장기 이동평균 차이(MACD) 상향 돌파 + 거래량 확인 → 매수
- 시간봉(1h) 기준으로 동작

---

## 7. 리스크 관리: 4가지 안전장치

리스크 엔진은 전략이 BUY/SELL 신호를 보내도 **4가지 규칙을 모두 통과해야** 실제 주문을 냅니다.
하나라도 걸리면 해당 신호는 자동으로 **거부(REJECT)**됩니다.

```
전략 → BUY 신호
         ↓
   [규칙1] 포지션 크기 한도 검사  → ❌ 거부 or ✅ 통과
         ↓
   [규칙2] 일일 손실 한도 검사    → ❌ 거부 or ✅ 통과
         ↓
   [규칙3] MDD 서킷브레이커 검사  → ❌ 거부 or ✅ 통과
         ↓
   [규칙4] 연속 손실 횟수 검사    → ❌ 거부 or ✅ 통과
         ↓
   4개 모두 통과 → 주문 실행 ✅
```

---

### 규칙 1: 포지션 크기 한도 (`max_position_size`)

**설정 파일:** `risk.yaml`

```yaml
max_single_asset_ratio: 0.20      # 한 코인에 최대 20%
max_total_investment_ratio: 0.70  # 전체 투자 비중 최대 70%
max_concurrent_positions: 5       # 동시 보유 종목 최대 5개
```

**예시:**
- 총 자산 100만원, BTC에 이미 19만원 투자 중
  - 19% < 20% → ✅ 통과
- 총 자산 100만원, BTC에 이미 21만원 투자 중
  - 21% > 20% → ❌ 거부! 추가 매수 안 됨

**왜 필요한가?**
한 코인에 모든 돈을 넣었다가 그 코인이 폭락하면 막대한 손실이 발생합니다. 이 규칙은 **달걀을 한 바구니에 담지 말라**는 원칙을 자동 적용합니다.

---

### 규칙 2: 일일 손실 한도 (`daily_loss_limit`)

**설정 파일:** `risk.yaml`

```yaml
daily_loss_limit_pct: 0.05  # 하루 최대 손실 5%
```

**예시:**
- 오늘 아침 자산: 100만원
- 현재까지 오늘 손실: 4만원 (4%)
  - 4% < 5% → ✅ 통과
- 현재까지 오늘 손실: 6만원 (6%)
  - 6% > 5% → ❌ 오늘은 더 이상 거래 없음

**왜 필요한가?**
어떤 날은 시장이 폭락하거나 전략이 연속으로 틀릴 수 있습니다. 하루 손실 한도를 설정하면 나쁜 날에 더 큰 손실을 막을 수 있습니다.

---

### 규칙 3: MDD 서킷브레이커 (`mdd_circuit_breaker`)

**설정 파일:** `risk.yaml`

```yaml
mdd_limit_pct: 0.15  # 고점 대비 최대 15% 하락 시 거래 중단
```

**MDD(Maximum DrawDown, 최대 낙폭)** = (최고 자산 - 현재 자산) ÷ 최고 자산

```
예시:
최고 자산(고점): 1,200,000원
현재 자산:       1,000,000원
MDD = (1,200,000 - 1,000,000) / 1,200,000 = 16.7%
→ 16.7% > 15% → ❌ 서킷브레이커 발동! 모든 거래 중단
```

**왜 필요한가?**
주식/코인 시장에서 "물타기"(손실 중에 계속 매수하기)는 큰 손실로 이어지는 흔한 실수입니다. 고점 대비 15%가 빠지면 전략이 환경에 맞지 않을 가능성이 높으므로 자동으로 중단합니다.

---

### 규칙 4: 연속 손실 보호 (`consecutive_loss_guard`)

**설정 파일:** `risk.yaml`

```yaml
max_consecutive_losses: 5  # 5번 연속 손실 시 거래 중단
```

**예시:**
- 거래 결과: 손실, 손실, 손실, 손실, 손실 (5연속)
- → ❌ 거래 중단 및 텔레그램 알림
- 거래 결과: 손실, 손실, 손실, 이익, 손실, 손실 (최대 2연속)
- → ✅ 계속 거래

**왜 필요한가?**
5번 연속 손실은 전략이 현재 시장 환경에 맞지 않는다는 신호일 수 있습니다. 잠시 멈추고 상황을 파악하는 것이 현명합니다.

---

## 8. paper 모드 vs live 모드의 기술적 차이

### paper 모드 (현재 설정)

```python
# backtest_executor.py
initial_capital = 1,000,000원  # 가상 자본
fee_rate = 0.0005               # 수수료 0.05% (실제와 동일하게 시뮬레이션)
slippage_rate = 0.0005          # 슬리피지 0.05% (네트워크 지연 반영, 보수적 설정)
```

- 업비트 API에 **주문 요청을 보내지 않습니다.**
- 내부 메모리에서 가상 잔고를 조작합니다.
- 수수료와 슬리피지까지 현실적으로 시뮬레이션합니다.
- 결과는 DB에 저장되지만 실제 돈과 무관합니다.

### live 모드

- 업비트 API에 **실제 주문**을 전송합니다.
- 여러분의 업비트 계정에 있는 실제 KRW가 사용됩니다.
- 체결된 주문은 업비트 앱에서도 볼 수 있습니다.

---

# 3부 — 설정 파일 수정하기

---

## 9. strategies.yaml 완전 해설

**파일 위치:** `~/upbit-trader/src/config/strategies.yaml`

이 파일은 **어떤 전략을 어떻게 실행할지** 정의합니다.

---

### 현재 설정 전체

```yaml
strategies:
  - name: trend_filtered_breakout  ← ⭐ 현재 활성 전략 (TFVB)
    enabled: true
    markets:
      - KRW-ETH                    ← 이더리움만 (BTC는 장기보유용으로 제외)
    timeframe: "1d"                ← 일봉 기준
    params:
      k_value: 0.4                 ← 돌파 강도 (텔레그램 /k 로 즉시 변경 가능)
      atr_risk_pct: 0.01           ← 1회 리스크 비율 1% (ATR 사이징)
      rsi_min: 45                  ← RSI 하한 (이 이상일 때만 매수)
      rsi_max: 70                  ← RSI 상한 (이 미만일 때만 매수)
      base_capital: 1000000        ← 리스크 계산 기준 자본 (100만원)

  - name: volatility_breakout      ← 구버전, 비활성
    enabled: false
    markets:
      - KRW-ETH
    ...

  - name: rsi_bollinger            ← 비활성 전략
    enabled: false
    ...

  - name: macd_momentum            ← 비활성 전략
    enabled: false
    ...
```

---

### 각 항목 상세 설명

#### `enabled` — 전략 켜기/끄기

```yaml
enabled: true   # 이 전략 사용
enabled: false  # 이 전략 사용 안 함
```

> 전략을 완전히 삭제하지 말고 `enabled: false`로 끄세요. 나중에 다시 켤 수 있습니다.

---

#### `markets` — 거래 코인 목록

업비트의 코인 코드 형식은 `KRW-코인심볼`입니다.

```yaml
markets:
  - KRW-BTC    # 비트코인
  - KRW-ETH    # 이더리움
  - KRW-SOL    # 솔라나 (추가 예시)
  - KRW-XRP    # 리플 (추가 예시)
```

> 코인 코드는 업비트 앱/웹에서 확인할 수 있습니다. 형식은 반드시 `KRW-대문자`여야 합니다.

---

#### `timeframe` — 캔들 기준 시간

```yaml
timeframe: "1d"   # 일봉 (하루 한 번 데이터)
timeframe: "1h"   # 시간봉 (한 시간 데이터)
timeframe: "15m"  # 15분봉
```

> 변동성 돌파 전략은 일봉(`1d`)이 가장 적합합니다. 시간봉으로 바꾸면 신호가 너무 자주 나올 수 있습니다.

---

#### `params.k_value` — K값 (변동성 돌파 강도)

| K값 | 특성 | 추천 시장 |
|-----|------|----------|
| 0.3 | 공격적, 신호 많음 | 강한 상승장 |
| **0.4** | **균형 (현재 설정)** | **일반적 상황** |
| 0.6 | 보수적, 신호 드묾 | 횡보장, 하락장 |

> ✨ **재시작 없이 즉시 변경 가능:** 텔레그램에서 `/k 0.35` 또는 `/set trend_filtered_breakout k_value 0.35`

#### `params.atr_risk_pct` — 1회 리스크 비율

- `0.01` = 자본의 1%를 최대 손실로 허용 (현재)
- `0.005` = 0.5%로 줄임 (더 보수적)
- `0.02` = 2%로 늘림 (더 공격적)

> ✨ **재시작 없이 즉시 변경:** `/set trend_filtered_breakout atr_risk_pct 0.005`

#### `params.rsi_min` / `params.rsi_max` — RSI 허용 범위

- 현재: 45~70 (이 범위 내에서만 매수)
- 넓히면 (40~75): 더 많은 신호 발생
- 좁히면 (50~65): 더 선택적인 진입

> ✨ **재시작 없이 즉시 변경:** `/set trend_filtered_breakout rsi_min 40`

---

### 설정 변경 방법 2가지

**방법 1: 텔레그램 명령어 (재시작 불필요, 즉시 적용)**
```
/set trend_filtered_breakout k_value 0.35
/set trend_filtered_breakout rsi_min 40
/k 0.35
```

**방법 2: 파일 수정 후 재시작 (영구 적용)**
```bash
# 서버에서 실행
sudo systemctl restart upbit-trader
```

---

## 10. risk.yaml 완전 해설

**파일 위치:** `~/upbit-trader/src/config/risk.yaml`

이 파일은 **손실을 막는 4가지 안전 규칙**을 설정합니다.

---

### 현재 설정 전체

```yaml
risk_rules:
  - name: max_position_size          ← 규칙1: 포지션 크기 한도
    enabled: true
    max_single_asset_ratio: 0.20     ← 한 코인 최대 20%
    max_total_investment_ratio: 0.70 ← 전체 투자 최대 70%
    max_concurrent_positions: 5      ← 동시 보유 최대 5종목

  - name: daily_loss_limit           ← 규칙2: 하루 손실 한도
    enabled: true
    daily_loss_limit_pct: 0.05       ← 하루 최대 5% 손실

  - name: mdd_circuit_breaker        ← 규칙3: 전체 손실 서킷브레이커
    enabled: true
    mdd_limit_pct: 0.15              ← 고점 대비 15% 하락 시 중단

  - name: consecutive_loss_guard     ← 규칙4: 연속 손실 보호
    enabled: true
    max_consecutive_losses: 5        ← 5연속 손실 시 중단
```

---

### 각 값의 의미와 조정 지침

#### `max_single_asset_ratio: 0.20`
- **현재**: 한 코인에 자산의 최대 20%까지만 투자
- **공격적으로**: 0.30 (30%까지 허용)
- **보수적으로**: 0.10 (10%까지만)
- 1보다 클 수 없습니다. 보통 0.10~0.30 범위

#### `daily_loss_limit_pct: 0.05`
- **현재**: 하루 5% 손실 시 그날 거래 중단
- **공격적으로**: 0.08 (8%까지 허용)
- **보수적으로**: 0.03 (3%에서 중단)
- 처음에는 보수적(0.03)으로 시작하는 것을 권장

#### `mdd_limit_pct: 0.15`
- **현재**: 고점 대비 15% 하락 시 모든 거래 중단
- **공격적으로**: 0.25 (25%까지 허용)
- **보수적으로**: 0.10 (10%에서 중단)
- 처음에는 보수적(0.10)으로 시작하는 것을 권장

#### `max_consecutive_losses: 5`
- **현재**: 5번 연속 손실 시 거래 중단
- **공격적으로**: 7 (7번까지 허용)
- **보수적으로**: 3 (3번 연속이면 중단)

---

## 11. .env 파일 완전 해설

**파일 위치 (서버):** `~/upbit-trader/.env`

이 파일에는 **비밀 정보**가 담겨 있습니다. 절대 다른 사람에게 보여주지 마세요!

```bash
UPBIT_ACCESS_KEY=9xcRhocF...    ← 업비트 API 접근 키 (공개키)
UPBIT_SECRET_KEY=jWyygoN8...    ← 업비트 API 비밀 키 (절대 노출 금지)
TELEGRAM_BOT_TOKEN=8625433...   ← 텔레그램 봇 토큰
TELEGRAM_CHAT_ID=5494304495     ← 알림 받을 텔레그램 채팅 ID
TRADING_MODE=paper              ← ⭐ paper 또는 live
LOG_LEVEL=INFO                  ← DEBUG(상세) / INFO(일반) / WARNING(경고만)
```

### 각 항목 설명

| 항목 | 설명 | 변경 가능? |
|------|------|----------|
| `UPBIT_ACCESS_KEY` | 업비트 API 공개 키 | 업비트에서 재발급 후 변경 |
| `UPBIT_SECRET_KEY` | 업비트 API 비밀 키 | 업비트에서 재발급 후 변경 |
| `TELEGRAM_BOT_TOKEN` | @GustjdBot 토큰 | BotFather에서 재발급 후 변경 |
| `TELEGRAM_CHAT_ID` | 알림 받을 채팅 ID | 변경 불필요 (내 ID) |
| `TRADING_MODE` | ⭐ **paper 또는 live** | 자주 변경 |
| `LOG_LEVEL` | 로그 상세도 | 문제 발생 시 DEBUG로 변경 |

---

# 4부 — 텔레그램으로 봇 제어하기 (외부에서)

서버에 접속하지 않고, **스마트폰에서 텔레그램 메시지만으로** 봇을 제어할 수 있습니다.

---

## 12. 텔레그램 명령어 전체 목록

`@GustjdBot`에게 아래 명령어를 보내세요. 본인의 채팅 ID(`5494304495`)에서 보낸 메시지만 처리됩니다.

### 📊 모니터링 명령어

| 명령어 | 결과 |
|--------|------|
| `/ping` | 봇 살아있는지 확인 + 현재 상태 표시 |
| `/status` | KRW 잔고 + 보유 포지션 현황 |
| `/strategy` | 활성 전략 목록 + 각 파라미터 |
| `/help` | 전체 명령어 목록 |

### ⚙️ 전략 제어 명령어

| 명령어 | 예시 | 설명 |
|--------|------|------|
| `/enable <전략명>` | `/enable trend_filtered_breakout` | 전략 활성화 |
| `/disable <전략명>` | `/disable volatility_breakout` | 전략 비활성화 |
| `/set <전략명> <파라미터> <값>` | `/set trend_filtered_breakout k_value 0.35` | 파라미터 즉시 변경 |
| `/k <값>` | `/k 0.4` | K값 일괄 변경 (범위: 0.1~0.9) |

**`/set` 으로 변경 가능한 파라미터:**
```
k_value      — 돌파 강도 (0.1~0.9, 현재 0.4)
rsi_min      — RSI 하한 (현재 45)
rsi_max      — RSI 상한 (현재 70)
atr_risk_pct — 1회 리스크 비율 (현재 0.01 = 1%)
```

> ⚠️ `/set`과 `/k`는 서버 재시작 없이 즉시 적용되지만, 재시작하면 파일의 원래 값으로 되돌아갑니다. 영구 변경은 `strategies.yaml` 수정 후 재시작이 필요합니다.

### 🎮 운영 제어 명령어

| 명령어 | 설명 |
|--------|------|
| `/mode paper` | 모의투자 모드로 전환 (가상 자금) |
| `/mode live` | 실거래 모드로 전환 (**실제 자금** ⚠️) |
| `/pause` | 거래 일시정지 (모니터링은 계속) |
| `/resume` | 거래 재개 |
| `/stop` | 봇 종료 (서버에서 재시작 필요) |

---

## 13. 전략 파라미터 실시간 변경

### K값 조절 예시

시장이 너무 조용해서 신호가 안 나올 때 (K를 낮춰서 더 쉽게 진입):
```
/k 0.3
```

시장이 너무 변동성이 커서 손실이 날 때 (K를 높여서 더 까다롭게 진입):
```
/k 0.5
```

### RSI 범위 조절 예시

모멘텀 기준을 완화해서 더 많은 진입 허용:
```
/set trend_filtered_breakout rsi_min 40
/set trend_filtered_breakout rsi_max 75
```

더 보수적으로 (과매수 직전만 진입):
```
/set trend_filtered_breakout rsi_min 50
/set trend_filtered_breakout rsi_max 65
```

### 리스크 비율 조절 예시

테스트 중이라 작게 투자하고 싶을 때:
```
/set trend_filtered_breakout atr_risk_pct 0.005
```

### 결과 확인

파라미터 변경 후 `/strategy`로 현재 설정 확인:
```
/strategy
```

---

## 14. 모드 전환 (paper ↔ live)

### 현재 모드 확인

```
/mode
```

응답 예시:
```
📡 [현재 모드] 모의투자

모드 전환:
  /mode live  — 실거래 (✅ 사용 가능)
  /mode paper — 모의투자 (항상 가능)

⚠️ live 모드는 실제 자금으로 거래됩니다.
```

### paper → live 전환

> 🚨 **전환 전 필수 체크리스트를 반드시 확인하세요 (섹션 23 참조)**

텔레그램에서:
```
/mode live
```

응답:
```
🔴 [모드 전환]
모의투자 → 실거래
⚠️ 실제 자금으로 거래합니다.
```

### live → paper 복귀

```
/mode paper
```

### 주의 사항

- **즉시 적용**: 다음 사이클부터 바뀐 executor로 거래합니다.
- **재시작 불필요**: 서버 재시작 없이 전환됩니다.
- **재시작 시 초기화**: 서버를 재시작하면 `.env`의 `TRADING_MODE` 값으로 돌아갑니다. live 모드를 영구 유지하려면 `.env`도 변경하세요.
- **live 사용 불가 조건**: 업비트 API 키가 설정되지 않으면 live 모드로 전환 불가 (에러 메시지 표시)

---

# 5부 — 서버 운영

---

## 15. 서버 접속 방법

### 1단계: Mac 터미널 열기

`Command(⌘) + Space` → `Terminal` 입력 → Enter

---

### 2단계: SSH 키 파일 권한 설정 (처음 한 번만)

```bash
chmod 400 ~/Downloads/ssh-key-2026-03-01.key
```

---

### 3단계: 서버 접속

```bash
ssh -i ~/Downloads/ssh-key-2026-03-01.key ubuntu@168.107.52.195
```

**명령어 분해:**
- `ssh` = Secure Shell (암호화된 원격 접속 프로그램)
- `-i ~/Downloads/ssh-key-2026-03-01.key` = 이 키 파일로 신원 증명
- `ubuntu` = 서버 계정 이름
- `168.107.52.195` = 서버 IP 주소

처음 접속 시 `yes` 입력 후 Enter. 이후 `ubuntu@...$ ` 프롬프트가 나타나면 성공.

---

### 서버에서 나가기

```bash
exit
```

봇은 서버에서 계속 실행됩니다. 나가도 봇은 꺼지지 않습니다.

---

## 16. 서비스 관리 (시작/중지/재시작/상태확인)

봇은 **systemd** 서비스로 등록되어 있습니다. systemd는 리눅스의 "프로그램 자동 관리자"입니다.
서버가 켜질 때 자동 시작, 봇이 죽으면 자동 재시작 등의 기능을 제공합니다.

---

### 상태 확인 (가장 먼저 할 것)

```bash
sudo systemctl status upbit-trader
```

**좋은 상태 (초록):**
```
● upbit-trader.service - Upbit Automated Trading System
   Active: active (running) since Sat 2026-03-01 09:00:00 KST; 2h ago
```

**나쁜 상태 (빨간):**
```
● upbit-trader.service - Upbit Automated Trading System
   Active: failed (Result: exit-code) since ...
```

화면에서 나가려면 `q` 키.

---

### 재시작

설정 변경 후 또는 봇이 이상할 때:

```bash
sudo systemctl restart upbit-trader
```

---

### 중지

```bash
sudo systemctl stop upbit-trader
```

---

### 시작

```bash
sudo systemctl start upbit-trader
```

---

### 명령어 요약표

| 원하는 것 | 명령어 |
|----------|--------|
| 상태 확인 | `sudo systemctl status upbit-trader` |
| 재시작 | `sudo systemctl restart upbit-trader` |
| 중지 | `sudo systemctl stop upbit-trader` |
| 시작 | `sudo systemctl start upbit-trader` |

---

## 17. 로그 읽기 — 봇이 지금 뭘 하는지 파악하기

로그는 봇이 매 사이클마다 기록하는 "업무 일지"입니다.

---

### 실시간 로그 보기

```bash
sudo journalctl -u upbit-trader -f
```

`-f` = follow (계속 새로운 줄 표시). `Ctrl+C` 로 중단.

---

### 최근 50줄만 보기

```bash
sudo journalctl -u upbit-trader -n 50
```

---

### 오늘 로그만 보기

```bash
sudo journalctl -u upbit-trader --since today
```

---

### 실제 로그 예시와 해석

```
# ① 봇이 시작될 때
2026-03-01 09:00:00 INFO  [main] ==================================================
2026-03-01 09:00:00 INFO  [main]   upbit-trader  |  mode: paper
2026-03-01 09:00:00 INFO  [main] ==================================================
2026-03-01 09:00:00 INFO  [main] Database initialised: sqlite+aiosqlite:///data/trading.db
2026-03-01 09:00:00 INFO  [main] UpbitClient initialised (mode=paper)
2026-03-01 09:00:00 INFO  [main] Strategy 'volatility_breakout' loaded (enabled=True, markets=['KRW-BTC', 'KRW-ETH'])
2026-03-01 09:00:00 INFO  [main] RiskEngine initialised with 4 rules.
→ 4개 규칙이 모두 로드됨

# ② 매 사이클 정상 동작 (5초마다)
2026-03-01 09:00:05 DEBUG [trading_engine] [volatility_breakout/KRW-BTC] signal=hold conf=0.50
2026-03-01 09:00:05 DEBUG [trading_engine] [volatility_breakout/KRW-ETH] signal=hold conf=0.50
→ 신호 없음 (HOLD). 정상입니다.

# ③ 매수 신호 발생
2026-03-01 14:30:00 DEBUG [trading_engine] [volatility_breakout/KRW-BTC] signal=buy conf=0.78
2026-03-01 14:30:00 INFO  [trading_engine] [volatility_breakout/KRW-BTC] buy order executed: qty=0.010010 @ 94600000 fee=473.00 id=a1b2c3d4
→ BTC 매수 체결! 수량 0.01010 BTC, 단가 94,600,000원, 수수료 473원

# ④ 리스크 규칙이 신호를 거부할 때
2026-03-01 15:00:00 INFO  [trading_engine] [volatility_breakout/KRW-ETH] Signal REJECTED by risk engine: ['Maximum drawdown 16% has breached circuit-breaker threshold 15%...']
→ MDD 서킷브레이커 발동! ETH 매수 거부됨

# ⑤ 텔레그램 전송 확인
2026-03-01 09:00:01 INFO  [telegram_bot] System start notification sent (HTTP 200)
→ 텔레그램 정상 전송

# ⑥ 오류
2026-03-01 09:05:00 ERROR [trading_engine] Failed to fetch candles for KRW-BTC/1d: timeout
→ 업비트 API 일시적 타임아웃. 5초 후 자동 재시도. 드물게 발생하면 정상.
```

---

### 로그 레벨 의미

| 레벨 | 의미 | 조치 |
|------|------|------|
| `DEBUG` | 상세 동작 기록 (매 사이클 신호 결과 등) | 없음 |
| `INFO` | 중요 이벤트 (시작, 체결, 거부) | 없음 |
| `WARNING` | 주의 필요하지만 자동 처리됨 | 보통 없음 |
| `ERROR` | 오류. 봇은 계속 실행됨 | 반복되면 확인 |
| `CRITICAL` | 심각한 오류. 봇 중단 가능 | 즉시 재시작 |

---

## 18. 텔레그램 알림 해석하기

봇(@GustjdBot)이 보내는 알림 종류와 의미입니다.

---

### 시스템 시작/종료

```
✅ [시스템 시작]
모드: paper

🛑 [시스템 종료]
사유: Shutdown requested
```

---

### 📡 실시간 매매 과정 알림 (3단계)

신호가 발생하면 순서대로 3개의 알림이 옵니다.

**① 신호 감지 (전략 판단 결과)**
```
🔔 [신호 감지]
마켓: KRW-ETH
전략: trend_filtered_breakout
신호: 매수
신뢰도: 76.5%
근거: [ALL SCREENS PASS] EMA20(3,050,000)>EMA60(2,900,000), RSI=58.3∈[45,70], price(3,040,000)≥target(3,020,000)
─────────────────
  ema_20: 3,050,000
  ema_60: 2,900,000
  rsi: 58.3
  atr_pct: 3.0
  target_price: 3,020,000
  k_value: 0.4
  position_krw: 167,000
```
→ **3개 스크린 모두 통과!** 리스크 점검으로 넘어갑니다.

**② 리스크 점검 (안전장치 검사)**
```
✅ [리스크 점검: 승인]
마켓: KRW-ETH
전략: trend_filtered_breakout
  • Position size within limits

🚫 [리스크 점검: 거절]
마켓: KRW-ETH
전략: trend_filtered_breakout
  • Maximum drawdown 16% has breached circuit-breaker threshold 15%
```
→ 승인이면 주문 실행, 거절이면 이 사이클 종료.

**③ 체결 확인**
```
🟢 [매수 체결]
마켓: KRW-ETH
가격: 3,040,000 KRW
수량: 0.054934
전략: trend_filtered_breakout
신뢰도: 76.5%
```

```
🔵 [매도 체결]   ← 다음날 자동 청산 (수익)
마켓: KRW-ETH
가격: 3,100,000 KRW
수량: 0.054934
손익: +3,244 KRW (+1.97%)
전략: trend_filtered_breakout

🔴 [매도 체결]   ← 손실
손익: -1,620 KRW (-0.98%)
```

---

### 전략 변경 확인 알림

텔레그램으로 전략을 변경하면 확인 알림이 옵니다.
```
⚙️ [전략 변경]
전략: trend_filtered_breakout
작업: 파라미터 변경
상세: k_value: 0.4 → 0.35

▶️ [전략 변경]
전략: trend_filtered_breakout
작업: 활성화

⏹️ [전략 변경]
전략: volatility_breakout
작업: 비활성화
```

---

### 모드 전환 알림

```
🔴 [모드 전환]
모의투자 → 실거래
⚠️ 실제 자금으로 거래합니다.

📄 [모드 전환]
실거래 → 모의투자
📄 가상 자금으로 거래합니다.
```

---

### 리스크 경고

```
🚨 [MDD 경고]
현재 MDD: 14.5%
한도: 15.0%
→ 한도에 거의 도달! 조심하세요.
```

---

### 주문 실패

```
⚠️ [주문 실패]
마켓: KRW-ETH
방향: 매수
전략: trend_filtered_breakout
사유: Insufficient balance (< 5,000 KRW minimum)
```

---

## 19. 코드 업데이트 방법

Mac에서 코드를 수정한 뒤 서버에 반영하는 방법입니다.

### 흐름

```
[Mac] 코드 수정 → git push → [서버] git pull → 재시작
```

---

### Mac에서 GitHub에 올리기

Mac의 터미널(서버 접속 전)에서:

```bash
# 프로젝트 폴더로 이동
cd ~/Desktop/upbit-trader

# 변경된 파일 확인
git status

# 변경 파일 추가
git add src/config/strategies.yaml  # 특정 파일만
# 또는
git add .  # 모든 변경사항

# 커밋 (변경 내용 설명)
git commit -m "k_value를 0.5에서 0.4로 변경"

# GitHub에 올리기
git push
```

---

### 서버에서 업데이트 적용

서버에 접속 후:

```bash
cd ~/upbit-trader

# 최신 코드 받기
git pull

# 라이브러리 업데이트 (필요할 때만)
uv sync

# 재시작
sudo systemctl restart upbit-trader

# 정상 확인
sudo systemctl status upbit-trader
```

---

# 6부 — 피드백과 수정

---

## 20. 전략 파라미터 조정하기

### 방법 1: 텔레그램 명령어 (즉시 적용, 재시작 불필요)

가장 쉽고 빠른 방법입니다. 외출 중에도 스마트폰으로 가능.

```
# K값 변경
/k 0.35

# 특정 파라미터 변경
/set trend_filtered_breakout k_value 0.35
/set trend_filtered_breakout rsi_min 40
/set trend_filtered_breakout rsi_max 75
/set trend_filtered_breakout atr_risk_pct 0.005
```

> ⚠️ 재시작하면 파일 원래 값으로 초기화됩니다. 영구 변경은 방법 2를 사용하세요.

---

### 방법 2: 파일 수정 (영구 적용)

서버에서 직접 수정하거나, Mac에서 수정 후 push합니다.

**서버에서 직접 수정:**
```bash
# 서버 접속 후
cd ~/upbit-trader
nano src/config/strategies.yaml
```

수정 전:
```yaml
params:
  k_value: 0.4
```

수정 후 (더 보수적으로):
```yaml
params:
  k_value: 0.35
```

저장: `Ctrl+O` → Enter → `Ctrl+X`

재시작:
```bash
sudo systemctl restart upbit-trader
```

---

### 전략 효과 테스트 방법

K값을 바꾼 뒤 며칠간 **paper 모드**에서 결과를 관찰하세요:

1. 텔레그램의 일일 보고 알림을 모아서 비교
2. 로그에서 신호 발생 빈도 확인:
   ```bash
   sudo journalctl -u upbit-trader --since "2026-03-01" | grep "signal=buy" | wc -l
   ```
   → 매수 신호가 며칠 동안 몇 번 발생했는지 숫자로 확인

---

## 21. 리스크 한도 조정하기

서버에서 직접 수정:

```bash
nano ~/upbit-trader/src/config/risk.yaml
```

예시 — 더 보수적인 설정:

```yaml
risk_rules:
  - name: max_position_size
    enabled: true
    max_single_asset_ratio: 0.15      # 20% → 15%로 줄임
    max_total_investment_ratio: 0.60  # 70% → 60%
    max_concurrent_positions: 3       # 5개 → 3개

  - name: daily_loss_limit
    enabled: true
    daily_loss_limit_pct: 0.03        # 5% → 3%

  - name: mdd_circuit_breaker
    enabled: true
    mdd_limit_pct: 0.10               # 15% → 10%

  - name: consecutive_loss_guard
    enabled: true
    max_consecutive_losses: 3         # 5번 → 3번
```

저장 후 재시작:
```bash
sudo systemctl restart upbit-trader
```

---

## 22. 거래 코인 추가/제거하기

`strategies.yaml`에서 `markets` 목록을 수정합니다.

> ⚠️ **비트코인(KRW-BTC)은 현재 의도적으로 제외되어 있습니다.** 장기 보유 중인 BTC 수량이 자동매매로 변경되지 않도록 설계된 것입니다.

**현재 (이더리움만):**
```yaml
markets:
  - KRW-ETH
```

**솔라나 추가 (이더리움 + 솔라나):**
```yaml
markets:
  - KRW-ETH
  - KRW-SOL
```

> ⚠️ 코인 코드는 대문자, `KRW-` 형식이어야 합니다. 업비트에서 지원하는 코인만 가능합니다.
> 업비트 앱 → 코인 선택 → 마켓 코드 확인

---

## 23. paper → live 모드 전환

> 🚨 **실제 돈이 관련됩니다. 아래 체크리스트를 반드시 확인하세요.**

### 전환 전 필수 체크리스트

- [ ] paper 모드에서 최소 2주 이상 안정적인 성과 확인
- [ ] 텔레그램 알림이 정상적으로 오고 있음 (신호 감지 → 리스크 점검 → 체결 순서로)
- [ ] 업비트 API 키에 **"출금" 권한 없음** 확인 (조회 + 거래만)
- [ ] 업비트 계정에 거래할 KRW가 충분히 있음
- [ ] 잃어도 되는 금액만 투자할 준비가 됨

---

### 전환 방법 1: 텔레그램 (즉시 전환, 재시작 불필요)

```
/mode live
```

텔레그램 응답:
```
🔴 [모드 전환]
모의투자 → 실거래
⚠️ 실제 자금으로 거래합니다.
```

> ⚠️ **주의**: 재시작하면 `.env`의 `TRADING_MODE` 값으로 돌아갑니다.
> live 모드를 재시작 후에도 유지하려면 아래 방법 2도 함께 사용하세요.

---

### 전환 방법 2: .env 파일 수정 (영구 적용)

```bash
# 서버 접속 후
nano ~/upbit-trader/.env
```

```
TRADING_MODE=paper   ← 이것을
TRADING_MODE=live    ← 이렇게 변경
```

저장 (`Ctrl+O` → Enter → `Ctrl+X`) 후:

```bash
sudo systemctl restart upbit-trader
sudo journalctl -u upbit-trader -n 20
```

`mode: live` 메시지 + 텔레그램 "시스템 시작 / live" 알림 확인.

---

### live → paper로 되돌리기

**텔레그램으로 즉시:**
```
/mode paper
```

**또는 .env 수정:**
같은 방법으로 `.env`에서 `TRADING_MODE=paper`로 변경 후 재시작.

---

## 23. 자주 발생하는 문제와 해결법 (FAQ)

---

### Q. 봇이 `failed` 상태예요

**A.** 원인 확인 → 재시작 순서로 해결합니다.

```bash
# 1. 마지막 50줄 로그에서 오류 확인
sudo journalctl -u upbit-trader -n 50

# 2. 맨 아래 ERROR / CRITICAL 메시지 읽기
# 예: "UPBIT_ACCESS_KEY not configured" → .env 파일 확인

# 3. 재시작
sudo systemctl restart upbit-trader
```

반복해서 실패한다면 로그 내용을 그대로 복사하여 문의하세요.

---

### Q. 텔레그램 알림이 안 와요

**A.** 순서대로 확인합니다.

```bash
# 텔레그램 관련 오류 찾기
sudo journalctl -u upbit-trader -n 100 | grep -i telegram
```

- `HTTP 200` → 전송 성공 (텔레그램 앱에서 @GustjdBot 확인)
- `timeout` → 서버 인터넷 연결 일시적 문제. 잠시 후 다시 확인
- `401 Unauthorized` → 봇 토큰이 틀림. `.env`의 `TELEGRAM_BOT_TOKEN` 확인

---

### Q. 봇이 전혀 매수를 안 해요

**A.** 정상일 수 있습니다. 로그를 확인하세요.

```bash
sudo journalctl -u upbit-trader --since today | grep "signal="
```

- `signal=hold`가 계속 나오면 → 시장이 기준에 맞지 않는 것. 정상입니다.
- 신호 자체가 너무 드물다면 K값을 낮춰보세요 (0.5 → 0.4 → 0.3)

---

### Q. 매수는 하는데 매도를 안 해요

**A.** 변동성 돌파 전략은 다음 날 시가에 매도하는 구조입니다. 하루가 지나면 자동으로 처리됩니다. 즉시 매도를 원하면 전략 로직을 수정해야 합니다 (개발 작업 필요).

---

### Q. 서킷브레이커가 발동됐어요. 어떻게 해야 하나요?

**A.** 두 가지 선택지가 있습니다.

**선택 1: 기다리기**
고점이 현재가로 재설정되면 MDD가 0으로 돌아와 자동 해제됩니다.

**선택 2: 한도 일시 완화**
`risk.yaml`에서 `mdd_limit_pct`를 일시적으로 높이고 재시작:
```yaml
mdd_limit_pct: 0.20  # 0.15 → 0.20으로 일시 완화
```

---

### Q. SSH 접속이 안 돼요

```bash
# 1. 키 파일 존재 확인
ls ~/Downloads/ssh-key-2026-03-01.key

# 2. 권한 재설정
chmod 400 ~/Downloads/ssh-key-2026-03-01.key

# 3. 재시도
ssh -i ~/Downloads/ssh-key-2026-03-01.key ubuntu@168.107.52.195
```

계속 안 되면 인터넷 연결 확인 또는 수 분 후 재시도.

---

### Q. nano 편집기 사용법을 모르겠어요

| 동작 | 단축키 |
|------|--------|
| 저장 | `Ctrl + O` → `Enter` |
| 종료 | `Ctrl + X` |
| 줄 이동 | 방향키 (↑↓←→) |
| 저장 없이 종료 | `Ctrl + X` → `N` |

---

### Q. `Permission denied` 오류가 나요

`sudo`를 명령어 앞에 붙이세요.
```bash
# 틀림
systemctl restart upbit-trader

# 맞음
sudo systemctl restart upbit-trader
```

---

### Q. 서버가 재부팅되면 봇도 꺼지나요?

아니요! 자동 재시작됩니다. systemd의 `enabled` 설정 덕분에 서버 부팅 시 봇도 자동으로 시작됩니다. 봇이 갑자기 죽어도 10초 내 자동 재시작됩니다 (최대 5분에 5번).

---

### Q. paper 모드에서 수익이 나는데 live로 바꿔도 같은 결과가 나오나요?

반드시 그렇지는 않습니다. Paper 모드와 live 모드의 차이:
- **슬리피지**: 실제 시장에서는 내가 원하는 가격에 정확히 체결되지 않을 수 있음
- **시장 충격**: 큰 금액 주문은 가격을 움직일 수 있음
- **API 지연**: 실시간 체결에 약간의 지연이 있음

Paper에서 좋아도 live에서는 결과가 다를 수 있습니다. 충분한 기간 동안 paper 테스트 후 소액으로 live를 시작하세요.

---

## 24. 빠른 참조 카드

### 📱 텔레그램 명령어 (외부에서 스마트폰으로)

```
# 모니터링
/ping          봇 상태 확인
/status        잔고 + 포지션
/strategy      전략 현황 + 파라미터

# 전략 제어 (재시작 불필요)
/k 0.35                                        K값 변경
/set trend_filtered_breakout k_value 0.35      K값 변경
/set trend_filtered_breakout rsi_min 40        RSI 하한 변경
/set trend_filtered_breakout atr_risk_pct 0.005  리스크 비율 변경
/enable trend_filtered_breakout                전략 활성화
/disable volatility_breakout                   전략 비활성화

# 모드 전환
/mode          현재 모드 확인
/mode live     실거래 전환 ⚠️
/mode paper    모의투자 전환

# 운영
/pause         일시정지
/resume        재개
/stop          봇 종료
/help          명령어 목록
```

---

### 🖥️ 서버 접속 & 나가기
```bash
# 접속
ssh -i ~/Downloads/ssh-key-2026-03-01.key ubuntu@168.107.52.195

# 나가기
exit
```

### 봇 관리
```bash
sudo systemctl status upbit-trader   # 상태 확인
sudo systemctl restart upbit-trader  # 재시작
sudo systemctl stop upbit-trader     # 중지
sudo systemctl start upbit-trader    # 시작
```

### 로그 확인
```bash
sudo journalctl -u upbit-trader -f             # 실시간
sudo journalctl -u upbit-trader -n 50          # 마지막 50줄
sudo journalctl -u upbit-trader --since today  # 오늘 전체
```

### 설정 파일 수정 (영구 적용용)
```bash
nano ~/upbit-trader/src/config/strategies.yaml  # 전략 설정
nano ~/upbit-trader/src/config/risk.yaml        # 리스크 설정
nano ~/upbit-trader/.env                         # API 키 / 모드
```

### 코드 업데이트 (서버에서)
```bash
cd ~/upbit-trader
git pull
sudo systemctl restart upbit-trader
```

---

## 참고 정보

| 항목 | 값 |
|------|-----|
| 서버 IP | 168.107.52.195 |
| 서버 OS | Ubuntu (Oracle Cloud, 한국 춘천) |
| 텔레그램 봇 | @GustjdBot |
| 거래소 | 업비트 (Upbit) |
| 현재 전략 | **TFVB** — trend_filtered_breakout (k=0.4, EMA+RSI+돌파) |
| 거래 코인 | **KRW-ETH** (이더리움만, BTC 제외) |
| 슬리피지 | 0.05% (보수적, 네트워크 지연 반영) |
| 현재 모드 | paper (가상 거래, `/mode live`로 전환 가능) |
| 텔레그램 제어 | `/mode`, `/set`, `/enable`, `/disable`, `/k`, `/pause` 등 |

---

> 문서 최종 업데이트: 2026-03-04
> 이 가이드는 시스템의 전체 아키텍처와 운영 방법을 설명합니다.
> 코드 수정이 필요한 작업(새 전략 추가, API 변경 등)은 개발자에게 문의하세요.
