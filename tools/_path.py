# -*- coding: utf-8 -*-
"""tools/ 안의 스크립트가 프로젝트 루트 모듈(database, game_engine 등)을
그냥 import 할 수 있게 해주는 부트스트랩.

`python3 tools/foo.py` 로 실행하면 sys.path[0]이 tools/ 가 되기 때문에
`import database` 가 실패한다. 각 스크립트 맨 위에서
    import _path   # noqa: F401
한 줄만 넣어두면 이 모듈이 로드되면서 루트를 sys.path에 끼워 넣는다.

ROOT 를 직접 쓰고 싶을 때는 `import _path` 후 `_path.ROOT` 로 접근한다
(game.db 경로 등).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
