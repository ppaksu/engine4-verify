"""요청 입력 → Claude가 엔진 코드 수정 → 생성 → 검증 → 실패하면 실패 내용을 붙여 재요청.

make  (또는 uv run agent.py)
빈 줄 입력 = AI 없이 현재 코드로 생성, 검증, 그림만.
매 시도는 prompts/log.csv 에 한 줄씩, 코드는 prompts/attempts/ 에 남는다.
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
MAX_TRIES = int(os.environ.get("MAX_TRIES", 3))
MODEL = os.environ.get("CLAUDE_MODEL", "opus")

SYSTEM = """너는 학교 3D 프린팅 과제를 돕는 CAD 엔지니어다.
주제: 손으로 돌리는 4기통 엔진 원리 모형. 파이썬 CadQuery 코드 파일 하나(engine/make_engine.py)를 고친다.

반드시 지킬 것:
- 답은 수정된 make_engine.py 전체를 ```python 코드 블록 하나로만 낸다. 블록 밖 설명은 3줄 이내.
- main(out=OUT) 과 build() 구조, assembly.json 형식(kinematics / parts / fits)을 유지한다.
- 좌표: 크랭크축 = 원점을 지나는 X축, 실린더 = +Z. 모든 부품은 크랭크 0도(1번 상사점) 조립 위치로 저장.
- motion: fixed / crank / rod:<번호> / piston:<번호>. type: functional(기능, STEP+STL) / exterior(겉면, STL).
- fits: 원형 핀과 구멍마다 at(조립 좌표의 단면 위치), axis(x/y/z), kind(running/sliding/press/locating).
  press 쌍은 겹쳐도 되고, 나머지 부품 쌍은 크랭크가 한 바퀴 도는 동안 겹치면 안 된다.
- 못, 나사, 접착제 금지. 핀과 끼워 맞춤으로만 결합한다. 장식은 "decor": true, 서포트가 필요하면 "support_note".
- 검증 기준(rules.toml):
{rules}
"""


def ask_claude(prompt):
    cmd = ["claude", "-p", "--model", MODEL, "--system-prompt", SYSTEM.format(rules=(ROOT / "rules.toml").read_text()),
           "--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
           "--disable-slash-commands"]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=1200, cwd=ROOT)
    if p.returncode != 0:
        raise RuntimeError(f"claude 실행 실패: {(p.stderr or p.stdout).strip()[-500:]}")
    return p.stdout


def run(*args):
    p = subprocess.run(["uv", "run", *args], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def build_and_verify():
    for f in [*PARTS.glob("*.stl"), *PARTS.glob("*.step"), PARTS / "assembly.json"]:
        f.unlink(missing_ok=True)
    code, out = run("--group", "demo", str(ENGINE))
    if code != 0:
        return False, "생성 실패:\n" + out.strip()[-2000:]
    code, out = run(str(ROOT / "verify.py"))
    print(out.strip())
    return code == 0, out


def failures(verify_out):
    """재요청에 붙일 실패/경고 줄만."""
    lines = [ln.strip() for ln in verify_out.splitlines() if re.match(r"\s+(FAIL|WARN)\s", ln) or "생성 실패" in ln]
    return "\n".join(lines) or verify_out.strip()[-2000:]


def log(prompt, ok, summary, attempt_file):
    new = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(["날짜", "부품", "도구", "프롬프트", "첨부", "결과", "메모"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M"), "*", f"Claude Code ({MODEL}) + CadQuery", prompt, "",
                    ("성공" if ok else "실패") + ": " + summary, f"코드 {attempt_file.relative_to(ROOT)}"])


def main():
    print("4기통 엔진 모형. 바꾸고 싶은 점을 적으세요 (빈 줄 = 현재 설계로 검증만).")
    try:
        request = input("요청> ").strip()
    except EOFError:
        request = ""
    if not request:
        ok, _ = build_and_verify()
        run(str(ROOT / "render.py"))
        sys.exit(0 if ok else 1)

    original = ENGINE.read_text()
    ATTEMPTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    feedback = ""
    for n in range(1, MAX_TRIES + 1):
        print(f"\n[{n}/{MAX_TRIES}] Claude 가 코드를 고치는 중...")
        prompt = (f"현재 engine/make_engine.py:\n```python\n{ENGINE.read_text()}\n```\n\n요청: {request}\n"
                  + (f"\n직전 시도가 검증에 실패했다. 아래 문제를 고쳐라:\n{feedback}\n" if feedback else ""))
        try:
            reply = ask_claude(prompt)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            print(e)
            break
        blocks = re.findall(r"```python\n(.*?)```", reply, re.S)
        attempt_file = ATTEMPTS / f"{stamp}_{n}.py"
        if not blocks:
            attempt_file.write_text(reply)
            feedback = "답에 ```python 코드 블록이 없었다. 파일 전체를 코드 블록 하나로 내라."
            log(request if n == 1 else f"{request} (재시도 {n})", False, "코드 블록 없음", attempt_file)
            continue
        code = max(blocks, key=len)
        attempt_file.write_text(code)
        ENGINE.write_text(code)
        ok, out = build_and_verify()
        feedback = failures(out)
        summary = "검증 통과" if ok else feedback.splitlines()[0][:150]
        log(request if n == 1 else f"{request} (재시도 {n}, 직전 실패 내용 전달)", ok, summary, attempt_file)
        if ok:
            run(str(ROOT / "render.py"))
            print(f"\n완료 ({n}회 시도). reports/report.md, reports/engine.gif")
            return
    print(f"\n{MAX_TRIES}회 모두 실패. 원래 설계로 되돌림. 시도 코드는 prompts/attempts/ 에 있음.")
    ENGINE.write_text(original)
    build_and_verify()
    sys.exit(1)


if __name__ == "__main__":
    if not shutil.which("claude"):
        sys.exit("Claude Code CLI(claude) 가 필요합니다: https://claude.com/claude-code")
    main()
