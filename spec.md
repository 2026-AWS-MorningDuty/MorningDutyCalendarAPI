# Morning Duty Calendar API Spec

````md

## 1. 프로젝트 개요

사내 아침 조례(朝礼) 담당자 일정을 관리하는 Serverless Web Application의 Backend API.

주요 기능:

- 월별 담당자 캘린더 조회
- 일본 공휴일 / 휴일 표시
- 이메일 알림 ON/OFF
- 본인 담당 날짜와 다른 담당 날짜 교환
- 일정 변경 시 관련 사용자에게 SES 이메일 발송

정기 알림은 별도 Notification Lambda가 담당한다.

이번 프로젝트의 API Lambda는 Python 3.12를 사용한다.

---

# 2. Architecture

```text
Frontend
  ↓
CloudFront + S3
  ↓
Cognito Login
  ↓
API Gateway HTTP API
  ↓
JWT Authorizer
  ↓
API Lambda
  ↓
DynamoDB

Schedule Notification:
EventBridge → Notification Lambda → SES
````

JWT 검증은 API Gateway JWT Authorizer가 처리한다.

Frontend는 Cognito Access Token을 다음 형태로 전송한다.

```http
Authorization: Bearer <ACCESS_TOKEN>
```

Lambda에서는 검증된 Claims의:

```python
event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
```

를 사용하여 현재 사용자를 식별한다.

Lambda에서 JWT 자체를 검증하지 않는다.

---

# 3. 프로젝트 구조

```text
morning-duty-api/
├── spec.md
├── requirements.txt
├── src/
│   ├── lambda_function.py
│   ├── config.py
│   ├── logger.py
│   ├── services/
│   │   ├── calendar_service.py
│   │   ├── notification_service.py
│   │   └── schedule_service.py
│   ├── repositories/
│   │   ├── user_repository.py
│   │   └── calendar_repository.py
│   └── utils/
│       ├── auth.py
│       ├── response.py
│       └── date_utils.py
└── tests/
```

역할:

* `lambda_function.py`

  * routeKey 분기
  * Request parsing
  * Claims 추출
  * Service 호출
  * Response 반환

* `services/`

  * 비즈니스 로직

* `repositories/`

  * DynamoDB 접근

* `utils/`

  * 인증 Claims 처리
  * 날짜 처리
  * 공통 Response

---

# 4. DynamoDB

## Users

PK:

```text
userId
```

Attributes:

```text
userId
cognitoSub
name
email
employeeId
emailNotification
```

로그인 사용자는 JWT의 `sub`와 `cognitoSub`를 매칭해서 찾는다.

`cognitoSub` 조회를 위해 GSI 사용:

```text
IndexName: cognitoSub-index
PK: cognitoSub
```

Scan은 사용하지 않는다.

`cognitoSub`는 반드시 한 명의 User와만 매칭되어야 한다.
중복 발견 시 사용자를 임의 선택하지 않고 데이터 무결성 오류를 로그에 기록하며,
HTTP 500과 오류 코드 `INTERNAL_ERROR`를 반환한다.

---

## Calendar

PK:

```text
month
```

예:

```text
2026-09
```

SK:

```text
date
```

예:

```text
2026-09-24
```

Attributes:

```text
month
date

originalUserId
originalUserName

assignedUserId
assignedUserName

holiday
holidayName

