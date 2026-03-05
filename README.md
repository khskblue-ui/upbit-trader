# upbit-trader

업비트(Upbit) API 기반 코인 자동 트레이딩 시스템.

## 특징

- **확장 가능한 전략 아키텍처** — `BaseStrategy`를 상속해 전략 추가, `@register` 데코레이터로 자동 등록
- **3단계 청산 알고리즘** — HARD_STOP → 트레일링 스탑 → 시간 청산 (세션 또는 시간 기준)
- **컴포저블 리스크 엔진** — `BaseRiskRule` 체인으로 포지션 크기·MDD·손절 등 규칙 조합
- **백테스팅** — 슬리피지·수수료 시뮬레이션, 샤프 비율·MDD·CAGR 등 성과 지표 계산
- **실전 거래** — 업비트 REST/WebSocket 연동, 주문 상태 폴링, 자동 취소
- **모니터링** — 텔레그램 알림, 매시간 브리핑, 로테이팅 로그 파일
- **런타임 제어** — 텔레그램 명령어로 전략 전환·파라미터 변경·모드 전환 (재시작 불필요)
- **배포** — systemd 서비스 + 자동 DB 백업 스크립트

## 요구 사항

| 항목 | 버전 |
|------|------|
| Python | 3.12+ |
| uv | 0.5+ |
| SQLite | 3.35+ |

## 빠른 시작

```bash
# 1. 저장소 클론
git clone https://github.com/khskblue-ui/upbit-trader.git
cd upbit-trader

# 2. 환경 변수 설정
cp .env.example .env
# .env 파일에 API 키와 텔레그램 정보 입력

# 3. 의존성 설치
uv sync

# 4. 테스트 실행
uv run pytest

# 5. 페이퍼 트레이딩 (모의투자)
uv run python -m src.main --mode paper

# 6. 실거래 실행
uv run python -m src.main --mode live
```

## 환경 변수 (.env)

```dotenv
# 업비트 API
UPBIT_ACCESS_KEY=your_access_key
UPBIT_SECRET_KEY=your_secret_key

# 텔레그램 알림
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 데이터베이스
DATABASE_URL=sqlite+aiosqlite:///data/trading.db

# 거래 설정
TRADING_MODE=paper          # paper | live
LOG_LEVEL=INFO
```

## 프로젝트 구조

```
upbit-trader/
├── src/
│   ├── api/                  # 업비트 REST 클라이언트
│   │   └── upbit_client.py   # JWT 인증, 캔들/호가/잔고 조회
│   ├── config/
│   │   └── strategies.yaml   # 전략 파라미터 설정 파일
│   ├── data/                 # 데이터 수집 및 저장
│   │   ├── database.py       # SQLAlchemy 비동기 DB
│   │   └── models.py         # ORM 모델 (Trade 등)
│   ├── indicators/
│   │   └── technical.py      # EMA, RSI, ATR 등 지표 계산
│   ├── strategy/             # 트레이딩 전략
│   │   ├── base.py                         # BaseStrategy ABC, Signal, TradeSignal
│   │   ├── registry.py                     # @register 데코레이터, StrategyRegistry
│   │   ├── trend_filtered_breakout.py      # TFVB — 일봉 전략
│   │   └── intraday_momentum_breakout.py   # IMB — 1시간봉 전략
│   ├── risk/                 # 리스크 관리
│   │   ├── base.py           # BaseRiskRule, RiskDecision
│   │   └── engine.py         # RiskEngine (규칙 체인)
│   ├── execution/            # 주문 실행
│   │   ├── base.py               # BaseExecutor, OrderRequest, OrderResult
│   │   ├── live_executor.py      # 실거래 실행기
│   │   └── paper_executor.py     # 페이퍼 트레이딩 실행기
│   ├── core/
│   │   └── trading_engine.py     # TradingEngine (전략→리스크→실행)
│   └── notification/
│       ├── telegram_bot.py       # 텔레그램 알림 발송
│       └── command_handler.py    # 텔레그램 명령어 수신·처리
├── tests/                    # 전체 테스트 스위트
├── scripts/
│   ├── deploy.sh             # 프로덕션 배포 스크립트
│   └── backup_db.sh          # DB 백업 (cron 연동)
├── systemd/
│   └── upbit-trader.service  # systemd 유닛 파일
└── pyproject.toml
```

