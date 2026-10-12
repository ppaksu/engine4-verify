"""명령 하나로 모든 단계 검증: uv run verify.py

입력: parts/<부품>.stl(.step), parts/assembly.json, rules.toml,
      prompts/log.csv, measurements.csv
출력: 화면 요약 + reports/report.md. 실패가 하나라도 있으면 종료 코드 1.
"""
import argparse
import csv
import fnmatch
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

import manifold3d
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent
AXES = {"x": np.array([1.0, 0, 0]), "y": np.array([0, 1.0, 0]), "z": np.array([0, 0, 1.0])}
RANK = {"FAIL": 3, "WARN": 2, "PASS": 1, "SKIP": 0}


class Ctx:
    def __init__(self, parts_dir, rules_path, log_path):
        self.dir, self.log = parts_dir, log_path
        self.rules = tomllib.loads(rules_path.read_text())
        self.asm = json.loads((parts_dir / "assembly.json").read_text())
        self.parts = {p["name"]: p for p in self.asm["parts"]}
        self.meshes = {n: trimesh.load_mesh(parts_dir / f"{n}.stl")
                       for n in self.parts if (parts_dir / f"{n}.stl").exists()}
        self.orient = {}   # 2단계에서 고른 출력 방향 (6단계 슬라이서가 사용)


# ---------- 0. 파일과 프롬프트 기록 ----------
def stage_files(ctx):
    out = []
    for name, p in ctx.parts.items():
        need = ["stl"] + (["step"] if p["type"] == "functional" else [])
        missing = [e for e in need if not (ctx.dir / f"{name}.{e}").exists()]
        step = ctx.dir / f"{name}.step"
        if missing:
            out.append(("FAIL", name, f"파일 없음: {', '.join(missing)}"))
        elif step.exists() and not step.read_text(errors="ignore")[:20].startswith("ISO-10303-21"):
            out.append(("FAIL", name, "STEP 형식 아님"))
        else:
            out.append(("PASS", name, " + ".join(need)))
    for f in sorted(ctx.dir.glob("*.stl")):
        if f.stem not in ctx.parts:
            out.append(("WARN", f.name, "assembly.json 에 없는 파일"))
    return out


def stage_prompts(ctx):
    rows = read_csv(ctx.log)
    if not rows:
        return [("FAIL", "prompts/log.csv", "프롬프트 기록 없음")]
    pats = [p.strip() for r in rows for p in r.get("부품", "").split(";") if p.strip()]
    fails = sum(1 for r in rows if r.get("결과", "").startswith("실패"))
    out = [("PASS", "prompts/log.csv", f"시도 {len(rows)}회 (실패 {fails}회)")]
    for name in ctx.parts:
        if not any(fnmatch.fnmatch(name, p) for p in pats):
            out.append(("WARN", name, "프롬프트 기록에 없음"))
    return out


# ---------- 1. 형상 규칙 ----------
def pts_below(mesh, t, limit):
    """얇은 점들의 중심 좌표 (같은 seed 라 thickness() 와 같은 점)."""
    pts, _ = trimesh.sample.sample_surface(mesh, len(t), seed=0)
    return pts[t < limit].mean(axis=0).round(1).tolist()


def thickness(mesh, n):
    pts, fi = trimesh.sample.sample_surface(mesh, n, seed=0)
    nrm = mesh.face_normals[fi]
    eps = 1e-3
    loc, ri, _ = mesh.ray.intersects_location(pts - nrm * eps, -nrm, multiple_hits=False)
    t = np.full(n, np.inf)
    t[ri] = np.linalg.norm(loc - pts[ri], axis=1) + eps
    return t


