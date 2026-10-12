"""손으로 돌리는 4기통 엔진 원리 모형 (CadQuery).

부품마다 parts/<이름>.step + .stl, 조립 정보 parts/assembly.json 을 만든다.
좌표 규칙: 크랭크축 = X축(원점 통과), 실린더 = +Z 방향.
모든 부품은 크랭크 각 0도(1번 실린더 상사점)의 조립 위치 그대로 저장한다.
"""
import json
import math
from pathlib import Path

import cadquery as cq

OUT = Path(__file__).resolve().parent.parent / "parts"

# ---- 핵심 치수 (mm) ----
R = 12.0                    # 크랭크 반경, 행정 24
L = 40.0                    # 커넥팅 로드 중심거리
PITCH = 28.0                # 실린더 간격
PHASES = [0, 180, 180, 0]   # 크랭크핀 위상, 점화순서 1-3-4-2
XS = [PITCH * (i - 1.5) for i in range(4)]   # -42 -14 14 42

SHAFT = 6.0                 # 크랭크핀, 저널 지름
PRESS = 5.95                # 압입 구멍 (-0.05)
RUN = 0.4                   # 회전부 지름 여유
PIN = 4.0                   # 피스톤 핀, 고정 핀 지름
PIN_PRESS = 3.95
LOCATE = 4.2                # 위치 맞춤 핀 구멍 (+0.2)
PISTON_D = 20.0
BORE = 20.5                 # 피스톤 미끄럼 여유 0.5

WEB_T = 3.0                 # 크랭크 웹 두께
GAP = 4.6                   # 웹 사이 (로드 4 + 양쪽 0.3)
ROD_T = 4.0
WEB_W = 13.0                # 웹 폭

BLOCK_Z = (27.0, 64.0)      # 실린더 블록 몸통
FLANGE_Z = (24.0, 27.0)     # 블록 아래 플랜지
BASE_Z = (-31.0, -28.0)     # 받침 판
POSTS = [(sx * 52.0, sy * 17.0) for sx in (-1, 1) for sy in (-1, 1)]
BEARING_X = {"front": (-61.0, -55.0), "rear": (55.0, 61.0)}
HANDLE_X = (-68.0, -65.0)
REAR_END = 64.0


# ---- 도형 도우미 ----
def box(x0, x1, y0, y1, z0, z1):
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=False).translate((x0, y0, z0))


def xcyl(x0, x1, y, z, d):
    """X축 방향 원기둥."""
    return cq.Workplane("YZ", origin=(x0, 0, 0)).center(y, z).circle(d / 2).extrude(x1 - x0)


def zcyl(z0, z1, x, y, d):
    """Z축 방향 원기둥."""
    return cq.Workplane("XY", origin=(0, 0, z0)).center(x, y).circle(d / 2).extrude(z1 - z0)


def pin_yz(phase):
    """크랭크 각 0도일 때 크랭크핀 중심 (y, z)."""
    a = math.radians(phase)
    return -R * math.sin(a), R * math.cos(a)


# ---- 부품 ----
def web(x0, phase):
    y, z = pin_yz(phase)
    body = (cq.Workplane("YZ", origin=(x0, 0, 0)).center(y / 2, z / 2)
            .slot2D(R + WEB_W, WEB_W, math.degrees(math.atan2(z, y))).extrude(WEB_T))
    return body.cut(xcyl(x0, x0 + WEB_T, 0, 0, PRESS)).cut(xcyl(x0, x0 + WEB_T, y, z, PRESS))


def rod(x, phase):
    y, zb = pin_yz(phase)
    assert abs(y) < 1e-9, "0도에서 로드가 수직인 위상만 지원"
    zs = zb + L
    x0, x1 = x - ROD_T / 2, x + ROD_T / 2
    body = (xcyl(x0, x1, 0, zb, 11.0).union(xcyl(x0, x1, 0, zs, 8.5))
            .union(box(x0, x1, -2.25, 2.25, zb, zs)))
    return body.cut(xcyl(x0, x1, 0, zb, SHAFT + RUN)).cut(xcyl(x0, x1, 0, zs, PIN + RUN))


def piston(x, zs):
    body = zcyl(zs - 9, zs + 7, x, 0, PISTON_D).cut(zcyl(zs - 9, zs + 4.5, x, 0, 16.0))
    return body.cut(xcyl(x - 11, x + 11, 0, zs, PIN_PRESS))


def block():
    b = box(-55, 55, -13, 13, *BLOCK_Z).union(box(-58, 58, -22, 22, *FLANGE_Z))
    for x in XS:
        b = b.cut(zcyl(FLANGE_Z[0], BLOCK_Z[1], x, 0, BORE))
    for x, y in POSTS:
        b = b.cut(zcyl(*FLANGE_Z, x, y, LOCATE))
    return b


def cover():
    """투명 블록 커버: 플랜지 위에 얹는 상자."""
    return box(-57.3, 57.3, -15.3, 15.3, FLANGE_Z[1], 70).cut(box(-55.3, 55.3, -13.3, 13.3, FLANGE_Z[1], 67))