## 내장 전략

### TFVB — Trend-Filtered Volatility Breakout (일봉)

일봉 기반 3단계 스크리닝 전략. 추세가 확인된 상승장에서만 변동성 돌파 진입.

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `k_value` | 0.4 | 변동성 돌파 계수 (시가 + ATR×k) |
| `atr_risk_pct` | 0.01 | 거래당 리스크 1% |
| `rsi_min` / `rsi_max` | 45 / 70 | RSI 허용 범위 |
| `atr_trail_mult` | 2.0 | ATR 트레일링 스탑 승수 |
| `hard_stop_pct` | 0.05 | 하드스탑 5% |
| `max_hold_days` | 5 | 최대 보유 세션 수 (09:00 KST 기준) |

**청산 방식:**
- 트레일링 스탑: ATR 기반, 새 Upbit 세션(09:00 KST)마다 1회 갱신
- 시간 청산: `max_hold_days` 세션 초과 시 강제 청산

---

### IMB — Intraday Momentum Breakout (1시간봉)

1시간봉 기반 3단계 스크리닝 전략. 퍼센트 기반 스탑과 24시간 시간 청산.

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `k_value` | 1.5 | 변동성 돌파 계수 (시가 + ATR_24×k) |
| `atr_risk_pct` | 0.01 | 거래당 리스크 1% |
| `rsi_min` / `rsi_max` | 50 / 75 | RSI 허용 범위 |
| `hard_stop_pct` | 0.03 | 하드스탑 3% |
| `trailing_stop_pct` | 0.03 | 퍼센트 트레일링 스탑 3% |
| `max_hold_hours` | 24 | 최대 보유 시간 (실경과 시간 기준) |

**청산 방식:**
- 트레일링 스탑: 퍼센트 기반, 매 틱마다 갱신 (최고가 × (1 - 3%))
- 시간 청산: 매수 후 `max_hold_hours` 경과 시 강제 청산

**포지션 사이징:**
```
risk_budget = capital × atr_risk_pct        # 예: 1,000,000 × 0.01 = 10,000 KRW
position   = risk_budget / hard_stop_pct    # 예: 10,000 / 0.03 = 333,333 KRW
```
스탑이 발동(-3%)하면 손실 = 자본의 정확히 1%.

---

### 전략 비교

| 항목 | TFVB | IMB |
|------|------|-----|
| 타임프레임 | 1d | 60m |
| EMA 필터 | EMA20 > EMA60 | EMA24 > EMA120 |
| RSI 범위 | [45, 70] | [50, 75] |
| 트레일링 스탑 | ATR 기반 (세션 1회) | 퍼센트 기반 (매 틱) |
| 시간 청산 | 세션 단위 (09:00 KST) | 실시간 경과 시간 |

## 텔레그램 명령어

봇이 실행 중이면 텔레그램에서 실시간으로 제어할 수 있습니다.

### 모니터링

| 명령어 | 설명 |
|--------|------|
| `/ping` | 봇 작동 여부 확인 |
| `/status` | 현재 잔고 및 포지션 |
| `/strategy` | 전략 현황 및 파라미터 |
| `/briefing` | 마지막 브리핑 이후 현재까지의 요약을 즉시 전송하고 창 초기화 |

### 전략 제어

| 명령어 | 설명 |
|--------|------|
| `/switchstrategy tfvb` | TFVB(일봉 전략)로 독점 전환 |
| `/switchstrategy imb` | IMB(1시간봉 전략)로 독점 전환 |
| `/enable <전략명>` | 전략 활성화 |
| `/disable <전략명>` | 전략 비활성화 |
| `/set <전략명> <파라미터> <값>` | 파라미터 실시간 변경 |
| `/k <값>` | k_value 일괄 변경 (0.1 ~ 0.9) |