def stage_shape(ctx):
    r, out = ctx.rules, []
    for name, m in ctx.meshes.items():
        ext = m.extents
        size = "x".join(f"{e:.1f}" for e in ext)
        if max(ext) > r["size"]["max_part"]:
            out.append(("FAIL", name, f"크기 {size} > {r['size']['max_part']}, 나누고 핀으로 맞출 것"))
            continue
        if not (m.is_watertight and m.is_winding_consistent and m.volume > 0):
            out.append(("FAIL", name, "닫힌 메쉬 아님 (구멍/뒤집힌 면)"))
            continue
        min_wall = r["wall"]["min_decor"] if ctx.parts[name].get("decor") else r["wall"]["min"]
        t = thickness(m, r["wall"]["samples"])
        thin = float(np.mean(t < min_wall))
        note = f"크기 {size}, 부피 {m.volume:.0f}mm3, 두께 1%값 {np.percentile(t, 1):.2f}"
        if thin > r["wall"]["thin_fraction_max"]:
            thin_pts = pts_below(m, t, min_wall)
            out.append(("FAIL", name, f"{min_wall}mm 미만 표면 {thin:.1%} ({note})",
                        {"min_wall": min_wall, "thin": thin, "p1": float(np.percentile(t, 1)), "at": thin_pts}))
        elif m.body_count > 1:
            out.append(("WARN", name, f"덩어리 {m.body_count}개 ({note})"))
        else:
            out.append(("PASS", name, note))
    return out


# ---------- 2. 출력성 (오버행) ----------
DOWNS = {"-Z": (0, 0, -1), "+Z": (0, 0, 1), "-Y": (0, -1, 0), "+Y": (0, 1, 0), "-X": (-1, 0, 0), "+X": (1, 0, 0)}


def overhang(mesh, max_angle):
    """6방향 중 오버행 면적이 가장 작은 출력 방향. 같으면 바닥 접촉이 넓은 쪽."""
    best = None
    for label, d in DOWNS.items():
        down = np.array(d, float)
        h = mesh.triangles_center @ -down
        bed = h < h.min() + 0.2
        over = (mesh.face_normals @ down > math.cos(math.radians(max_angle))) & ~bed
        area, bed_area = mesh.area_faces[over].sum(), mesh.area_faces[bed].sum()
        key = (round(area, 1), -bed_area)
        if best is None or key < best[0]:
            best = (key, label, area)
    return best[1], best[2]


def stage_overhang(ctx):
    r, out = ctx.rules["overhang"], []
    for name, m in ctx.meshes.items():
        label, area = overhang(m, r["max_angle"])
        ctx.orient[name] = label
        note = ctx.parts[name].get("support_note")
        msg = f"{label} 면을 바닥에, 오버행 {area:.1f}mm2"
        if area <= r["ignore_area"]:
            out.append(("PASS", name, msg))
        elif note:
            out.append(("PASS", name, f"{msg}, 서포트: {note}"))
        else:
            out.append(("WARN", name, f"{msg}, 서포트 위치를 support_note 로 적을 것"))
    return out


# ---------- 3. 결합 공차 ----------
def measure_d(mesh, at, axis, role):
    """at 을 지나는 축에 수직인 단면에서, 축을 중심으로 한 원의 지름."""
    at, ax = np.asarray(at, float), AXES[axis]
    sec = mesh.section(plane_origin=at, plane_normal=ax)
    if sec is None:
        return None
    found = []
    for loop in sec.discrete:
        d = np.linalg.norm(np.cross(loop - at, ax), axis=1)
        if d.max() > 0 and (d.max() - d.min()) / d.max() < 0.03:
            found.append(2 * d.max())
    if not found:
        return None
    return max(found) if role == "pin" else min(found)


