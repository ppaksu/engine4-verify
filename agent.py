"""요청 입력 → Claude가 엔진 코드 작성/수정 → 생성 → 검증 → 실패하면 분석을 붙여 통과할 때까지 재요청.

make  (또는 uv run agent.py)
engine/make_engine.py 가 없으면 첫 요청으로 처음부터 만든다.
빈 줄 입력 = AI 없이 현재 코드로 생성, 검증, 그림만.
매 시도는 prompts/log.csv 에 한 줄씩, 코드는 prompts/attempts/ 에 남는다.
멈추려면 Ctrl+C: 요청 전 상태로 되돌린다. 횟수 제한이 필요하면 MAX_TRIES=5 make.
"""
import csv
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / "engine" / "make_engine.py"
PARTS = ROOT / "parts"
LOG = ROOT / "prompts" / "log.csv"
ATTEMPTS = ROOT / "prompts" / "attempts"
MAX_TRIES = int(os.environ.get("MAX_TRIES", 0))   # 0 = 통과할 때까지
MODEL = os.environ.get("CLAUDE_MODEL", "opus")

SYSTEM = """너는 학교 3D 프린팅 과제를 돕는 CAD 엔지니어다.
주제: 손으로 돌리는 4기통 엔진 원리 모형. 파이썬 CadQuery 코드 파일 하나(engine/make_engine.py)를
요청에 따라 처음부터 쓰거나 고친다. 무엇을 어떤 치수로 만들지는 요청을 따른다.

반드시 지킬 것:
- 답은 make_engine.py 전체를 ```python 코드 블록 하나로만 낸다. 블록 밖 설명은 3줄 이내.
- 검증기가 읽는 형식. 이 뼈대를 그대로 따른다:
```python
OUT = Path(__file__).resolve().parent.parent / "parts"

def build():
    # parts: {이름: (cq.Workplane, {"name": 이름, "type": "functional" 또는 "exterior", "motion": ...})}
    # fits:  [{"name": "핀 / 구멍", "pin": 핀 부품, "hole": 구멍 부품, "at": [x, y, z], "axis": "x", "kind": "running"}]
    # kin:   {"r": 크랭크 반경, "L": 로드 중심거리, "cylinders": [{"id": 1, "x": 실린더 x, "phase": 0}, ...],
    #         "firing_order": [1, 3, 4, 2]}  (점화 순서, 그림에 쓰임)
    return parts, fits, kin

def main(out=OUT):
    out = Path(out)
    out.mkdir(exist_ok=True)
    parts, fits, kin = build()
    for name, (shape, _) in parts.items():
        cq.exporters.export(shape, str(out / f"{name}.step"))
        cq.exporters.export(shape, str(out / f"{name}.stl"), tolerance=0.01, angularTolerance=0.1)
    meta = {"kinematics": kin, "parts": [m for _, m in parts.values()], "fits": fits}
    (out / "assembly.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
```
- 좌표: 크랭크축 = 원점을 지나는 X축, 실린더 = +Z. 모든 부품은 크랭크 0도(1번 상사점) 조립 위치로 저장.
- 검증기의 운동 모델: 실린더 i 의 크랭크핀 중심 = (x, -r*sin(phase), r*cos(phase)),
  피스톤 핀 중심 = (x, 0, 크랭크핀 z + sqrt(L^2 - 크랭크핀 y^2)). 로드 큰쪽은 크랭크핀, 작은쪽은 피스톤 핀에 둔다.
- motion: fixed / crank / rod:<번호> / piston:<번호>. type: functional(기능, STEP+STL) / exterior(겉면, STL).
- fits: 원형 핀과 구멍마다 at(조립 좌표의 단면 위치), axis(x/y/z), kind(running/sliding/press/locating).
  press 쌍은 겹쳐도 되고, 나머지 부품 쌍은 크랭크가 한 바퀴 도는 동안 겹치면 안 된다.
- 코드는 parts/ 폴더 안에만 파일을 쓴다. prompts/, rules.toml, verify.py 등 다른 파일을 쓰거나 지우면 실패 처리된다.
  프롬프트 기록과 검증은 파이프라인이 한다.
- 못, 나사, 접착제 금지. 핀과 끼워 맞춤으로만 결합한다. 장식은 "decor": true, 서포트가 필요하면 "support_note".
- 검증 기준(rules.toml):
{rules}
"""