jwd
holidayWorkRequest
```

의미:

* `originalUserId`, `originalUserName`

  * 최초 담당자
  * 변경 시 수정하지 않음

* `assignedUserId`, `assignedUserName`

  * 현재 실제 담당자
  * 일정 교환 시 이 값만 변경

* `jwd`, `holidayWorkRequest`

  * 현재 API에서는 사용하지 않음
  * 구조만 유지

---

# 5. API

MVP API는 3개만 구현한다.

```text
GET   /calendar
PATCH /me/notification
POST  /schedule/swap
```

---

# 6. GET /calendar

Request:

```http
GET /calendar?year=2026&month=9
```

반환 데이터:

```text
year
month
currentUser
navigation
days
changeCandidates
```

`currentUser`:

```text
userId
name
emailNotification
```

`navigation`:

```text
previousMonthAvailable
nextMonthAvailable
```

`days` 각 항목:

```text
date
dayOfWeek
holiday
holidayName
hasSchedule
assignedUserId
assignedUserName
mine
```

---

## Calendar 생성 규칙

DynamoDB Item만 그대로 반환하지 않는다.

예를 들어 DB에:

```text
09/24
09/25
09/28
```

만 있어도 Frontend는 9월 전체 날짜가 필요하다.

따라서:

```text
1. 요청한 월의 1일~말일 생성
2. DynamoDB에서 해당 month Query
3. 날짜 데이터와 DB 데이터 Merge
4. 주말/공휴일 계산
5. 사용자 담당 여부 계산
```

DB 데이터가 없는 날짜:

```text
hasSchedule = false
assignedUserId = null
assignedUserName = null
```

`hasSchedule`은 DB Item 존재 여부가 아닌 실제 담당자 배정 여부다.
`assignedUserId`가 있으면 `true`, 없거나 null/빈 값이면 `false`다.
DB Item이 있어도 담당자가 없으면 `hasSchedule = false`다.

---

## 공휴일

`jpholiday` 사용.

주말과 일본 공휴일을 판단한다.

DB에 holiday 정보가 있으면 우선 사용하고,
없으면 jpholiday로 계산한다.

---

## mine

```text
assignedUserId == currentUser.userId
```

이면 `true`.

Frontend에서는 `mine == true`일 때만 변경 UI를 보여준다.

단, 실제 변경 API에서도 다시 검증한다.

---

## 이전 / 다음 월

현재 Calendar Table에는 META Item을 만들지 않는다.

이전/다음 month PK로 Query하여 Item이 1개 이상 존재하면:

```text
previousMonthAvailable = true
nextMonthAvailable = true
```

로 판단한다.

---

## changeCandidates

별도 API를 만들지 않고 `/calendar` 응답에 포함한다.

조건:

```text
담당자가 존재함
현재 사용자 담당 날짜가 아님
주말 아님
공휴일 아님
과거 날짜 아님
```

조회 범위:

```text
현재 월
+
다음 월 데이터가 존재하면 다음 월
```

현재 월은 요청 월이 아니라 `Asia/Tokyo` 기준 실제 오늘이 속한 연·월이다.
과거 날짜 판단도 `Asia/Tokyo` 기준으로 한다.

Response는 UI 문자열이 아니라 구조화된 데이터로 반환한다.

예:

```json
{
  "date": "2026-10-01",
  "dayOfWeek": "THU",
  "assignedUserId": "USER#003",
  "assignedUserName": "松田"
}
```

---

# 7. PATCH /me/notification

Request:

```json
{
  "enabled": true
}
```

Frontend에서 원하는 상태를 명시적으로 보낸다.

toggle 방식은 사용하지 않는다.

`emailNotification`은 매일 아침 / 매주 일요일 Reminder 정기 알림에만 적용한다.
정기 알림 발송은 별도 Notification Lambda가 담당한다.
Schedule Swap 변경 알림에는 이 설정을 적용하지 않는다.

현재 사용자는 Request Body의 userId가 아니라 JWT `sub`로 식별한다.

Response:

```json
{
  "emailNotification": true
}
```

---

# 8. POST /schedule/swap

Request:

```json
{
  "myDate": "2026-09-24",
  "targetDate": "2026-10-01"
}
```

사용자 ID는 받지 않는다.

현재 사용자는 JWT Claims의 `sub`로 찾는다.

---

## 검증

`myDate`:

```text
날짜 존재
담당자 존재
assignedUserId == 현재 사용자
주말 아님
공휴일 아님
```

`targetDate`:

```text
날짜 존재
담당자 존재
현재 사용자 담당 날짜가 아님
주말 아님
공휴일 아님
과거 날짜 아님
```

Frontend 검증은 신뢰하지 않는다.

---

## Swap

다음 필드만 교환한다.

```text
assignedUserId
assignedUserName
```

다음 필드는 유지한다.

```text
originalUserId
originalUserName
```

DynamoDB `TransactWriteItems`를 사용하여
두 날짜 변경을 하나의 Transaction으로 처리한다.

가능하면 ConditionExpression으로 기존 담당자가 그대로인지 확인한다.

충돌 시:

```text
409 Conflict
```

성공 시 HTTP 200과 다음 최소 응답을 반환한다.

```json
{
  "success": true
}
```

Frontend는 이후 `GET /calendar`를 다시 호출하여 최신 상태를 조회한다.

---

## 변경 이메일

Transaction 성공 후 양쪽 사용자에게 SES 이메일을 발송한다.

변경 알림은 시스템성 중요 알림이므로 `emailNotification` 값과 관계없이 양쪽에 발송한다.
제목과 본문은 간결한 일본어로 작성하고, 수신자 본인의 변경 전·후 담당 날짜를 포함한다.

흐름:

```text
DynamoDB Transaction
↓
성공
↓
SES
```

SES 실패 때문에 Calendar 변경을 rollback하지 않는다.

이메일 실패는 CloudWatch Logs에 기록한다.

---

# 9. 화면 최신화

Swap 성공 후 Frontend는 Calendar 데이터를 직접 수정하지 않는다.

```text
POST /schedule/swap
↓
200
↓
GET /calendar
```

를 다시 호출하여 최신 데이터를 받는다.

---

# 10. Logging

Python `logging` 사용.

`print()`는 사용하지 않는다.

로그 예:

```text
event=calendar_requested
event=notification_updated
event=schedule_swap_requested
event=schedule_swap_success
event=schedule_swap_failed
```

필요한 값:

```text
userId
year
month
myDate
targetDate
result
reason
```

로그에 남기지 않을 것:

```text
Access Token
ID Token
Refresh Token
Authorization Header
Password
```

---

# 11. Error Response

공통 형식:

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "message"
  }
}
```