def stage_fits(ctx):
    r, out = ctx.rules["fit"], []
    for f in ctx.asm.get("fits", []):
        kind = f["kind"]
        lo, hi = r["kinds"][kind]
        pd = measure_d(ctx.meshes[f["pin"]], f["at"], f["axis"], "pin")
        hd = measure_d(ctx.meshes[f["hole"]], f["at"], f["axis"], "hole")
        if pd is None or hd is None:
            out.append(("FAIL", f["name"], f"원 단면을 못 찾음 (at={f['at']}, axis={f['axis']})"))
            continue
        c = hd - pd
        msg = f"{kind}: 핀 {pd:.2f} / 구멍 {hd:.2f} / 차이 {c:+.2f} (허용 {lo:+.2f}~{hi:+.2f})"
        data = {"pin": pd, "hole": hd, "c": c, "lo": lo, "hi": hi, "kind": kind, "min_pin": r["min_pin_d"]}
        if pd < r["min_pin_d"]:
            out.append(("FAIL", f["name"], f"핀 지름 {r['min_pin_d']}mm 미만. {msg}", data))
        elif lo - 0.005 <= c <= hi + 0.005:
            out.append(("PASS", f["name"], msg))
        else:
            out.append(("FAIL", f["name"], msg, data))
    return out


# ---------- 4, 5. 조립 간섭 + 크랭크 회전 ----------
def rx(a):
    return trimesh.transformations.rotation_matrix(a, [1, 0, 0])


def tr(v):
    return trimesh.transformations.translation_matrix(v)


def poses(kin, theta):
    """크랭크 각 theta(rad)에서 움직임 그룹별 변환. 0도 조립 위치 기준."""
    r, L = kin["r"], kin["L"]
    T = {"fixed": np.eye(4), "crank": rx(theta)}
    for c in kin["cylinders"]:
        def crankpin(t):
            a = t + math.radians(c["phase"])
            return np.array([c["x"], -r * math.sin(a), r * math.cos(a)])

        def small_end(p):
            return np.array([c["x"], 0, p[2] + math.sqrt(L * L - p[1] ** 2)])

        p0, p = crankpin(0), crankpin(theta)
        s0, s = small_end(p0), small_end(p)
        b0 = math.atan2(-(s0 - p0)[1], (s0 - p0)[2])
        b = math.atan2(-(s - p)[1], (s - p)[2])
        T[f"rod:{c['id']}"] = tr(p) @ rx(b - b0) @ tr(-p0)
        T[f"piston:{c['id']}"] = tr([0, 0, s[2] - s0[2]])
    return T


def to_manifold(m):
    return manifold3d.Manifold(manifold3d.Mesh(
        vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)))


def overlap(a, b):
    return all(a[i] < b[i + 3] and b[i] < a[i + 3] for i in range(3))


def collisions(ctx, solids, groups, theta, pairs):
    T = poses(ctx.asm["kinematics"], theta)
    placed = {n: solids[n].transform(T[groups[n]][:3, :]) for n in solids}
    boxes = {n: placed[n].bounding_box() for n in placed}
    hits = []
    for a, b in pairs:
        if overlap(boxes[a], boxes[b]):
            inter = placed[a] ^ placed[b]
            v = inter.volume()
            if v > ctx.rules["motion"]["interference_tol"]:
                bb = np.array(inter.bounding_box())
                hits.append((a, b, v, ((bb[:3] + bb[3:]) / 2).round(1).tolist()))
    return hits


def stage_assembly(ctx):
    solids = {n: to_manifold(m) for n, m in ctx.meshes.items()}
    groups = {n: ctx.parts[n]["motion"] for n in solids}
    allowed = {frozenset((f["pin"], f["hole"])) for f in ctx.asm.get("fits", []) if f["kind"] == "press"}
    pairs = [p for p in combinations(sorted(solids), 2) if frozenset(p) not in allowed]
    ctx.solids, ctx.groups = solids, groups
    hits = collisions(ctx, solids, groups, 0.0, pairs)
    if not hits:
        return [("PASS", "0도 조립", f"{len(pairs)}쌍 겹침 없음 (압입 {len(allowed)}쌍 제외)")]
    return [("FAIL", f"{a} x {b}", f"0도에서 {v:.1f}mm3 겹침", {"vol": v, "at": at}) for a, b, v, at in hits]


