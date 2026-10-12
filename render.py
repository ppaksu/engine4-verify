"""발표용 그림: reports/engine.png (조립도), reports/engine.gif (크랭크 2회전 = 4행정 한 사이클).
폭발 행정 중인 피스톤을 빨갛게 칠해 점화 순서를 보인다.
uv run render.py [--parts parts] [--out reports]
"""
import argparse
import json
import math
from pathlib import Path

import matplotlib
import numpy as np
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import trimesh  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402
from matplotlib.colors import to_rgb  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

from verify import poses  # noqa: E402

ROOT = Path(__file__).resolve().parent
PARTS = ROOT / "parts"
COLORS = {"block": "#9aa5b1", "cover": "#cfe8ff", "base": "#6b5b4b", "bearing": "#c08040",
          "rod": "#2f6fd6", "piston": "#e0e0e0", "pistonpin": "#444444", "handle": "#d64545",
          "crankpin": "#f2b134"}   # 크랭크핀만 다른 색: 둥근 웹이라도 회전이 보이게
FIRE = "#e4572e"           # 폭발 행정 중인 피스톤
LIGHT = np.array([-0.4, -0.6, 0.7]) / np.linalg.norm([-0.4, -0.6, 0.7])


def color(name):
    return COLORS.get(name.split("_")[0], "#3a8f5c")   # 크랭크 부품 = 초록


def load(parts, skip=("cover",)):
    asm = json.loads((parts / "assembly.json").read_text())
    meshes = {}
    for p in asm["parts"]:
        if p["name"] in skip:
            continue
        m = trimesh.load_mesh(parts / f"{p['name']}.stl")
        meshes[p["name"]] = (m.simplify_quadric_decimation(face_count=1500) if len(m.faces) > 1500 else m, p["motion"])
    return asm, meshes


def limits(meshes):
    """전체 부품 범위 + 움직임 여유 10mm."""
    lo = np.min([m.bounds[0] for m, _ in meshes.values()], axis=0) - 10
    hi = np.max([m.bounds[1] for m, _ in meshes.values()], axis=0) + 10
    return lo, hi


def fire_angles(kin):
    """폭발 순서대로 720/n 도마다 점화. 각 실린더의 상사점과 맞는지 확인."""
    order = kin.get("firing_order", [1, 3, 4, 2])
    phase = {c["id"]: c["phase"] for c in kin["cylinders"]}
    angles = {cyl: 720 * k / len(order) for k, cyl in enumerate(order)}
    for cyl, a in angles.items():   # 상사점: theta + phase = 0 (mod 360)
        if (a + phase[cyl]) % 360:
            print(f"경고: {cyl}번 점화 {a:.0f}도가 상사점과 안 맞음 (위상 {phase[cyl]})")
    return angles


def firing(angles, deg):
    """지금 폭발 행정(점화 후 180도) 중인 실린더."""
    return next((c for c, a in angles.items() if 0 <= (deg - a) % 720 < 180), None)


def draw(ax, asm, meshes, theta):
    ax.cla()
    kin = asm["kinematics"]
    T = poses(kin, theta)
    deg = math.degrees(theta)
    hot = firing(fire_angles(kin), deg)
    tris, cols = [], []
    for name, (m, motion) in meshes.items():
        tris.append(trimesh.transform_points(m.vertices, T[motion])[m.faces])
        shade = 0.45 + 0.55 * np.clip((m.face_normals @ T[motion][:3, :3].T) @ LIGHT, 0, 1)
        base = FIRE if motion == f"piston:{hot}" else color(name)
        rgba = np.tile([*to_rgb(base), 0.18 if name == "block" else 1.0], (len(m.faces), 1))
        rgba[:, :3] *= shade[:, None]
        cols.append(rgba)
    # 한 컬렉션에 모아야 부품 단위가 아니라 면 단위로 앞뒤가 정렬된다
    ax.add_collection3d(Poly3DCollection(np.concatenate(tris), facecolors=np.concatenate(cols), edgecolor="none"))
    lo, hi = limits(meshes)
    ax.set_xlim(lo[0], hi[0]), ax.set_ylim(lo[1], hi[1]), ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(hi - lo, zoom=1.3)
    ax.view_init(elev=18, azim=-60)
    ax.set_axis_off()
    order = "-".join(map(str, kin.get("firing_order", [1, 3, 4, 2])))
    ax.set_title(f"crank {deg:.0f} deg   power stroke: cylinder {hot}   (firing order {order})", fontsize=13)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default=PARTS, type=Path)
    ap.add_argument("--out", default=ROOT / "reports", type=Path)
    args = ap.parse_args()
    asm, meshes = load(args.parts)
    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(projection="3d")
    draw(ax, asm, meshes, 0.0)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "engine.png", dpi=120, bbox_inches="tight")
    frames = [math.radians(d) for d in range(0, 720, 10)]   # 4행정 한 사이클 = 2회전
    FuncAnimation(fig, lambda t: draw(ax, asm, meshes, t), frames=frames).save(
        out / "engine.gif", writer=PillowWriter(fps=12))
    print(f"{out / 'engine.png'}, {out / 'engine.gif'}")


if __name__ == "__main__":
    main()