def bearing(x0, x1):
    xm = (x0 + x1) / 2
    b = box(x0, x1, -10, 10, BASE_Z[1], 8).cut(xcyl(x0, x1, 0, 0, SHAFT + RUN))
    for y in (-6, 6):
        b = b.union(zcyl(*BASE_Z, xm, y, PIN))
    return b


def base():
    b = box(-64, 64, -25, 25, *BASE_Z)
    for x, y in POSTS:
        b = b.union(box(x - 2.5, x + 2.5, y - 2.5, y + 2.5, BASE_Z[1], FLANGE_Z[0]))
        b = b.union(zcyl(*FLANGE_Z, x, y, PIN))
    for x0, x1 in BEARING_X.values():
        for y in (-6, 6):
            b = b.cut(zcyl(*BASE_Z, (x0 + x1) / 2, y, LOCATE))
    return b


def handle():
    x0, x1 = HANDLE_X
    arm = (cq.Workplane("YZ", origin=(x0, 0, 0)).center(0, 11)
           .slot2D(22 + 12, 12, 90).extrude(x1 - x0))
    return arm.cut(xcyl(x0, x1, 0, 0, PRESS)).union(xcyl(x0 - 12, x0, 0, 22, 6.0))


def build():
    parts, fits = {}, []

    def add(name, shape, kind="functional", motion="fixed", **extra):
        parts[name] = (shape, {"name": name, "type": kind, "motion": motion, **extra})

    def fit(pin, hole, at, axis, kind):
        fits.append({"name": f"{pin} / {hole}", "pin": pin, "hole": hole, "at": list(at), "axis": axis, "kind": kind})

    add("block", block())
    add("cover", cover(), kind="exterior")
    add("base", base(), kind="exterior")
    for side, (x0, x1) in BEARING_X.items():
        add(f"bearing_{side}", bearing(x0, x1))
        for y in (-6, 6):
            fit(f"bearing_{side}", "base", ((x0 + x1) / 2, y, -29.5), "z", "locating")
    for x, y in POSTS:
        fit("base", "block", (x, y, 25.5), "z", "locating")

    # 조립식 크랭크축: 웹 8, 크랭크핀 4, 저널 5, 손잡이 1
    add("handle", handle(), motion="crank")
    journal_x = [(HANDLE_X[0], XS[0] - GAP / 2)]
    journal_x += [(XS[i] + GAP / 2, XS[i + 1] - GAP / 2) for i in range(3)]
    journal_x += [(XS[3] + GAP / 2, REAR_END)]
    for j, (x0, x1) in enumerate(journal_x):
        add(f"journal_{j}", xcyl(x0, x1, 0, 0, SHAFT), motion="crank")
    fit("journal_0", "handle", (sum(HANDLE_X) / 2, 0, 0), "x", "press")
    fit("journal_0", "bearing_front", (sum(BEARING_X["front"]) / 2, 0, 0), "x", "running")
    fit("journal_4", "bearing_rear", (sum(BEARING_X["rear"]) / 2, 0, 0), "x", "running")

    cylinders = []
    for i, (x, ph) in enumerate(zip(XS, PHASES), start=1):
        y, z = pin_yz(ph)
        zs = z + L
        cylinders.append({"id": i, "x": x, "phase": ph})
        xa, xb = x - GAP / 2 - WEB_T, x + GAP / 2      # 웹 A, B 시작 x
        add(f"web_{i}A", web(xa, ph), motion="crank")
        add(f"web_{i}B", web(xb, ph), motion="crank")
        add(f"crankpin_{i}", xcyl(xa, xb + WEB_T, y, z, SHAFT), motion="crank")
        add(f"rod_{i}", rod(x, ph), motion=f"rod:{i}")
        add(f"piston_{i}", piston(x, zs), motion=f"piston:{i}")
        add(f"pistonpin_{i}", xcyl(x - 9.5, x + 9.5, 0, zs, PIN), motion=f"piston:{i}")

        fit(f"crankpin_{i}", f"rod_{i}", (x, y, z), "x", "running")
        for w, xw in (("A", xa), ("B", xb)):
            fit(f"crankpin_{i}", f"web_{i}{w}", (xw + WEB_T / 2, y, z), "x", "press")
            j = i - 1 if w == "A" else i
            fit(f"journal_{j}", f"web_{i}{w}", (xw + WEB_T / 2, 0, 0), "x", "press")
        fit(f"pistonpin_{i}", f"rod_{i}", (x, 0, zs), "x", "running")
        fit(f"pistonpin_{i}", f"piston_{i}", (x + 9, 0, zs), "x", "press")
        fit(f"piston_{i}", "block", (x, 0, zs + 5), "z", "sliding")

    return parts, fits, {"r": R, "L": L, "cylinders": cylinders}


def main(out=OUT):
    out = Path(out)
    out.mkdir(exist_ok=True)
    parts, fits, kin = build()
    for name, (shape, _) in parts.items():
        cq.exporters.export(shape, str(out / f"{name}.step"))
        cq.exporters.export(shape, str(out / f"{name}.stl"), tolerance=0.01, angularTolerance=0.1)
    meta = {"kinematics": kin, "parts": [m for _, m in parts.values()], "fits": fits}
    (out / "assembly.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"{len(parts)}개 부품, {len(fits)}개 결합 -> {out}")


if __name__ == "__main__":
    main()