def stage_motion(ctx):
    kin, r = ctx.asm["kinematics"], ctx.rules["motion"]
    out = []
    ratio = kin["L"] / kin["r"]
    status = "PASS" if ratio >= r["min_rod_ratio"] else "WARN"
    out.append((status, "기구 치수", f"행정 {2 * kin['r']:.0f}, 로드/반경 {ratio:.2f} (기준 {r['min_rod_ratio']} 이상)"))
    pairs = [p for p in combinations(sorted(ctx.solids), 2) if ctx.groups[p[0]] != ctx.groups[p[1]]]
    worst = {}
    for deg in range(r["step_deg"], 360, r["step_deg"]):
        for a, b, v, at in collisions(ctx, ctx.solids, ctx.groups, math.radians(deg), pairs):
            first, vmax, at0 = worst.get((a, b), (deg, 0, at))
            worst[(a, b)] = (first, max(vmax, v), at0)
    if not worst:
        out.append(("PASS", "크랭크 1회전", f"{r['step_deg']}도 간격 {360 // r['step_deg']}자세, {len(pairs)}쌍 겹침 없음"))
    for (a, b), (deg, v, at) in sorted(worst.items(), key=lambda kv: kv[1][0]):
        out.append(("FAIL", f"{a} x {b}", f"{deg}도부터 충돌, 최대 {v:.1f}mm3", {"deg": deg, "vol": v, "at": at}))
    return out


# ---------- 6. 슬라이서 ----------
def find_slicer(path):
    for c in [path, shutil.which("prusa-slicer"), shutil.which("PrusaSlicer"),
              "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"]:
        if c and Path(c).exists():
            return c
    return None


def slice_one(slicer, name, mesh, label, support, density, tmp):
    m = mesh.copy()
    down = np.array(DOWNS[label], float)
    m.apply_transform(trimesh.geometry.align_vectors(down, [0, 0, -1]))
    stl, gcode = Path(tmp) / f"{name}.stl", Path(tmp) / f"{name}.gcode"
    m.export(stl)
    cmd = [slicer, "--export-gcode", "--ensure-on-bed", "--center", "100,100", "-o", str(gcode), str(stl)]
    if support:
        cmd.insert(1, "--support-material")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if p.returncode != 0 or not gcode.exists():
        return ("FAIL", name, (p.stderr or p.stdout).strip().splitlines()[-1:] or ["슬라이스 실패"])
    g = gcode.read_text(errors="ignore")
    tm = re.search(r"estimated printing time \(normal mode\) = (.+)", g)
    cm = re.search(r"filament used \[cm3\] = ([\d.]+)", g)
    grams = float(cm.group(1)) * density if cm else 0.0
    return ("PASS", name, f"{tm.group(1).strip() if tm else '?'}, {grams:.1f}g")


def stage_slicer(ctx):
    r = ctx.rules["slicer"]
    slicer = find_slicer(r["path"])
    if not slicer:
        return [("SKIP", "슬라이서", "PrusaSlicer 없음. 설치: brew install --cask prusaslicer")]
    with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(4) as ex:
        jobs = [ex.submit(slice_one, slicer, n, m, ctx.orient.get(n, "-Z"),
                          bool(ctx.parts[n].get("support_note")), r["density"], tmp) for n, m in ctx.meshes.items()]
        out = [j.result() for j in jobs]
    out = [(s, n, d if isinstance(d, str) else d[0]) for s, n, d in out]
    secs = sum(duration(d) for s, _, d in out if s == "PASS")
    grams = sum(float(m.group(1)) for _, _, d in out if (m := re.search(r"([\d.]+)g$", d)))
    out.append(("PASS", "합계", f"{secs // 3600}h {secs % 3600 // 60}m, {grams:.0f}g (프린터 1대 순차 출력 기준)"))
    return out