사용 Status:

```text
200 OK
400 Bad Request
401 Unauthorized
403 Forbidden
404 Not Found
409 Conflict
500 Internal Server Error
```

Stack Trace는 Response에 반환하지 않고 CloudWatch에 기록한다.

---

# 12. Lambda 구현 원칙

* Python 3.12 사용
* boto3 AWS Client는 가능하면 module global scope에서 생성
* Lambda Memory에 사용자 상태를 저장하지 않음
* AWS Resource 이름은 환경변수 사용
* Service / Repository 책임 분리
* 비즈니스 로직을 `lambda_function.py`에 몰아넣지 않음
* 과도한 Framework 사용 금지
* 요구사항에 없는 기능 추가 금지
* 테스트 가능한 구조로 작성

환경변수:

```text
USERS_TABLE_NAME
CALENDAR_TABLE_NAME
SES_FROM_EMAIL
AWS_REGION
```

---

# 13. requirements.txt

```text
jpholiday
```

로컬 테스트 필요 시:

```text
boto3
pytest
```

추가 가능.

---

# 14. 구현 순서

Phase 1:

```text
GET /calendar
```

Phase 2:

```text
PATCH /me/notification
```

Phase 3:

```text
POST /schedule/swap
```

Phase 4:

```text
SES 변경 알림
```

Phase 5: TODO — 현재 구현 범위와 요구사항이 정의되어 있지 않다. 별도 확인 후 구현한다.

---

# 15. Backlog

현재 구현하지 않는다.

```text
Excel Upload
S3 File Upload
Presigned URL
Excel 자동 동기화
관리자 화면
Schedule 생성 UI
SQS 비동기 알림
CloudWatch Dashboard
CI/CD
```

---

# 16. Codex 작업 지시

이 spec을 기준으로 구현한다.

중요:

* API Endpoint를 추가하지 않는다.
* DynamoDB 구조를 임의 변경하지 않는다.
* JWT 검증은 Lambda에서 직접 구현하지 않는다.
* 현재 사용자는 JWT Claims의 sub로 식별한다.
* Users 조회는 cognitoSub GSI를 사용한다.
* `/calendar`는 월 전체 날짜를 생성 후 DB 데이터와 Merge한다.
* Schedule Swap은 Transaction을 사용한다.
* `originalUser*`는 수정하지 않는다.
* 구현 중 애매한 부분은 추측하지 말고 TODO로 남긴다.

---

# 17. 변경 이력 — 2026-09-21

* 일정 교환 성공 후 보내는 일본어 이메일의 변경 전·후 날짜를 `YYYY年MM月DD日 ○曜日` 형식으로 변경했다. 월·일은 두 자리로 표시하며 요일은 실제 날짜에서 계산한다.
* 본문 마지막에 캘린더 URL을 추가했다. 선택 환경변수 `CALENDAR_URL`로 변경 가능하며, 미설정 또는 공백이면 `https://d88l94lniveng.cloudfront.net/`를 사용한다.
* `Config`에서 URL을 읽어 일정 교환 알림 서비스에 전달한다.
* 수신자별 교환 날짜, 요일(윤일 포함), 기본·사용자 지정 URL, 환경변수 연결을 테스트한다. SES 요청은 Mock 및 botocore Stubber로 검증한다.

변경 후 이메일 예시:

```text
朝礼の担当日が変更されました。

変更前: 2026年09月28日 月曜日
変更後: 2026年09月29日 火曜日

最新の予定はカレンダーでご確認ください。
https://d88l94lniveng.cloudfront.net/
```

검증 결과: 전체 테스트 **93개 통과**. 의존성과 수정 소스를 포함한 `dist/morning-duty-api-lambda.zip`을 빌드하고 ZIP 무결성 및 소스 일치를 확인했다. 실제 AWS 배포 및 SES 실발송은 수행하지 않았다.
