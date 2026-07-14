# ktx-watch

KTX/SRT 취소표가 나면 텔레그램으로 알려주는 봇. 여러 명이 각자 봇과의 1:1 대화에서
원하는 구간/날짜를 등록하고, 자리가 나면 등록한 본인에게만 DM이 온다.

## 사용법 (텔레그램에서)

봇과 1:1 대화방을 열고 START를 누른 뒤:

| 보낼 메시지 | 동작 |
|---|---|
| `서울 울산 7/25 17-20` | 감시 등록 (7/25 17~20시 출발 열차) |
| `울산 서울 2026-07-27` | 감시 등록 (시간 생략 = 하루 종일) |
| `서울 울산 내일 오후` | 오늘/내일/모레, 오전/오후/저녁도 인식 |
| `목록` | 내 감시 목록 |
| `해제 2` | 2번 감시 삭제 |
| `도움말` | 사용법 안내 |

- 지난 날짜/시간의 감시는 자동 삭제되고 안내 메시지가 온다
- 등록 즉시 현재 좌석 상황을 보여주고, 이후 **매진 → 자리** 전환 시에만 알림
- KTX(코레일)와 SRT를 모두 조회하며 일반실/특실 구분 표시

## 동작 방식

- 텔레그램 long polling으로 명령을 받고, 3분(`interval_seconds`)마다 등록된 구간을 조회
- 같은 구간/날짜는 여러 명이 등록해도 1번만 조회 (계정 부담 최소화)
- 코레일톡/SRT 앱의 내부 API를 사용 (`korail2`, `SRTrain` 라이브러리)
- 5회 연속 조회 실패 시 관리자(`admin_chat_id`)에게 알림

## 설정 (config.json)

`config.example.json`을 `config.json`으로 복사 후 작성:

| 키 | 설명 |
|---|---|
| `telegram_token` | 텔레그램 봇 토큰 |
| `admin_chat_id` | 시스템 에러 알림을 받을 본인 chat id |
| `allowed_chat_ids` | 사용 허용 chat id 목록. `null` = 누구나 사용 가능 |
| `korail.id / password` | 코레일 멤버십 로그인 정보 (조회 전용) |
| `srt.id / password` | SRT 로그인 정보. 계정 없으면 `"srt": null` |
| `interval_seconds` | 검사 주기 (기본 180초) |

**config.json은 비밀번호가 들어 있으므로 git에 커밋 금지** (.gitignore 처리됨)

## 배포 (Oracle Cloud)

최초 1회, 서버에서:

```bash
mkdir -p ~/ktx-watch
pip3 install korail2 SRTrain    # 안 되면: sudo apt install python3-pip 먼저
```

PC에서 파일 업로드 + 서비스 등록은 naver-booking-watch와 동일한 패턴:

```powershell
scp -i <키> ktx_watch.py config.json ubuntu@<IP>:~/ktx-watch/
scp -i <키> deploy\ktx-watch.service ubuntu@<IP>:~/
```

```bash
sudo mv ~/ktx-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ktx-watch
```

이후 코드 수정 반영: `powershell -ExecutionPolicy Bypass -File deploy\redeploy.ps1`

## 주의

- 알림 전용이다. 예매는 본인이 코레일톡/SRT 앱에서 직접 한다 (자동 예매는 법적 문제 소지가 있어 만들지 않음)
- 조회 간격을 너무 줄이면 (60초 미만) 코레일/SR 쪽에서 계정·IP가 차단될 수 있다