def duration(text):
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    return sum(int(n) * units[u] for n, u in re.findall(r"(\d+)([dhms])", text.split(",")[0]))


# ---------- 7. 실물 측정 ----------
def stage_measure(ctx):
    rows = read_csv(ROOT / "measurements.csv")
    if not rows:
        return [("SKIP", "measurements.csv", "측정값 없음. 출력 후 캘리퍼 값을 적으면 검사")]
    out = []
    for r in rows:
        nom, got, tol = float(r["설계값"]), float(r["측정값"]), float(r["허용오차"])
        s = "PASS" if abs(got - nom) <= tol else "FAIL"
        out.append((s, f"{r['부품']} {r['항목']}", f"설계 {nom} / 측정 {got} / 차이 {got - nom:+.2f} (±{tol})"))
    return out


def read_csv(path):
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines() if ln.strip() and not ln.startswith("#")]
    return list(csv.DictReader(lines))


STAGES = [
    ("0", "파일", stage_files),
    ("0", "프롬프트 기록", stage_prompts),
    ("1", "형상 규칙", stage_shape),
    ("2", "출력성", stage_overhang),
    ("3", "결합 공차", stage_fits),
    ("4", "조립 간섭", stage_assembly),
    ("5", "동작 검사", stage_motion),
    ("6", "슬라이서", stage_slicer),
    ("7", "실물 측정", stage_measure),
]


# ---------- 문제 분석 ----------
def xyz(at):
    return "(" + ", ".join(str(int(round(v))) for v in at) + ")"


def explain(num, msg, d):
    """실패/경고 한 줄을 '무엇이 기준을 어겼고, 어디를, 어떻게 고칠지'로."""
    if num == "1" and "thin" in d:
        return (f"벽이 {d['min_wall']}mm 보다 얇은 곳이 표면의 {d['thin']:.1%}, 가장 얇은 1% 지점 {d['p1']:.2f}mm, "
                f"위치 약 {xyz(d['at'])}. 그 부근 벽이나 구멍 둘레를 {d['min_wall']}mm 이상으로 키울 것")
    if num == "3" and "c" in d:
        if d["pin"] < d["min_pin"]:
            return f"핀 ⌀{d['pin']:.2f} 가 최소 ⌀{d['min_pin']} 보다 가늘다. 핀과 구멍을 함께 키울 것"
        target = (d["lo"] + d["hi"]) / 2
        word = "빡빡함" if d["c"] < d["lo"] else "헐거움"
        return (f"{d['kind']} 차이 {d['c']:+.2f} 가 기준 {d['lo']:+.2f}~{d['hi']:+.2f} 밖({word}). "
                f"핀 ⌀{d['pin']:.2f} 그대로면 구멍을 ⌀{d['hole']:.2f} → ⌀{d['pin'] + target:.2f} 로 바꿀 것")
    if num == "3":
        return msg + ". fits 의 at/axis 가 실제 핀과 구멍 위치와 다르거나, 구멍이 없음"
    if num == "4" and "at" in d:
        return (f"조립 상태(0°)에서 {d['vol']:.1f}mm3 겹침, 위치 약 {xyz(d['at'])}. "
                "두 부품 사이를 0.3mm 이상 띄우거나, 끼워 맞춤이면 fits 에 press 로 등록")
    if num == "5" and "deg" in d:
        return (f"크랭크 {d['deg']}°에서 처음 부딪힘, 최대 {d['vol']:.1f}mm3, 위치 약 {xyz(d['at'])}. "
                "회전하는 부품이 지나가는 반경 안에 있는 형상을 줄이거나 옮길 것")
    hints = {"0": "내보내기 코드와 prompts/log.csv 확인", "1": "부품을 나누거나 형상을 정리할 것",
             "2": "출력 방향을 바꾸거나 45° 챔퍼를 넣는 방법도 있음",
             "5": "로드를 길게 하거나 크랭크 반경을 줄일 것", "6": "슬라이서 오류 메시지 확인",
             "7": "프린터 보정(수축)을 하거나 설계값을 조정할 것"}
    return f"{msg}. {hints.get(num, '')}"


