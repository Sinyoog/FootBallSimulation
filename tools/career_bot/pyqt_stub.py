"""PyQt6 가짜 모듈 — 화면 코드를 창 없이 실행하기 위한 시험 전용(게임 코드와 무관). 모든 위젯·상수는 아무 일도 안 하는
더미이고, QPushButton은 글자·활성 상태·연결된 함수를 기억한다. QDialog.exec()는 '계약 수락' 버튼을 누른 것처럼 동작한다."""
import sys, types

BUTTONS = []


class _Sig:
    def __init__(self): self.cbs = []
    def connect(self, cb): self.cbs.append(cb)
    def emit(self, *a): [cb(*a) for cb in self.cbs]


class _D:
    def __init__(self, *a, **k):
        self._text = a[0] if a and isinstance(a[0], str) else ""
        self._enabled = True
        self.clicked = _Sig(); self.currentIndexChanged = _Sig(); self.finished = _Sig(); self.timeout = _Sig()
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        return _D()
    def __call__(self, *a, **k): return _D()
    def __or__(self, o): return self
    def __iter__(self): return iter(())
    def __bool__(self): return True
    def __int__(self): return 0
    def setText(self, t): self._text = t
    def text(self): return self._text
    def setEnabled(self, v): self._enabled = bool(v)
    def isEnabled(self): return self._enabled
    def currentData(self): return None
    def count(self): return 0
    def currentIndex(self): return 0
    def exec(self):
        for b in list(BUTTONS):
            if "계약 수락" in (b._text or "") and b._enabled:
                for cb in b.clicked.cbs:
                    cb()
                return 1
        return 0


class QPushButton(_D):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); BUTTONS.append(self)


class QDialog(_D):
    pass


def _mod(name):
    m = types.ModuleType(name)
    m.__getattr__ = lambda n: {"QPushButton": QPushButton, "QDialog": QDialog}.get(n, _D)
    return m


def install():
    pkg = _mod("PyQt6"); sys.modules["PyQt6"] = pkg
    for sub in ("QtWidgets", "QtCore", "QtGui"):
        m = _mod(f"PyQt6.{sub}"); sys.modules[f"PyQt6.{sub}"] = m; setattr(pkg, sub, m)
    sys.modules["PyQt6.QtCore"].pyqtSignal = lambda *a, **k: _Sig()
