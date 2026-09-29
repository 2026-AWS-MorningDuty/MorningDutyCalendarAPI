# Performance Logging Guide

## 1. 목적

현재 구현되어 있는 Morning Duty Calendar API의 동작 로직은 변경하지 않고,
각 처리 구간의 실행 시간과 API 전체 처리 시간을 CloudWatch Logs에서 확인할 수 있도록
성능 측정 로그를 추가한다.

성능 로그는 모두 다음 Prefix를 사용한다.

```text
[PERF]
```

목표는 다음 두 가지다.

1. 각 처리 단계가 얼마나 걸렸는지 확인
2. 요청 1건의 전체 Lambda 처리 시간이 얼마나 걸렸는지 확인

예:

```text
[PERF] route=GET /calendar step=get_current_user duration_ms=42.8
[PERF] route=GET /calendar step=query_calendar duration_ms=68.4
[PERF] route=GET /calendar step=build_calendar duration_ms=3.2
[PERF] route=GET /calendar step=total duration_ms=131.7
```

---

# 2. 기본 원칙

기존 비즈니스 로직, Response 형식, DynamoDB 구조는 변경하지 않는다.

성능 측정을 위해 필요한 최소한의 코드만 추가한다.

Python의 다음 함수를 사용한다.

```python
time.perf_counter()
```

`datetime.now()` 등으로 실행 시간을 측정하지 않는다.

기존 프로젝트의 Logging 원칙에 맞춰 `print()`를 사용하지 않고
Python `logging`을 사용한다.

```python
logger.info(...)
```

---

# 3. 로그 형식

모든 성능 로그는 가능한 한 동일한 형식을 사용한다.

```text
[PERF] route=<ROUTE> step=<STEP_NAME> duration_ms=<TIME>
```

예:

```text
[PERF] route=GET /calendar step=get_current_user duration_ms=41.3
```

전체 처리 시간은 반드시 다음처럼 `step=total`로 남긴다.

```text
[PERF] route=GET /calendar step=total duration_ms=187.6
```

이렇게 하면 CloudWatch Logs에서 다음 문자열로 쉽게 검색할 수 있다.

```text
[PERF]
```

또는 특정 API만 확인할 수 있다.

```text
[PERF] route=GET /calendar
```

---

# 4. 공통 측정 방식

각 처리 직전에 시작 시간을 저장하고,
처리가 끝난 직후 경과 시간을 로그로 기록한다.

```python
import time

start = time.perf_counter()

result = some_operation()

duration_ms = (time.perf_counter() - start) * 1000

logger.info(
    "[PERF] route=%s step=%s duration_ms=%.1f",
    route,
    "some_operation",
    duration_ms,
)
```

---

# 5. 권장 Helper

같은 코드가 반복되는 것을 줄이기 위해
성능 로그용 작은 Helper를 추가해도 된다.

예:

```python
import time


def log_perf(logger, route: str, step: str, started_at: float) -> None:
    duration_ms = (time.perf_counter() - started_at) * 1000

    logger.info(
        "[PERF] route=%s step=%s duration_ms=%.1f",
        route,
        step,
        duration_ms,
    )
```

사용 예:

```python
started_at = time.perf_counter()

user = user_repository.find_by_cognito_sub(cognito_sub)

log_perf(
    logger,
    "GET /calendar",
    "get_current_user",
    started_at,
)
```

단, Helper 추가 때문에 기존 구조를 크게 변경하지 않는다.

---

# 6. 전체 요청 시간 측정

각 API 요청이 Lambda 내부에서 실제로 얼마나 걸렸는지 확인하기 위해
route 처리가 시작되는 시점에 전체 Timer를 시작한다.

예:

```python
total_started_at = time.perf_counter()
```

Response 반환 직전에 다음 로그를 남긴다.

```python
log_perf(
    logger,
    route,
    "total",
    total_started_at,
)
```

예상 로그:

```text
[PERF] route=GET /calendar step=total duration_ms=214.6
```

---

# 7. GET /calendar

`GET /calendar`는 현재 API 중 처리 단계가 가장 많기 때문에
가장 세분화해서 측정한다.

권장 측정 구간:

