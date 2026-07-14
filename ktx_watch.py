# -*- coding: utf-8 -*-
"""KTX/SRT 취소표 감시 → 텔레그램 알림 봇.

사용자는 봇과의 1:1 대화에서 자유 형식으로 감시를 등록한다:
    서울 울산 7/25 17-20
    울산 서울 2026-07-27
    목록 / 해제 1 / 도움말

매진이던 열차에 자리가 생기면 등록한 사람에게 DM으로 알린다.

실행: python3 ktx_watch.py
필요 패키지: pip install korail2 SRTrain
"""
import json
import re
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
# 코레일 매크로 차단(DynaPath) 우회 패치가 적용된 korail2를 우선 사용한다.
# (pip의 korail2는 MACRO ERROR로 막히므로 프로젝트에 동봉한 버전을 먼저 로드)
sys.path.insert(0, str(BASE_DIR / "vendor"))
CONFIG_FILE = BASE_DIR / "config.json"
WATCHES_FILE = BASE_DIR / "watches.json"
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "ktx_watch.log"

# 사용자 입력 → (코레일 역명, SRT 역명). SRT 미정차역은 None.
STATIONS = {
    "서울": ("서울", None),
    "수서": (None, "수서"),
    "용산": ("용산", None),
    "광명": ("광명", None),
    "동탄": (None, "동탄"),
    "평택지제": (None, "평택지제"),
    "천안아산": ("천안아산", "천안아산"),
    "오송": ("오송", "오송"),
    "대전": ("대전", "대전"),
    "김천구미": ("김천(구미)", "김천구미"),
    "동대구": ("동대구", "동대구"),
    "경주": ("경주", "경주"),
    "신경주": ("경주", "경주"),
    "울산": ("울산(통도사)", "울산(통도사)"),
    "부산": ("부산", "부산"),
    "포항": ("포항", None),
}

WEEKDAY_KO = "월화수목금토일"
ERROR_NOTIFY_THRESHOLD = 5
MAX_WATCH_DAYS = 40  # 이 날짜 이후는 등록 거부 (예매 오픈 전)

HELP_TEXT = (
    "🚄 KTX/SRT 취소표 감시 봇\n\n"
    "감시 등록 — 출발역 도착역 날짜 [시간대] 순서로 보내주세요:\n"
    "  · 서울 울산 7/25 17-20\n"
    "  · 울산 서울 2026-07-27 (시간 생략 = 하루 종일)\n"
    "  · 서울 울산 내일 오후\n\n"
    "관리 명령:\n"
    "  · 목록 — 내 감시 목록 보기\n"
    "  · 해제 2 — 2번 감시 삭제\n"
    "  · 도움말 — 이 안내 다시 보기\n\n"
    "매진이던 열차에 자리가 나면 바로 알려드립니다.\n"
    "알림을 받으면 코레일톡/SRT 앱에서 직접 예매하세요."
)


def log(message):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ---------------------------------------------------------------- telegram


