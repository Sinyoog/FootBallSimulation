# -*- coding: utf-8 -*-
"""PYTHONHASHSEED 누수 후보 정적 스캐너.

파이썬 3.7+ 에서 dict은 삽입순서를 보존하므로 순회 순서가 결정론적이다.
순서가 해시시드에 좌우되는 건 set/frozenset뿐이다. 그래서
  · set 리터럴 / set 컴프리헨션 / set(...) / .keys()-set 연산 결과가
  · list(...) 로 변환되거나, for 문에서 순회되거나,
    random.* 인자로 들어가거나, 반환/누적되는 곳
을 찾는다. sorted(...)로 감싸진 건 안전하므로 제외.
추가로 builtin hash() 직접 사용(문자열이면 시드 의존)도 신고한다.
"""
import ast
import os
import sys

SET_CALLS = {"set", "frozenset"}
SAFE_WRAPPERS = {"sorted", "len", "sum", "min", "max", "any", "all",
                 "frozenset", "set"}


class V(ast.NodeVisitor):
    def __init__(self, path, src):
        self.path = path
        self.lines = src.splitlines()
        self.hits = []
        self.func = "<module>"
        self.setvars = set()

    def _rep(self, node, kind, extra=""):
        line = self.lines[node.lineno - 1].strip() if node.lineno - 1 < len(self.lines) else ""
        self.hits.append((self.path, node.lineno, self.func, kind, line[:150], extra))

    def visit_FunctionDef(self, node):
        old, oldsets = self.func, self.setvars
        self.func = node.name
        self.setvars = set()
        # 1패스: 이 함수 안에서 set으로 대입되는 지역변수 수집
        for n in ast.walk(node):
            if isinstance(n, ast.Assign) and self._is_setexpr(n.value):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        self.setvars.add(t.id)
            elif isinstance(n, ast.AnnAssign) and n.value is not None \
                    and self._is_setexpr(n.value):
                if isinstance(n.target, ast.Name):
                    self.setvars.add(n.target.id)
        self.generic_visit(node)
        self.func, self.setvars = old, oldsets

    visit_AsyncFunctionDef = visit_FunctionDef

    def _is_setexpr(self, n):
        if isinstance(n, (ast.Set, ast.SetComp)):
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id in SET_CALLS:
            return True
        # a - b / a & b / a | b where either side looks like a set expr
        if isinstance(n, ast.Name) and n.id in self.setvars:
            return True
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Sub, ast.BitAnd, ast.BitOr, ast.BitXor)):
            return self._is_setexpr(n.left) or self._is_setexpr(n.right)
        return False

    def visit_Call(self, node):
        f = node.func
        # list(<set expr>)  / tuple(<set expr>)
        if isinstance(f, ast.Name) and f.id in ("list", "tuple") and node.args:
            if self._is_setexpr(node.args[0]):
                self._rep(node, "list(set...)")
        # random.*(<set expr>)
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                and f.value.id == "random":
            for a in node.args:
                if self._is_setexpr(a):
                    self._rep(node, f"random.{f.attr}(set...)")
        # builtin hash()
        if isinstance(f, ast.Name) and f.id == "hash":
            self._rep(node, "builtin hash()")
        self.generic_visit(node)

    def visit_For(self, node):
        if self._is_setexpr(node.iter):
            self._rep(node, "for x in set(...)")
        self.generic_visit(node)

    def visit_comprehension(self, node):
        if self._is_setexpr(node.iter):
            self._rep(node.iter, "comprehension over set")
        self.generic_visit(node)


def scan(root):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git", "qa_runs", "qa_gk", "qa_awards")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            try:
                src = open(p, encoding="utf-8").read()
                tree = ast.parse(src)
            except Exception as e:
                print("PARSE FAIL", rel, e)
                continue
            v = V(rel, src)
            v.visit(tree)
            hits.extend(v.hits)
    return hits


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = scan(root)
    hits.sort()
    for h in hits:
        print(f"{h[0]}:{h[1]}  [{h[3]}]  ({h[2]})  {h[4]}")
    print(f"\n총 {len(hits)}건")
