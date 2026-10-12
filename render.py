"""발표용 그림: reports/engine.png (조립도), reports/engine.gif (크랭크 1회전).
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


def draw(ax, asm, meshes, theta):
    ax.cla()
    T = poses(asm["kinematics"], theta)
    tris, cols = [], []
    for name, (m, motion) in meshes.items():
        tris.append(trimesh.transform_points(m.vertices, T[motion])[m.faces])
        shade = 0.45 + 0.55 * np.clip((m.face_normals @ T[motion][:3, :3].T) @ LIGHT, 0, 1)
        rgba = np.tile([*to_rgb(color(name)), 0.18 if name == "block" else 1.0], (len(m.faces), 1))
        rgba[:, :3] *= shade[:, None]
        cols.append(rgba)
    # 한 컬렉션에 모아야 부품 단위가 아니라 면 단위로 앞뒤가 정렬된다
    ax.add_collection3d(Poly3DCollection(np.concatenate(tris), facecolors=np.concatenate(cols), edgecolor="none"))
    lo, hi = limits(meshes)
    ax.set_xlim(lo[0], hi[0]), ax.set_ylim(lo[1], hi[1]), ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(hi - lo, zoom=1.3)
    ax.view_init(elev=18, azim=-60)
    ax.set_axis_off()
    ax.set_title(f"crank {math.degrees(theta):.0f} deg (firing order 1-3-4-2)")


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
    frames = [math.radians(d) for d in range(0, 360, 10)]
    FuncAnimation(fig, lambda t: draw(ax, asm, meshes, t), frames=frames).save(
        out / "engine.gif", writer=PillowWriter(fps=12))
    print(f"{out / 'engine.png'}, {out / 'engine.gif'}")


if __name__ == "__main__":
    main()