def analyze(results, limit=6):
    lines = []
    for num, title, _, rows in results:
        bad = [r for r in rows if r[0] in ("FAIL", "WARN")]
        if not bad:
            continue
        lines.append(f"[{num}. {title}] {len(bad)}건")
        groups = {}   # 위치만 다른 같은 문제는 한 줄로
        for s, item, msg, d in bad:
            text = explain(num, msg, d)
            groups.setdefault((s, re.sub(r"위치 약 \([^)]*\)", "", text)), [text, []])[1].append(item)
        for (s, _), (text, items) in list(groups.items())[:limit]:
            names = ", ".join(items) if len(items) <= 4 else f"{', '.join(items[:3])} 외 {len(items) - 3}개"
            lines.append(f"  {s} {names}: {text}" + (" (위치는 첫 부품 기준)" if len(items) > 1 and "위치 약" in text else ""))
        if len(groups) > limit:
            lines.append(f"  ... 외 {len(groups) - limit}종 (reports/report.md)")
    return "\n".join(lines) or "모든 기준 통과"


def main():
    ap = argparse.ArgumentParser(description="4기통 엔진 모형 검증")
    ap.add_argument("--parts", default=ROOT / "parts", type=Path)
    ap.add_argument("--rules", default=ROOT / "rules.toml", type=Path)
    ap.add_argument("--reports", default=ROOT / "reports", type=Path)
    ap.add_argument("--log", default=ROOT / "prompts" / "log.csv", type=Path)
    ap.add_argument("--skip", default="", help="건너뛸 단계 번호, 예: 6,7")
    args = ap.parse_args()
    skip = set(args.skip.split(",")) - {""}

    if not (args.parts / "assembly.json").exists():
        sys.exit(f"{args.parts}/assembly.json 없음. 먼저 make 로 엔진을 만드세요.")
    ctx = Ctx(args.parts, args.rules, args.log)
    results, t0 = [], time.time()
    for num, title, fn in STAGES:
        t = time.time()
        rows = [("SKIP", "-", "--skip")] if num in skip else fn(ctx)
        rows = [(*r, {}) if len(r) == 3 else r for r in rows]
        status = max((r[0] for r in rows), key=RANK.get)
        results.append((num, title, status, rows))
        print(f"[{status:4}] {num}. {title} ({time.time() - t:.1f}s)")
        for s, item, msg, _ in rows:
            if s in ("FAIL", "WARN"):
                print(f"         {s}  {item}: {msg}")

    analysis = analyze(results)
    write_report(results, time.time() - t0, analysis, args.reports)
    failed = any(s == "FAIL" for _, _, s, _ in results)
    print(f"\n==== 문제 분석 ====\n{analysis}")
    print(f"\n{'실패' if failed else '통과'} - reports/report.md")
    sys.exit(1 if failed else 0)


def write_report(results, secs, analysis, out):
    lines = [f"# 검증 결과 ({time.strftime('%Y-%m-%d %H:%M')}, {secs:.0f}초)", "",
             "| 단계 | 이름 | 결과 |", "|---|---|---|"]
    lines += [f"| {n} | {t} | {s} |" for n, t, s, _ in results]
    lines += ["", "## 문제 분석", "", "```", analysis, "```"]
    for n, t, s, rows in results:
        lines += ["", f"## {n}. {t}: {s}", "", "| 결과 | 대상 | 내용 |", "|---|---|---|"]
        lines += [f"| {st} | {item} | {msg} |" for st, item, msg, _ in rows]
    out.mkdir(exist_ok=True)
    (out / "report.md").write_text("\n".join(lines) + "\n")
    (out / "analysis.txt").write_text(analysis + "\n")


if __name__ == "__main__":
    main()