class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}"

    def _call(self, method, params, timeout=15):
        req = urllib.request.Request(
            f"{self.base}/{method}",
            data=json.dumps(params).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))

    def get_updates(self, offset, timeout=20):
        data = self._call(
            "getUpdates",
            {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 10,
        )
        return data.get("result", [])

    def send(self, chat_id, text):
        try:
            self._call("sendMessage", {"chat_id": chat_id, "text": text})
        except Exception as e:
            log(f"텔레그램 발송 실패 (chat_id={chat_id}): {e}")


# ---------------------------------------------------------------- 입력 파싱


def parse_date(token, today):
    token = token.strip()
    if token in ("오늘",):
        return today
    if token in ("내일",):
        return today + timedelta(days=1)
    if token in ("모레",):
        return today + timedelta(days=2)
    m = re.fullmatch(r"(?:(\d{4})[-./])?(\d{1,2})[-./월]\s*(\d{1,2})일?", token)
    if not m:
        return None
    year = int(m.group(1)) if m.group(1) else today.year
    try:
        parsed = date_cls(year, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if not m.group(1) and parsed < today:
        parsed = date_cls(year + 1, parsed.month, parsed.day)  # 연도 생략 시 다음 해로
    return parsed


def parse_time_range(text):
    """'17-20', '17:30~20:00', '오후' 등을 (분, 분)으로. 없으면 None."""
    text = text.strip()
    if text in ("오전",):
        return 5 * 60, 12 * 60
    if text in ("오후",):
        return 12 * 60, 19 * 60
    if text in ("저녁", "밤"):
        return 17 * 60, 24 * 60
    m = re.fullmatch(
        r"(\d{1,2})(?::(\d{2}))?시?\s*[-~부터에서]+\s*(\d{1,2})(?::(\d{2}))?시?(?:까지)?",
        text,
    )
    if not m:
        return None
    start = int(m.group(1)) * 60 + int(m.group(2) or 0)
    end = int(m.group(3)) * 60 + int(m.group(4) or 0)
    if not (0 <= start < end <= 24 * 60):
        return None
    return start, end


def parse_watch_request(text, today):
    """감시 등록 문장 해석. 성공 시 dict, 실패 시 (None, 안내문)."""
    cleaned = re.sub(r"[→>]+|->", " ", text)
    cleaned = re.sub(r"(\d{1,2})월\s+(\d{1,2})일?", r"\1월\2일", cleaned)  # '7월 25일' 붙이기
    tokens = cleaned.split()

    stations, date, time_range, unknown = [], None, None, []
    for token in tokens:
        name = token.rstrip("역")
        if name in STATIONS and len(stations) < 2:
            stations.append(name)
            continue
        if date is None:
            parsed = parse_date(token, today)
            if parsed:
                date = parsed
                continue
        if time_range is None:
            parsed = parse_time_range(token)
            if parsed:
                time_range = parsed
                continue
        unknown.append(token)

    if len(stations) < 2:
        known = ", ".join(STATIONS)
        return None, (
            "출발역과 도착역을 못 찾았어요.\n"
            "예: 서울 울산 7/25 17-20\n"
            f"인식 가능한 역: {known}"
        )
    if date is None:
        return None, "날짜를 알려주세요. 예: 서울 울산 7/25 또는 '내일'"
    if date < today:
        return None, "지난 날짜예요. 다시 확인해주세요."
    if date > today + timedelta(days=MAX_WATCH_DAYS):
        return None, f"{MAX_WATCH_DAYS}일 이후 날짜는 아직 예매 오픈 전이라 등록할 수 없어요."
    if unknown:
        return None, (
            f"이 부분을 이해하지 못했어요: {' '.join(unknown)}\n"
            "예: 서울 울산 7/25 17-20"
        )

    start, end = time_range if time_range else (0, 24 * 60)
    return {
        "dep": stations[0],
        "arr": stations[1],
        "date": f"{date:%Y-%m-%d}",
        "from_minute": start,
        "to_minute": end,
    }, None


# ---------------------------------------------------------------- 열차 조회


class TrainSearcher:
    """코레일/SRT 로그인 세션을 유지하며 (dep, arr, date)별 열차를 조회한다."""

    def __init__(self, config):
        self.config = config
        self.korail = None
        self.srt = None

    def _login_korail(self):
        from korail2 import Korail

        account = self.config.get("korail")
        if not account or not account.get("id"):
            return None
        self.korail = Korail(account["id"], account["password"], auto_login=True)
        return self.korail

    def _login_srt(self):
        from SRT import SRT

        account = self.config.get("srt")
        if not account or not account.get("id"):
            return None
        self.srt = SRT(account["id"], account["password"])
        return self.srt

    def search(self, dep, arr, date_str):
        """해당 구간/날짜의 열차 목록을 통합 반환.

        반환: [{key, carrier, train_no, dep_hm, arr_hm, minute,
                general, special}] (시간순)
        """
        korail_dep, srt_dep = STATIONS[dep]
        korail_arr, srt_arr = STATIONS[arr]
        yyyymmdd = date_str.replace("-", "")
        trains = []
        errors = []

        if korail_dep and korail_arr:
            try:
                trains += self._search_korail(korail_dep, korail_arr, yyyymmdd)
            except Exception as e:
                errors.append(f"KTX: {type(e).__name__} {e}")
        if srt_dep and srt_arr:
            try:
                trains += self._search_srt(srt_dep, srt_arr, yyyymmdd)
            except Exception as e:
                errors.append(f"SRT: {type(e).__name__} {e}")

        trains.sort(key=lambda t: t["minute"])
        return trains, errors

    def _search_korail(self, dep, arr, yyyymmdd):
        from korail2 import NoResultsError

        if self.korail is None and self._login_korail() is None:
            return []
        # search_train은 시각 기준 ~10편만 반환하므로, 하루 전체를 훑는
        # search_train_allday를 써야 낮/저녁 열차까지 모두 본다.
        try:
            found = self.korail.search_train_allday(
                dep, arr, date=yyyymmdd, time="000000", include_no_seats=True
            )
        except NoResultsError:
            return []
        except Exception:
            # 세션 만료 가능성 → 재로그인 후 1회 재시도
            self._login_korail()
            try:
                found = self.korail.search_train_allday(
                    dep, arr, date=yyyymmdd, time="000000", include_no_seats=True
                )
            except NoResultsError:
                return []
        result = []
        for t in found:
            if t.train_group != "100":  # KTX 계열만 (100=KTX)
                continue
            result.append(self._entry(
                "KTX", t.train_no, t.dep_time, t.arr_time,
                t.has_general_seat(), t.has_special_seat(),
            ))
        return result

    def _search_srt(self, dep, arr, yyyymmdd):
        if self.srt is None and self._login_srt() is None:
            return []
        try:
            found = self.srt.search_train(
                dep, arr, date=yyyymmdd, time="000000", available_only=False
            )
        except Exception as e:
            if "결과가 없" in str(e) or "조회 내역이 없" in str(e):
                return []
            self._login_srt()
            found = self.srt.search_train(
                dep, arr, date=yyyymmdd, time="000000", available_only=False
            )
        return [
            self._entry(
                "SRT", t.train_number, t.dep_time, t.arr_time,
                t.general_seat_available(), t.special_seat_available(),
            )
            for t in found
        ]

    @staticmethod
    def _entry(carrier, train_no, dep_hhmmss, arr_hhmmss, general, special):
        return {
            "key": f"{carrier}-{train_no}",
            "carrier": carrier,
            "train_no": train_no,
            "dep_hm": f"{dep_hhmmss[:2]}:{dep_hhmmss[2:4]}",
            "arr_hm": f"{arr_hhmmss[:2]}:{arr_hhmmss[2:4]}",
            "minute": int(dep_hhmmss[:2]) * 60 + int(dep_hhmmss[2:4]),
            "general": bool(general),
            "special": bool(special),
        }


# ---------------------------------------------------------------- 감시 로직


def minute_str(minute):
    return f"{minute // 60:02d}:{minute % 60:02d}"


def watch_label(watch):
    d = datetime.strptime(watch["date"], "%Y-%m-%d")
    time_part = (
        "하루 종일"
        if watch["from_minute"] == 0 and watch["to_minute"] == 24 * 60
        else f"{minute_str(watch['from_minute'])}~{minute_str(watch['to_minute'])}"
    )
    return (
        f"{watch['dep']}→{watch['arr']} "
        f"{d.month}/{d.day}({WEEKDAY_KO[d.weekday()]}) {time_part}"
    )


def seat_text(train):
    parts = []
    if train["general"]:
        parts.append("일반실O")
    if train["special"]:
        parts.append("특실O")
    return " ".join(parts) if parts else "매진"


def trains_in_window(trains, watch):
    return [
        t for t in trains if watch["from_minute"] <= t["minute"] < watch["to_minute"]
    ]


def sweep(searcher, telegram, watches, state, config):
    """모든 감시 건을 검사해서 새로 자리 난 열차를 알린다."""
    today = datetime.now().date()
    now_minute = datetime.now().hour * 60 + datetime.now().minute

    # 만료된 감시 정리
    expired = []
    for w in watches[:]:
        w_date = datetime.strptime(w["date"], "%Y-%m-%d").date()
        if w_date < today or (w_date == today and w["to_minute"] <= now_minute):
            watches.remove(w)
            state["seen"].pop(str(w["id"]), None)
            expired.append(w)
    for w in expired:
        telegram.send(w["chat_id"], f"⏰ 기간이 지나 감시를 종료했어요: {watch_label(w)}")

    # 같은 구간/날짜는 한 번만 조회
    routes = {}
    for w in watches:
        routes.setdefault((w["dep"], w["arr"], w["date"]), None)
    all_errors = []
    for route in routes:
        routes[route], errors = searcher.search(*route)
        all_errors += errors
        time.sleep(config.get("request_gap_seconds", 1))

    for w in watches:
        trains = routes[(w["dep"], w["arr"], w["date"])]
        if trains is None:
            continue
        seen = state["seen"].setdefault(str(w["id"]), {})
        newly_open = []
        is_today = w_date_is_today = w["date"] == f"{today:%Y-%m-%d}"
        for t in trains_in_window(trains, w):
            if is_today and t["minute"] <= now_minute:
                continue  # 이미 떠난 열차
            available = t["general"] or t["special"]
            was_available = seen.get(t["key"])
            if available and was_available is False:  # 매진 → 자리
                newly_open.append(t)
            seen[t["key"]] = available
        if newly_open:
            lines = "\n".join(
                f"· {t['carrier']} {t['train_no']} "
                f"{t['dep_hm']}→{t['arr_hm']} {seat_text(t)}"
                for t in newly_open
            )
            telegram.send(
                w["chat_id"],
                f"🚄 취소표 발견! {watch_label(w)}\n{lines}\n\n"
                f"지금 바로 {'SRT 앱' if all(t['carrier'] == 'SRT' for t in newly_open) else '코레일톡/SRT 앱'}에서 예매하세요!",
            )
            log(f"감시#{w['id']} 알림: {[t['key'] for t in newly_open]}")

    return all_errors


# ---------------------------------------------------------------- 명령 처리


def handle_message(text, chat_id, watches, state, searcher, telegram, config):
    text = text.strip()
    allowed = config.get("allowed_chat_ids")
    if allowed and str(chat_id) not in [str(c) for c in allowed]:
        telegram.send(chat_id, "이 봇을 사용할 권한이 없어요.")
        return

    lower = text.lower().lstrip("/")
    if lower in ("start", "help", "도움말", "도움", "사용법", "?"):
        telegram.send(chat_id, HELP_TEXT)
        return

    if lower in ("목록", "리스트", "list", "감시목록"):
        mine = [w for w in watches if w["chat_id"] == chat_id]
        if not mine:
            telegram.send(chat_id, "등록된 감시가 없어요.\n예: 서울 울산 7/25 17-20")
        else:
            lines = "\n".join(f"#{w['id']} {watch_label(w)}" for w in mine)
            telegram.send(chat_id, f"📋 내 감시 목록\n{lines}\n\n삭제: 해제 번호")
        return

    m = re.fullmatch(r"/?(?:해제|삭제|취소|remove|del)\s*#?(\d+)", text)
    if m:
        watch_id = int(m.group(1))
        target = next(
            (w for w in watches if w["id"] == watch_id and w["chat_id"] == chat_id),
            None,
        )
        if target:
            watches.remove(target)
            state["seen"].pop(str(watch_id), None)
            telegram.send(chat_id, f"🗑 해제했어요: {watch_label(target)}")
        else:
            telegram.send(chat_id, f"#{watch_id} 감시를 찾을 수 없어요. '목록'으로 확인해보세요.")
        return

    # 감시 등록 시도
    parsed, error = parse_watch_request(text, datetime.now().date())
    if error:
        telegram.send(chat_id, error)
        return

    state["next_id"] = state.get("next_id", 0) + 1
    watch = {"id": state["next_id"], "chat_id": chat_id, **parsed}
    watches.append(watch)

    # 즉시 1회 조회해서 현재 상태를 알려주고, 이후 비교 기준으로 저장
    trains, errors = searcher.search(watch["dep"], watch["arr"], watch["date"])
    in_window = trains_in_window(trains, watch)
    seen = state["seen"].setdefault(str(watch["id"]), {})
    for t in in_window:
        seen[t["key"]] = t["general"] or t["special"]

    reply = f"✅ 감시 #{watch['id']} 등록: {watch_label(watch)}\n"
    if errors and not trains:
        reply += "⚠️ 지금 조회에 실패해서 현재 상태를 못 보여드려요. 감시는 계속합니다."
    elif not in_window:
        reply += "이 시간대에 운행하는 열차가 없어요. 열차가 생기면 알려드릴게요."
    else:
        open_now = [t for t in in_window if t["general"] or t["special"]]
        lines = "\n".join(
            f"· {t['carrier']} {t['train_no']} {t['dep_hm']}→{t['arr_hm']} {seat_text(t)}"
            for t in in_window
        )
        reply += f"현재 상태 (열차 {len(in_window)}편):\n{lines}\n\n"
        if open_now:
            reply += "지금 바로 예매 가능한 열차가 있어요! 매진분은 자리 나면 알려드릴게요."
        else:
            reply += "전부 매진이네요. 자리가 나면 바로 알려드릴게요."
    telegram.send(chat_id, reply)
    log(f"감시#{watch['id']} 등록 (chat={chat_id}): {watch_label(watch)}")


# ---------------------------------------------------------------- 메인 루프


def main():
    config = load_json(CONFIG_FILE, None)
    if config is None or "여기에" in config.get("telegram_token", "여기에"):
        sys.exit("config.json에 telegram_token 등을 먼저 설정하세요.")

    telegram = Telegram(config["telegram_token"])
    searcher = TrainSearcher(config)
    watches = load_json(WATCHES_FILE, [])
    state = load_json(STATE_FILE, {"offset": 0, "next_id": 0, "seen": {}})

    interval = config.get("interval_seconds", 180)
    admin = config.get("admin_chat_id")
    last_sweep = 0.0
    consecutive_failures = 0

    log(f"시작: 감시 {len(watches)}건 로드")
    while True:
        # 1) 텔레그램 명령 처리 (long polling이 루프 주기를 겸함)
        try:
            updates = telegram.get_updates(state["offset"], timeout=20)
            for update in updates:
                state["offset"] = update["update_id"] + 1
                message = update.get("message") or {}
                text = message.get("text")
                chat_id = (message.get("chat") or {}).get("id")
                if text and chat_id:
                    try:
                        handle_message(
                            text, chat_id, watches, state,
                            searcher, telegram, config,
                        )
                    except Exception:
                        log(f"명령 처리 오류: {traceback.format_exc()}")
                        telegram.send(chat_id, "처리 중 오류가 났어요. 다시 시도해주세요.")
            save_json(WATCHES_FILE, watches)
            save_json(STATE_FILE, state)
        except Exception as e:
            log(f"텔레그램 폴링 오류: {e}")
            time.sleep(10)

        # 2) 주기 도래 시 열차 검사
        if watches and time.time() - last_sweep >= interval:
            last_sweep = time.time()
            try:
                errors = sweep(searcher, telegram, watches, state, config)
                save_json(WATCHES_FILE, watches)
                save_json(STATE_FILE, state)
                if errors:
                    consecutive_failures += 1
                    log(f"조회 오류({consecutive_failures}회 연속): {errors}")
                else:
                    consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                log(f"검사 실패({consecutive_failures}회 연속): {traceback.format_exc()}")
            if consecutive_failures == ERROR_NOTIFY_THRESHOLD and admin:
                telegram.send(
                    admin,
                    f"⚠️ KTX 감시 봇이 {ERROR_NOTIFY_THRESHOLD}회 연속 조회에 실패했어요. "
                    "서버 로그를 확인해주세요.",
                )


if __name__ == "__main__":
    main()
