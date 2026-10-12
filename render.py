"""발표용 그림: reports/engine.png (조립도), reports/engine.gif (크랭크 1회전).
uv run render.py
"""
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import trimesh  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

from verify import poses  # noqa: E402

ROOT = Path(__file__).resolve().parent
PARTS = ROOT / "parts"
COLORS = {"block": "#9aa5b1", "cover": "#cfe8ff", "base": "#6b5b4b", "bearing": "#c08040",
          "rod": "#2f6fd6", "piston": "#e0e0e0", "pistonpin": "#444444", "handle": "#d64545"}


def color(name):
    return COLORS.get(name.split("_")[0], "#3a8f5c")   # 크랭크 부품 = 초록


def load(skip=("cover",)):
    asm = json.loads((PARTS / "assembly.json").read_text())
    meshes = {}
    for p in asm["parts"]:
        if p["name"] in skip:
            continue
        m = trimesh.load_mesh(PARTS / f"{p['name']}.stl")
        meshes[p["name"]] = (m.simplify_quadric_decimation(face_count=1500) if len(m.faces) > 1500 else m, p["motion"])
    return asm, meshes


def draw(ax, asm, meshes, theta):
    ax.cla()
    T = poses(asm["kinematics"], theta)
    for name, (m, motion) in meshes.items():
        tri = trimesh.transform_points(m.vertices, T[motion])[m.faces]
        alpha = 0.2 if name == "block" else 0.95
        ax.add_collection3d(Poly3DCollection(tri, facecolor=color(name), edgecolor="none", alpha=alpha))
    ax.set_xlim(-80, 70), ax.set_ylim(-50, 50), ax.set_zlim(-35, 70)
    ax.set_box_aspect((150, 100, 105), zoom=1.3)
    ax.view_init(elev=18, azim=-60)
    ax.set_axis_off()
    ax.set_title(f"crank {math.degrees(theta):.0f} deg (firing order 1-3-4-2)")


def main():
    asm, meshes = load()
    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(projection="3d")
    draw(ax, asm, meshes, 0.0)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    fig.savefig(out / "engine.png", dpi=120, bbox_inches="tight")
    frames = [math.radians(d) for d in range(0, 360, 15)]
    FuncAnimation(fig, lambda t: draw(ax, asm, meshes, t), frames=frames).save(
        out / "engine.gif", writer=PillowWriter(fps=8))
    print(f"{out / 'engine.png'}, {out / 'engine.gif'}")


if __name__ == "__main__":
    main()