```text
Request 시작
    ↓
현재 사용자 조회
    ↓
요청 월 Calendar Query
    ↓
이전 월 존재 여부 Query
    ↓
다음 월 존재 여부 Query
    ↓
현재 월 / 다음 월 changeCandidates 조회
    ↓
전체 날짜 생성 및 DB 데이터 Merge
    ↓
공휴일 / 주말 / mine / candidate 계산
    ↓
Response 생성
    ↓
Request 종료
```

권장 `step` 이름:

```text
get_current_user
query_calendar
query_previous_month
query_next_month
query_change_candidates
build_calendar
build_response
total
```

예상 로그:

```text
[PERF] route=GET /calendar step=get_current_user duration_ms=38.4
[PERF] route=GET /calendar step=query_calendar duration_ms=61.2
[PERF] route=GET /calendar step=query_previous_month duration_ms=27.1
[PERF] route=GET /calendar step=query_next_month duration_ms=25.8
[PERF] route=GET /calendar step=query_change_candidates duration_ms=55.3
[PERF] route=GET /calendar step=build_calendar duration_ms=4.8
[PERF] route=GET /calendar step=build_response duration_ms=1.9
[PERF] route=GET /calendar step=total duration_ms=224.5
```

## 주의

`query_previous_month`, `query_next_month` 등 내부 구현이 하나의 Service 함수 안에 묶여 있다면
성능 로그를 넣기 위해 억지로 메서드를 분리하지 않는다.

현재 코드에서 자연스럽게 측정 가능한 구간까지만 로그를 추가한다.

---

# 8. PATCH /me/notification

권장 처리 흐름:

```text
Request 시작
    ↓
현재 사용자 조회
    ↓
Request Body 검증
    ↓
Users emailNotification Update
    ↓
Response 생성
    ↓
Request 종료
```

권장 `step` 이름:

```text
get_current_user
validate_request
update_notification
build_response
total
```

예상 로그:

```text
[PERF] route=PATCH /me/notification step=get_current_user duration_ms=35.7
[PERF] route=PATCH /me/notification step=validate_request duration_ms=0.1
[PERF] route=PATCH /me/notification step=update_notification duration_ms=48.6
[PERF] route=PATCH /me/notification step=build_response duration_ms=0.5
[PERF] route=PATCH /me/notification step=total duration_ms=87.4
```

---

# 9. POST /schedule/swap

권장 처리 흐름:

```text
Request 시작
    ↓
현재 사용자 조회
    ↓
Request Body / 날짜 검증
    ↓
myDate 조회
    ↓
targetDate 조회
    ↓
Swap 조건 검증
    ↓
DynamoDB Transaction
    ↓
관련 User 조회
    ↓
SES 이메일 발송
    ↓
Response 생성
    ↓
Request 종료
```

권장 `step` 이름:

```text
get_current_user
validate_request
get_my_schedule
get_target_schedule
validate_swap
dynamodb_transaction
get_notification_users
send_email
build_response
total
```

예상 로그:

```text
[PERF] route=POST /schedule/swap step=get_current_user duration_ms=37.5
[PERF] route=POST /schedule/swap step=validate_request duration_ms=0.4
[PERF] route=POST /schedule/swap step=get_my_schedule duration_ms=31.2
[PERF] route=POST /schedule/swap step=get_target_schedule duration_ms=29.8
[PERF] route=POST /schedule/swap step=validate_swap duration_ms=0.2
[PERF] route=POST /schedule/swap step=dynamodb_transaction duration_ms=72.5
[PERF] route=POST /schedule/swap step=get_notification_users duration_ms=51.9
[PERF] route=POST /schedule/swap step=send_email duration_ms=248.1
[PERF] route=POST /schedule/swap step=build_response duration_ms=0.7
[PERF] route=POST /schedule/swap step=total duration_ms=475.9
```

SES는 외부 AWS Service 호출이므로
다른 처리보다 시간이 크게 나올 가능성이 있다.

따라서 `send_email`은 반드시 별도로 측정한다.

---

# 10. 성공뿐 아니라 실패 요청도 total 기록

가능하면 성공 요청뿐 아니라 오류가 발생한 요청에서도
전체 처리 시간을 확인할 수 있도록 한다.

가장 안정적인 방법은 request 단위 처리를 `try / finally`로 감싸는 것이다.

예:

