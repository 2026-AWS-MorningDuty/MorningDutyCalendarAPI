# Morning Duty API — Phase 1 / Phase 2 / Phase 3 / Phase 4

Python 3.12의 `GET /calendar?year=2026&month=9` 구현입니다.

`PATCH /me/notification`은 `{"enabled": true}` 또는 `{"enabled": false}`를 받아
로그인 사용자의 알림 설정을 저장하고 `{"emailNotification": true}` 또는 `{"emailNotification": false}`를 반환합니다.
사용자는 검증된 JWT `sub`로 식별하며, body의 사용자 ID는 사용하지 않습니다.

`POST /schedule/swap`은 `{"myDate": "2026-09-24", "targetDate": "2026-10-01"}`을 받습니다.
두 필드 외의 입력은 허용하지 않으며, 로그인 사용자의 담당 일정과 다른 사용자의 일정을 검증한 뒤
`assignedUserId`, `assignedUserName`만 하나의 DynamoDB 트랜잭션으로 교환합니다.
`originalUserId`, `originalUserName`과 나머지 속성은 유지됩니다.
주말·휴일·미배정 일정은 교환할 수 없고, `targetDate`는 도쿄 기준 오늘 이후여야 합니다.
명세대로 과거 날짜 제한은 `targetDate`에만 적용하며, 교환 가능 월의 추가 제한은 두지 않습니다.
읽은 담당자·이름·휴일 값이 변경되거나 항목이 삭제된 경우 `409`를 반환합니다.
성공하면 `200`과 `{"success": true}`를 반환하며, 화면은 `GET /calendar`로 다시 조회합니다.
교환 트랜잭션 성공 후 양쪽 사용자에게 SES 이메일을 각각 발송합니다.
수신자는 Users 테이블의 `userId`로 조회하며, 각 이메일에는 본인의 변경 전·후 담당 날짜가 포함됩니다.
사용자 조회·SES 초기화·발송 실패는 CloudWatch Logs에 기록하고, 다른 수신자 처리는 계속합니다.
이메일 실패로 일정을 되돌리거나 교환 성공 응답을 실패로 변경하지 않습니다.
실패한 이메일의 별도 재발송 큐는 포함하지 않습니다.

## 실행 환경

- Lambda 배포 패키지 루트에 `src/`의 내용과 `requirements.txt` 의존성을 배치합니다.
- Handler: `lambda_function.lambda_handler`
- 환경변수: `USERS_TABLE_NAME`, `CALENDAR_TABLE_NAME`, `AWS_REGION` (도쿄: `ap-northeast-1`), `SES_FROM_EMAIL`.
- API Gateway HTTP API의 JWT Authorizer를 연결해야 합니다. Lambda는 검증된 claims의 `sub`만 사용합니다.
- IAM에는 Users 테이블의 `cognitoSub-index` 및 Calendar 테이블에 대한 `dynamodb:Query` 권한이 필요합니다.
- 알림 설정 변경에는 Users 테이블의 `dynamodb:UpdateItem` 권한도 필요합니다.
- 일정 교환에는 Calendar 테이블의 `dynamodb:GetItem`, `dynamodb:UpdateItem` 권한이 필요합니다.
- 변경 이메일에는 Users 테이블의 `dynamodb:GetItem`, 발신 자격 증명에 대한 `ses:SendEmail` 권한이 필요합니다.
- `SES_FROM_EMAIL`은 해당 리전에서 SES 발신에 사용할 수 있는 주소로 설정합니다. 누락되면 교환은 성공하고 이메일 실패가 로그에 기록됩니다.
- 변경 이메일 날짜는 `2026年09月28日 月曜日` 형식이며, 마지막에 캘린더 링크를 표시합니다. 선택 환경변수 `CALENDAR_URL`로 URL을 지정할 수 있고, 미설정 또는 공백이면 `https://d88l94lniveng.cloudfront.net/`를 사용합니다.
- 날짜 기준은 `Asia/Tokyo`입니다. 후보는 실제 오늘이 속한 월과 다음 월에서 조회하고, 월 이동 가능 여부는 요청 월 기준입니다.
- 성공 응답은 명세의 `year`, `month`, `currentUser`, `navigation`, `days`, `changeCandidates`를 직접 반환합니다.

## 로컬 테스트

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

테스트는 Repository 경계를 모킹하므로 AWS 자격 증명이나 실제 테이블이 필요 없습니다.

## 성능 로그

CloudWatch Logs에서 `[PERF]` 또는 `[PERF] route=GET /calendar`로 검색합니다.
로그 형식은 `[PERF] route=<ROUTE> step=<STEP> duration_ms=<밀리초>`입니다.
세 API 모두 성공·실패 시 `step=total`을 기록하며, Handler 내부 시간만 측정하므로 Cold Start 초기화 시간은 Lambda 기본 로그와 함께 확인합니다.

DB 접근, 검증, 캘린더 계산, 응답 직렬화(`build_response`), SES 발송을 각각 측정합니다.
후보 조회(`query_change_candidates`), 날짜 계산(`build_calendar`), 교체 검증(`validate_swap`)은 실행 구간마다 같은 step이 여러 번 기록될 수 있습니다.
알림 사용자 조회(`get_notification_users`)와 SES 발송(`send_email`)은 수신자별로 기록합니다.
실행하지 않은 구간은 기록하지 않으며, 같은 step의 전체 비용은 해당 요청 내 기록을 합산해서 확인합니다.
성능 로그에는 route, step, duration_ms만 포함합니다.

## 확정 정책

- `hasSchedule`은 실제 담당자 배정 여부입니다. DB Item이 있어도 `assignedUserId`가 없거나 null/빈 값이면 `false`입니다.
- 중복 `cognitoSub`는 데이터 무결성 오류로 로그에 기록하고 `500 / INTERNAL_ERROR`를 반환합니다.
- 일정 교환 성공 응답은 `200 {"success": true}`입니다.
- `emailNotification`은 별도 Notification Lambda의 매일 아침 / 매주 일요일 Reminder에만 적용합니다. 변경 이메일은 설정과 관계없이 양쪽에 발송합니다.
- 변경 이메일 제목·본문은 일본어이며, 본인의 변경 전·후 담당 날짜를 안내합니다.

## 구현 범위 점검

- Phase 1~4 API Lambda 기능은 구현되어 있습니다. 단위 테스트와 botocore Stubber로 검증합니다.
- Phase 5는 명세에 기능 정의가 없어 요구사항 확인이 필요합니다.
- 정기 Reminder는 별도 Notification Lambda의 역할이며, 이 저장소의 API Lambda에 포함하지 않습니다.
- Frontend, AWS 배포·IAM·API Gateway/Cognito 연결, 실제 DynamoDB·SES 통합 검증은 이 구현에 포함되지 않습니다.
- 명세의 Backlog 항목은 구현 대상에서 제외되어 있습니다.

배포 인프라는 포함하지 않습니다. 실제 SES 발송 없이 Mock과 botocore Stubber로 검증합니다.
# MorningDutyCalendarAPI
