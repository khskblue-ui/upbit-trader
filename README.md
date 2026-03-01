# upbit-trader

업비트(Upbit) API 기반 코인 자동 트레이딩 시스템.

## 특징

- **확장 가능한 전략 아키텍처** — `BaseStrategy`를 상속해 전략 추가, `@register` 데코레이터로 자동 등록
- **컴포저블 리스크 엔진** — `BaseRiskRule` 체인으로 포지션 크기·MDD·손절 등 규칙 조합
- **백테스팅** — 슬리피지·수수료 시뮬레이션, 샤프 비율·MDD·CAGR 등 성과 지표 계산
- **실전 거래** — 업비트 REST/WebSocket 연동, 주문 상태 폴링, 자동 취소
- **모니터링** — 텔레그램 알림, 로테이팅 로그 파일, 일/주간 성과 리포트
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

# 5. 백테스트 실행 (실제 API 불필요)
uv run python -m src.main --mode backtest

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
TRADING_MODE=backtest          # backtest | live
LOG_LEVEL=INFO
```

## 프로젝트 구조

```
upbit-trader/
├── src/
│   ├── api/                  # 업비트 REST 클라이언트
│   │   ├── client.py         # JWT 인증, 서명, 요청 처리
│   │   └── models.py         # API 응답 Pydantic 모델
│   ├── data/                 # 데이터 수집 및 저장
│   │   ├── database.py       # SQLAlchemy 비동기 DB
│   │   ├── models.py         # ORM 모델 (Trade, Candle 등)
│   │   └── collector.py      # 캔들 데이터 수집기
│   ├── strategy/             # 트레이딩 전략
│   │   ├── base.py           # BaseStrategy ABC, Signal, TradeSignal
│   │   ├── registry.py       # @register 데코레이터, StrategyRegistry
│   │   ├── technical.py      # RSI, MACD, BB 등 보조지표
│   │   ├── volatility_breakout.py  # 변동성 돌파 전략
│   │   ├── rsi_bollinger.py        # RSI + 볼린저밴드 전략
│   │   └── macd_momentum.py        # MACD 모멘텀 전략
│   ├── risk/                 # 리스크 관리
│   │   ├── base.py           # BaseRiskRule, RiskDecision
│   │   └── engine.py         # RiskEngine (규칙 체인)
│   ├── execution/            # 주문 실행
│   │   ├── base.py           # BaseExecutor, OrderRequest, OrderResult
│   │   ├── live_executor.py  # 실거래 실행기
│   │   ├── backtest_executor.py  # 백테스트 실행기
│   │   ├── order_manager.py  # 주문 상태 폴링·취소
│   │   └── position_tracker.py   # 인메모리 포지션 추적
│   ├── backtest/             # 백테스팅 엔진
│   │   ├── engine.py         # BacktestEngine
│   │   └── report.py         # PerformanceMetrics, format_report
│   ├── core/                 # 핵심 오케스트레이터
│   │   └── trading_engine.py # TradingEngine (전략→리스크→실행)
│   ├── monitoring/           # 모니터링
│   │   ├── logger.py         # 로그 설정 (파일 로테이션)
│   │   └── reporter.py       # 일/주간 성과 리포트
│   └── notification/         # 알림
│       └── telegram_bot.py   # 텔레그램 봇
├── tests/                    # 전체 테스트 스위트
├── scripts/
│   ├── deploy.sh             # 프로덕션 배포 스크립트
│   └── backup_db.sh          # DB 백업 (cron 연동)
├── systemd/
│   └── upbit-trader.service  # systemd 유닛 파일
├── config/
│   └── strategies.yaml       # 전략 파라미터 설정
└── pyproject.toml
```

## 내장 전략

| 전략 | 설명 | 주요 파라미터 |
|------|------|--------------|
| `volatility_breakout` | 변동성 돌파 — 전일 범위의 k배 이상 상승 시 매수 | `k_value=0.5` |
| `rsi_bollinger` | RSI 과매도(30) + 볼린저 하단 → 매수, RSI 과매수(70) + 볼린저 상단 → 매도 | `rsi_period=14`, `bb_period=20` |
| `macd_momentum` | MACD 골든크로스 + SMA 정배열 + 거래량 급증 → 매수 | `fast=12`, `slow=26`, `signal=9` |

## 새 전략 추가

```python
# src/strategy/my_strategy.py
from src.strategy.base import BaseStrategy, MarketData, Signal, TradeSignal
from src.strategy.registry import register

@register
class MyStrategy(BaseStrategy):
    name = "my_strategy"

    async def generate_signal(self, market: str, data: MarketData) -> TradeSignal:
        # 매수/매도/홀드 로직 구현
        return TradeSignal(signal=Signal.HOLD, market=market, confidence=0.0, reason="no signal")

    def required_indicators(self) -> list[str]:
        return ["rsi"]

    def required_timeframes(self) -> list[str]:
        return ["1h"]
```

## 배포 (Linux / systemd)

```bash
# 1. 배포 스크립트 실행
chmod +x scripts/deploy.sh
./scripts/deploy.sh --env /path/to/.env

# 2. 서비스 상태 확인
sudo systemctl status upbit-trader

# 3. 로그 확인
journalctl -u upbit-trader -f

# 4. DB 백업 (cron에 등록 권장)
chmod +x scripts/backup_db.sh
# crontab -e 에 추가:
# 0 2 * * * /path/to/upbit-trader/scripts/backup_db.sh
```

## 테스트

```bash
# 전체 테스트
uv run pytest

# 특정 모듈만
uv run pytest tests/test_strategies.py -v

# 커버리지 포함
uv run pytest --cov=src --cov-report=term-missing
```

## 아키텍처 원칙

- **개방-폐쇄 원칙** — 전략과 리스크 규칙은 기존 코드 수정 없이 추가 가능
- **의존성 역전** — `TradingEngine`은 `BaseExecutor` 인터페이스에만 의존 (실거래/백테스트 교체 가능)
- **비동기 우선** — 모든 I/O는 `asyncio` 기반
- **타입 안전** — Pydantic v2 + 타입 힌트 전면 적용

## 라이선스

MIT