**예시:**
```
/switchstrategy imb
/set intraday_momentum_breakout rsi_min 52
/set trend_filtered_breakout k_value 0.35
/k 0.4
```

### 운영 제어

| 명령어 | 설명 |
|--------|------|
| `/mode paper` | 모의투자 모드 전환 |
| `/mode live` | 실거래 모드 전환 |
| `/pause` | 거래 일시정지 (모니터링 계속) |
| `/resume` | 거래 재개 |
| `/stop` | 봇 종료 |
| `/help` | 전체 명령어 목록 |

## strategies.yaml 설정

```yaml
strategies:
  - name: trend_filtered_breakout
    enabled: true
    markets: [KRW-ETH, KRW-XRP, KRW-SOL]
    timeframe: "1d"
    params:
      k_value: 0.4
      atr_risk_pct: 0.01
      rsi_min: 45
      rsi_max: 70
      atr_trail_mult: 2.0
      hard_stop_pct: 0.05
      max_hold_days: 5

  - name: intraday_momentum_breakout
    enabled: false          # /switchstrategy imb 로 런타임 전환
    markets: [KRW-ETH, KRW-XRP, KRW-SOL]
    timeframe: "60m"
    params:
      k_value: 1.5
      atr_risk_pct: 0.01
      rsi_min: 50
      rsi_max: 75
      hard_stop_pct: 0.03
      trailing_stop_pct: 0.03
      max_hold_hours: 24
```

## 새 전략 추가

```python
# src/strategy/my_strategy.py
from src.strategy.base import BaseStrategy, MarketData, Signal, TradeSignal
from src.strategy.registry import register

@register
class MyStrategy(BaseStrategy):
    name = "my_strategy"

    def required_indicators(self) -> list[str]:
        return ["ema_20", "rsi_14"]

    def required_timeframes(self) -> list[str]:
        return ["1d"]

    async def generate_signal(self, market: str, data: MarketData) -> TradeSignal:
        return TradeSignal(signal=Signal.HOLD, market=market, confidence=0.0, reason="no signal")
```

`src/main.py`에 `import src.strategy.my_strategy  # noqa: F401` 추가하면 자동 등록됩니다.

## 배포 (Linux / systemd)

```bash
# 1. 서버에서 최신 코드 반영
git pull origin main

# 2. 배포 스크립트 실행 (의존성 설치 + DB 초기화 + 서비스 재시작)
chmod +x scripts/deploy.sh
./scripts/deploy.sh --env /path/to/.env

# 3. 서비스 상태 확인
sudo systemctl status upbit-trader

# 4. 실시간 로그 확인
journalctl -u upbit-trader -f

# 5. DB 백업 (cron에 등록 권장)
# crontab -e 에 추가:
# 0 2 * * * /path/to/upbit-trader/scripts/backup_db.sh
```

## 테스트

```bash
# 전체 테스트 (268개)
uv run pytest

# 전략별
uv run pytest tests/test_imb_strategy.py -v
uv run pytest tests/test_strategies.py -v

# 커버리지 포함
uv run pytest --cov=src --cov-report=term-missing
```

## 아키텍처 원칙

- **개방-폐쇄 원칙** — 전략과 리스크 규칙은 기존 코드 수정 없이 추가 가능
- **의존성 역전** — `TradingEngine`은 `BaseExecutor` 인터페이스에만 의존 (실거래/페이퍼 교체 가능)
- **비동기 우선** — 모든 I/O는 `asyncio` 기반
- **타입 안전** — Pydantic v2 + 타입 힌트 전면 적용
- **MagicMock 안전** — config 속성 읽기 시 `isinstance(val, (int, float))` 가드 적용 (테스트 격리)

## 라이선스

MIT