def ask_claude(prompt):
    cmd = ["claude", "-p", "--model", MODEL, "--system-prompt", SYSTEM.replace("{rules}", (ROOT / "rules.toml").read_text()),
           "--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
           "--disable-slash-commands"]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=1200, cwd=ROOT)
    if p.returncode != 0:
        raise RuntimeError(f"claude 실행 실패: {(p.stderr or p.stdout).strip()[-500:]}")
    return p.stdout


def run(*args):
    p = subprocess.run(["uv", "run", *args], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


PROTECTED = [LOG, ROOT / "rules.toml", ROOT / "verify.py", ROOT / "agent.py", ROOT / "render.py",
             ROOT / "measurements.csv", ROOT / "Makefile"]


def build_and_verify():
    for f in [*PARTS.glob("*.stl"), *PARTS.glob("*.step"), PARTS / "assembly.json"]:
        f.unlink(missing_ok=True)
    before = {f: f.read_bytes() for f in PROTECTED if f.exists()}
    code, out = run("--group", "demo", str(ENGINE))
    touched = [f for f in PROTECTED if before.get(f) != (f.read_bytes() if f.exists() else None)]
    for f in touched:   # 생성 코드가 기록/검증 파일을 건드리면 되돌리고 실패
        f.write_bytes(before[f]) if f in before else f.unlink()
    if touched:
        names = ", ".join(str(f.relative_to(ROOT)) for f in touched)
        return False, f"==== 문제 분석 ====\n[생성 코드 규칙 위반] parts/ 밖 파일을 바꿈: {names}. 되돌렸다. parts/ 에만 쓸 것\n\n"
    if code != 0:
        return False, "생성 실패:\n" + out.strip()[-2000:]
    code, out = run(str(ROOT / "verify.py"))
    print(out.strip())
    return code == 0, out


def analysis(verify_out):
    """verify.py 의 문제 분석 (생성 단계에서 죽었으면 오류 끝부분)."""
    if "==== 문제 분석 ====" in verify_out:
        text = verify_out.split("==== 문제 분석 ====")[1].rsplit("\n\n", 1)[0].strip()
        # 프롬프트 기록은 파이프라인 몫이라 Claude 에게 넘기지 않는다
        return re.sub(r"\[0\. 프롬프트 기록\].*?(?=\n\[|\Z)", "", text, flags=re.S).strip() or "모든 기준 통과"
    return verify_out.strip()[-1500:]


def log(prompt, result, attempt_file):
    """검증 전에 먼저 기록한다 (0단계가 이 줄을 본다). 결과는 set_result 로 채운다."""
    new = not LOG.exists() or not LOG.read_text().strip()
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(["날짜", "부품", "도구", "프롬프트", "첨부", "결과", "메모"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M"), "*", f"Claude Code ({MODEL}) + CadQuery", prompt, "",
                    result, f"코드 {attempt_file.relative_to(ROOT)}"])


def set_result(ok, summary):
    rows = list(csv.reader(LOG.open(encoding="utf-8")))
    rows[-1][5] = ("성공" if ok else "실패") + ": " + summary
    with LOG.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)