```python
route = event["requestContext"]["http"]["method"] + " " + event["rawPath"]
total_started_at = time.perf_counter()

try:
    return handle_request(event)

finally:
    log_perf(
        logger,
        route,
        "total",
        total_started_at,
    )
```

단, 현재 `lambda_handler` 구조에 따라
기존 예외 처리 흐름이나 Response 반환 구조가 변경되지 않도록 적용한다.

---

# 11. Lambda Cold Start와 구분

`[PERF] step=total`은 Lambda Handler 내부에서 측정한 시간이다.

CloudWatch에서는 다음 Lambda 기본 로그도 함께 확인한다.

```text
INIT_START
START RequestId: ...
END RequestId: ...
REPORT RequestId: ...
```

예:

```text
INIT_START
START RequestId: abc
[PERF] route=GET /calendar step=total duration_ms=240.2
END RequestId: abc
REPORT RequestId: abc Duration: 243.10 ms
```

`INIT_START`가 존재한다면 Cold Start가 발생한 호출일 가능성이 있다.

따라서 성능 확인 시 다음을 구분한다.

```text
Cold Start가 발생한 요청
Warm 상태의 요청
```

API 성능을 판단할 때는 한 번의 호출만 보지 않고
여러 번 호출한 결과를 비교한다.

---

# 12. 로그에 넣지 않을 정보

성능 측정을 위해 다음 값을 추가로 출력하지 않는다.

```text
Access Token
ID Token
Refresh Token
Authorization Header
Password
```

필요한 경우에도 개인 정보나 인증 정보를 성능 로그에 포함하지 않는다.

성능 로그에는 가능한 한 다음 정보만 사용한다.

```text
route
step
duration_ms
```

필요하다면 디버깅을 위해 `requestId` 정도만 추가할 수 있다.

예:

```text
[PERF] requestId=xxx route=GET /calendar step=query_calendar duration_ms=43.2
```

---

# 13. 구현 시 주의사항

이번 작업의 목적은 성능 측정이다.

따라서 다음 작업은 하지 않는다.

```text
API Endpoint 변경
DynamoDB Table 구조 변경
Query → 다른 조회 방식으로 변경
Service / Repository 구조 대규모 Refactoring
Response 구조 변경
비즈니스 로직 변경
```

먼저 현재 코드를 그대로 측정한다.

측정 결과를 확인한 뒤 실제 병목이 발견되었을 때
별도 작업으로 최적화를 진행한다.

---

# 14. 최종 목표 로그

CloudWatch에서 하나의 요청에 대해 다음과 같이
처리 흐름과 총 시간을 바로 확인할 수 있는 상태를 목표로 한다.

```text
[PERF] route=GET /calendar step=get_current_user duration_ms=41.5
[PERF] route=GET /calendar step=query_calendar duration_ms=57.8
[PERF] route=GET /calendar step=query_previous_month duration_ms=24.1
[PERF] route=GET /calendar step=query_next_month duration_ms=23.7
[PERF] route=GET /calendar step=query_change_candidates duration_ms=49.3
[PERF] route=GET /calendar step=build_calendar duration_ms=5.4
[PERF] route=GET /calendar step=build_response duration_ms=0.8
[PERF] route=GET /calendar step=total duration_ms=207.9
```

이 로그만 보면 다음 질문에 바로 답할 수 있어야 한다.

```text
전체 요청은 몇 ms 걸렸는가?
현재 사용자 조회는 몇 ms 걸렸는가?
DynamoDB Query 중 어느 부분이 가장 느린가?
Calendar 생성 로직 자체가 느린가?
SES 발송이 병목인가?
```

---

# 15. 구현 완료 조건

다음 조건을 만족하면 작업 완료로 본다.

- 모든 성능 로그가 `[PERF]`로 시작한다.
- 모든 로그에 `route`가 포함된다.
- 모든 로그에 `step`이 포함된다.
- 모든 로그에 `duration_ms`가 포함된다.
- 3개 API 모두 `step=total` 로그가 존재한다.
- 주요 DynamoDB 접근 구간의 시간이 측정된다.
- `POST /schedule/swap`의 SES 발송 시간이 별도로 측정된다.
- 기존 API 동작과 Response는 변경되지 않는다.
- 인증 정보 및 민감 정보는 로그에 남지 않는다.
- 기존 테스트가 모두 통과한다.
