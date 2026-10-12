"""검증기가 진짜 잡는지 확인한다.
참고 엔진(tests/reference_engine.py)은 0~5단계 통과, 일부러 망가뜨린 엔진은 해당 단계가 FAIL 이어야 한다.

uv run --group demo python tests/check_pipeline.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import reference_engine as e  # noqa: E402


def bump_under_cylinder_2():
    """2번 실린더 아래에 블록 돌기: 0도에선 안 닿고 크랭크가 돌면 웹과 로드가 부딪힘."""
    orig = e.block
    e.block = lambda: orig().union(e.box(-22, -8, 11, 20, 10, 24))


CASES = [
    ("정상", None, None),
    ("얇은 웹 (구멍 둘레 0.5mm)", lambda: setattr(e, "WEB_W", 7.0), "1"),
    ("회전부 여유 0.1mm", lambda: setattr(e, "RUN", 0.1), "3"),
    ("웹 사이가 로드보다 좁음", lambda: setattr(e, "GAP", 3.6), "4"),
    ("회전 중 블록 돌기와 충돌", bump_under_cylinder_2, "5"),
]


def run(label, mutate, expect_fail):
    saved = dict(vars(e))
    if mutate:
        mutate()
    with tempfile.TemporaryDirectory() as tmp:
        e.main(tmp)
        p = subprocess.run([sys.executable, str(ROOT / "verify.py"), "--parts", tmp, "--skip", "6,7",
                            "--log", str(ROOT / "tests" / "reference_log.csv"), "--reports", tmp],
                           capture_output=True, text=True)
    vars(e).update(saved)
    failed = {ln.split("]")[1].strip().split(".")[0] for ln in p.stdout.splitlines() if ln.startswith("[FAIL]")}
    ok = failed == set() if expect_fail is None else expect_fail in failed
    print(f"{'OK ' if ok else 'BAD'} {label}: FAIL 단계 {sorted(failed) or '없음'}")
    return ok


if __name__ == "__main__":
    results = [run(*c) for c in CASES]
    sys.exit(0 if all(results) else 1)