def main():
    fresh = not ENGINE.exists()
    print("4기통 엔진 모형. " + ("아직 엔진 코드가 없습니다. 만들 것을 적으세요."
                                 if fresh else "바꾸고 싶은 점을 적으세요 (빈 줄 = 현재 설계로 검증만)."))
    try:
        request = input("요청> ").strip()
    except EOFError:
        request = ""
    if not request:
        if fresh:
            sys.exit("요청이 비어 있습니다.")
        ok, _ = build_and_verify()
        run(str(ROOT / "render.py"))
        sys.exit(0 if ok else 1)

    original = None if fresh else ENGINE.read_text()
    ENGINE.parent.mkdir(exist_ok=True)
    ATTEMPTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    feedback, first, n = "", None, 0
    try:
        while not MAX_TRIES or n < MAX_TRIES:
            n += 1
            ok = attempt(request, n, stamp, feedback)
            feedback, first = ok[1], first or ok[1]
            if ok[0]:
                run(str(ROOT / "render.py"))
                report_first(request, first, f"{n}회차에서 통과. reports/report.md, reports/engine.gif")
                return
        why = f"{MAX_TRIES}회 모두 실패"
    except KeyboardInterrupt:
        if LOG.exists() and LOG.read_text().rstrip().split("\n")[-1].count("검증 중"):
            set_result(False, "Ctrl+C 로 중단")
        why = f"{n}회차에서 Ctrl+C 로 중단"
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(e)
        why = f"{n}회차에서 Claude 호출 실패"
    if original is None:
        ENGINE.unlink(missing_ok=True)
        shutil.rmtree(PARTS, ignore_errors=True)
        back = "엔진 코드 없는 상태로 되돌림"
    else:
        ENGINE.write_text(original)
        build_and_verify()
        back = "원래 설계로 되돌림"
    report_first(request, first, f"{why}, {back}. 시도 코드는 prompts/attempts/")
    sys.exit(1)


def attempt(request, n, stamp, feedback):
    """한 번 시도: Claude 호출 → 저장 → 생성·검증. (통과 여부, 분석) 을 돌려준다."""
    print(f"\n[{n}회차{'' if not MAX_TRIES else f' / 최대 {MAX_TRIES}'}] Claude 가 코드를 쓰는 중... (멈추려면 Ctrl+C)")
    current = ENGINE.read_text() if ENGINE.exists() else None
    prompt = ((f"현재 engine/make_engine.py:\n```python\n{current}\n```\n\n" if current
               else "아직 코드가 없다. 처음부터 작성한다.\n\n") + f"요청: {request}\n"
              + (f"\n직전 시도가 검증에 실패했다. 아래 분석(측정값, 기준, 위치, 고칠 점)대로 고쳐라:\n{feedback}\n"
                 if feedback else ""))
    reply = ask_claude(prompt)
    blocks = re.findall(r"```python\n(.*?)```", reply, re.S)
    attempt_file = ATTEMPTS / f"{stamp}_{n}.py"
    if not blocks:
        attempt_file.write_text(reply)
        log(request if n == 1 else f"{request} (재시도 {n})", "실패: 코드 블록 없음", attempt_file)
        return False, "답에 ```python 코드 블록이 없었다. 파일 전체를 코드 블록 하나로 내라."
    code = max(blocks, key=len)
    attempt_file.write_text(code)
    ENGINE.write_text(code)
    log(request if n == 1 else f"{request} (재시도 {n}, 직전 분석 전달)", "검증 중", attempt_file)
    ok, out = build_and_verify()
    text = analysis(out)
    set_result(ok, "검증 통과" if ok else " / ".join(ln.strip() for ln in text.splitlines() if ln.startswith("["))[:200])
    return ok, text


def report_first(request, first, outcome):
    """첫 프롬프트로 만든 결과의 분석을 마지막에 출력하고 reports/first_attempt.md 에 저장."""
    text = (f"==== 첫 시도 분석 ====\n요청: {request}\n\n{first or '분석 없음 (Claude 호출 실패)'}\n\n"
            f"결과: {outcome}")
    print("\n" + text)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "first_attempt.md").write_text(f"```\n{text}\n```\n")


if __name__ == "__main__":
    if not shutil.which("claude"):
        sys.exit("Claude Code CLI(claude) 가 필요합니다: https://claude.com/claude-code")
    main()
