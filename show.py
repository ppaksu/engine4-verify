"""지금 parts/ 의 엔진을 브라우저에서 3D 로 본다: make show
마우스로 돌리기/확대, 슬라이더나 재생 버튼으로 크랭크 회전 (폭발 피스톤 빨간색).
reports/show.html 하나로 저장되고, 열 때 three.js 를 CDN 에서 받는다.
"""
import argparse
import base64
import json
import sys
import webbrowser
from pathlib import Path

import trimesh

from render import FIRE, color, fire_angles, load

ROOT = Path(__file__).resolve().parent

PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>엔진 3D 보기</title>
<style>
  html, body { margin: 0; height: 100%; background: #f5f3ee; font-family: -apple-system, "Apple SD Gothic Neo", sans-serif; color: #13202e; }
  #ui { position: fixed; left: 16px; right: 16px; bottom: 16px; display: flex; gap: 12px; align-items: center;
        background: #fbfaf7cc; padding: 12px 16px; border-radius: 12px; border: 1px solid #dcd8cf; }
  #ui input { flex: 1; }
  #ui button { font-size: 16px; padding: 6px 14px; border-radius: 8px; border: 1px solid #13202e; background: #13202e; color: #f5f3ee; cursor: pointer; }
  #label { min-width: 230px; font-variant-numeric: tabular-nums; }
  #help { position: fixed; top: 12px; left: 16px; font-size: 14px; color: #3d4654; }
</style></head><body>
<div id="help">드래그: 회전 · 스크롤: 확대 · 오른쪽 드래그: 이동</div>
<div id="ui"><button id="play">일시정지</button><input id="deg" type="range" min="0" max="719" value="0"><span id="label"></span></div>
<script type="importmap">{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js","three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}</script>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const DATA = __DATA__;
const GLB = Uint8Array.from(atob("__GLB__"), c => c.charCodeAt(0)).buffer;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(devicePixelRatio);
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color("#f5f3ee");
scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.6); sun.position.set(-150, -250, 300); scene.add(sun);

const camera = new THREE.PerspectiveCamera(35, innerWidth / innerHeight, 1, 5000);
camera.up.set(0, 0, 1);                       // 모델은 Z 가 위
const controls = new OrbitControls(camera, renderer.domElement);

// verify.py poses() 와 같은 크랭크-슬라이더 운동
const rx = a => new THREE.Matrix4().makeRotationX(a);
const tr = v => new THREE.Matrix4().makeTranslation(v[0], v[1], v[2]);
function poses(kin, th) {
  const T = { fixed: new THREE.Matrix4(), crank: rx(th) };
  for (const c of kin.cylinders) {
    const pin = t => { const a = t + c.phase * Math.PI / 180; return [c.x, -kin.r * Math.sin(a), kin.r * Math.cos(a)]; };
    const small = p => [c.x, 0, p[2] + Math.sqrt(kin.L * kin.L - p[1] * p[1])];
    const p0 = pin(0), p = pin(th), s0 = small(p0), s = small(p);
    const b0 = Math.atan2(-(s0[1] - p0[1]), s0[2] - p0[2]), b = Math.atan2(-(s[1] - p[1]), s[2] - p[2]);
    T["rod:" + c.id] = tr(p).multiply(rx(b - b0)).multiply(tr(p0.map(v => -v)));
    T["piston:" + c.id] = tr([0, 0, s[2] - s0[2]]);
  }
  return T;
}
const firing = deg => Object.entries(DATA.fire).filter(([, a]) => ((deg - a) % 720 + 720) % 720 < 180).map(([c]) => c);

const parts = [];
new GLTFLoader().parse(GLB, "", gltf => {
  gltf.scene.traverse(o => {
    if (!o.isMesh || !(o.name in DATA.motion)) return;
    const block = o.name === "block";
    o.material = new THREE.MeshStandardMaterial({ color: DATA.color[o.name], roughness: 0.6, metalness: 0.1, flatShading: true,
      transparent: block, opacity: block ? 0.2 : 1, depthWrite: !block });
    o.matrixAutoUpdate = false;
    parts.push(o);
  });
  scene.add(gltf.scene);
  const box = new THREE.Box3().setFromObject(gltf.scene), size = box.getSize(new THREE.Vector3()).length();
  controls.target.copy(box.getCenter(new THREE.Vector3()));
  camera.position.copy(controls.target).add(new THREE.Vector3(size * 0.55, -size * 0.95, size * 0.45));
  controls.update();
  requestAnimationFrame(tick);
});

const slider = document.getElementById("deg"), label = document.getElementById("label"), play = document.getElementById("play");
let playing = true, last = 0;
play.onclick = () => { playing = !playing; play.textContent = playing ? "일시정지" : "재생"; };
slider.oninput = () => { playing = false; play.textContent = "재생"; };
function tick(now) {
  if (playing) slider.value = (Number(slider.value) + (now - last) * 0.09) % 720;   // 약 8초에 2바퀴
  last = now;
  const deg = Number(slider.value), T = poses(DATA.kin, deg * Math.PI / 180), hot = firing(deg);
  for (const o of parts) {
    const m = DATA.motion[o.name];
    o.matrix.copy(T[m]);
    o.material.color.set(m.startsWith("piston:") && hot.includes(m.slice(7)) ? DATA.hot : DATA.color[o.name]);
  }
  label.textContent = `크랭크 ${Math.round(deg)}° · 폭발: ${hot.join(", ") || "없음"} · 점화 순서 ${DATA.order}`;
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}
addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight);
});
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default=ROOT / "parts", type=Path)
    ap.add_argument("--out", default=ROOT / "reports" / "show.html", type=Path)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    if not (args.parts / "assembly.json").exists():
        sys.exit(f"{args.parts}/assembly.json 없음. 먼저 make 로 엔진을 만드세요.")

    asm, meshes = load(args.parts, faces=6000)
    scene = trimesh.Scene()
    for name, (m, _) in meshes.items():
        scene.add_geometry(m, node_name=name, geom_name=name)
    kin = asm["kinematics"]
    data = {
        "kin": kin,
        "motion": {n: motion for n, (_, motion) in meshes.items()},
        "color": {n: color(n) for n in meshes},
        "hot": FIRE,
        "fire": {str(c): a for c, a in fire_angles(kin).items()},
        "order": "-".join(map(str, kin.get("firing_order", [1, 3, 4, 2]))),
    }
    html = PAGE.replace("__DATA__", json.dumps(data)).replace(
        "__GLB__", base64.b64encode(scene.export(file_type="glb")).decode())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html)
    print(f"{args.out} ({args.out.stat().st_size / 1e6:.1f}MB)")
    if not args.no_open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__ == "__main__":
    main()
