"""
RNG 소비량 계측 전용 스크립트(재사용 가능한 진단 도구). 게임 코드는 전혀
안 건드리고, random 모듈의 모든 진입점(random/randint/choice/choices/
shuffle/uniform/gauss/sample/randrange/triangular/betavariate/
expovariate/getrandbits/normalvariate)을 카운팅 wrapper로 감싼 뒤,
ai_lifecycle.py의 각 단계 함수를 monkeypatch해서 전/후 소비량을 찍는다.

[2026-08 버그수정] 최초 버전은 random.choices()(가중치 버전, 복수형)를
빠뜨리고 있어서 실제로는 RNG 스트림이 갈린 지점인데도 계측상으로는
"일치"하는 것처럼 잘못 보이는 문제가 있었다 — 이번에 전체 진입점을
빠짐없이 감싸도록 고쳤다.

용도: 두 실행 간 RNG 소비 지점 차이를 좁혀서 찾는 디버깅 도구. 코드베이스
전체의 완전한 bit-level 결정론을 만드는 게 목적이 아니라(그건 별도
과제로 분리하기로 함), 특정 divergence를 조사할 때 재사용한다.

사용: python3 rng_probe.py <db_path> [시즌수]
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import os
import random
import sys

_counters = {"random": 0, "randint": 0, "choice": 0, "choices": 0, "shuffle": 0,
             "uniform": 0, "gauss": 0, "sample": 0, "randrange": 0,
             "triangular": 0, "betavariate": 0, "expovariate": 0,
             "getrandbits": 0, "normalvariate": 0}

_orig = {name: getattr(random, name) for name in _counters}


def _wrap(name):
    orig = _orig[name]
    def wrapper(*a, **kw):
        _counters[name] += 1
        return orig(*a, **kw)
    return wrapper


for _name in _counters:
    setattr(random, _name, _wrap(_name))


def total():
    return sum(_counters.values())


def snapshot(label):
    print(f"[RNG-PROBE] {label}: total={total()} detail={dict(_counters)}")


db_path = sys.argv[1] if len(sys.argv) > 1 else "game.db"
seasons = int(sys.argv[2]) if len(sys.argv) > 2 else 1

import database
database.DB_PATH = os.path.abspath(db_path)
database.init_db()
database.flush_to_disk_async = lambda: None  # 헤드리스 결정론 모드 — 백그라운드 저장 비활성화

import game_engine as ge
import ai_lifecycle

random.seed(12345)

p = ge.get_player()
if not p:
    ge.create_player(name="Headless Dummy", position="CM", sub_role="")

# ai_lifecycle 내부 단계 함수들을 감싸서 전/후 스냅샷을 찍는다.
_orig_age = ai_lifecycle._age_and_progress
_orig_retire = ai_lifecycle._retire_and_replace
_orig_transfer = ai_lifecycle._transfer_market
_orig_shuffle_f = ai_lifecycle._shuffle_formations


def _wrapped_age(*a, **kw):
    snapshot("age_and_progress 진입 전")
    r = _orig_age(*a, **kw)
    snapshot("age_and_progress 완료 후")
    return r


def _wrapped_retire(*a, **kw):
    snapshot("retire_and_replace 진입 전")
    r = _orig_retire(*a, **kw)
    snapshot("retire_and_replace 완료 후")
    return r


def _wrapped_transfer(*a, **kw):
    snapshot("transfer_market 진입 전")
    r = _orig_transfer(*a, **kw)
    snapshot("transfer_market 완료 후")
    return r


def _wrapped_shuffle_f(*a, **kw):
    snapshot("shuffle_formations 진입 전")
    r = _orig_shuffle_f(*a, **kw)
    snapshot("shuffle_formations 완료 후")
    return r


ai_lifecycle._age_and_progress = _wrapped_age
ai_lifecycle._retire_and_replace = _wrapped_retire
ai_lifecycle._transfer_market = _wrapped_transfer
ai_lifecycle._shuffle_formations = _wrapped_shuffle_f

_orig_gen_sched_all = ge._generate_all_league_schedules


def _wrapped_gen_sched_all(*a, **kw):
    snapshot("_generate_all_league_schedules 진입 전")
    r = _orig_gen_sched_all(*a, **kw)
    snapshot("_generate_all_league_schedules 완료 후")
    return r


ge._generate_all_league_schedules = _wrapped_gen_sched_all

import intl_engine

schedule = [(d, "휴식", {}) for d in range(1, 365)]
for s in range(seasons):
    snapshot(f"시즌{s+1} advance_days 시작 전")
    remaining = list(schedule)
    for _guard in range(20):
        ge.advance_days(remaining)
        pending = intl_engine.get_pending_choice()
        if not pending:
            break
        for opt in pending.get("options", []):
            intl_engine.decline_national_team(opt["tournament_id"])
        cur_day = ge.get_state().get("current_day")
        remaining = [item for item in schedule if item[0] >= cur_day]
        if not remaining:
            break
    snapshot(f"시즌{s+1} advance_days 종료 후")

database.flush_to_disk()
snapshot("전체 종료")