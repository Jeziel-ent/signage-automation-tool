import { Suspense, useLayoutEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { Environment, Lightformer, RoundedBox, Stars, useTexture } from "@react-three/drei";
import { Bloom, EffectComposer, ToneMapping, Vignette } from "@react-three/postprocessing";
import { ToneMappingMode } from "postprocessing";
import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import logoImg from "../assets/logo.jpeg";

/**
 * The launch screen's 3D backdrop: a minimal low-poly night street. The main Adinn billboard stands on the far pavement
 * facing the camera; four smaller Adinn hoardings / LED lightboxes with red neon borders line the street and two buildings;
 * cars run both ways on a four-lane road in front of it (headlight pools, taillight trails); an elevated metro line runs
 * behind the billboard with a train on each track at intervals. While waiting the camera drifts slowly (plus pointer
 * parallax); once `leaving` is set it flies a curved path down over the traffic and into the billboard's face, and calls
 * `onArrive` just before contact so the caller can fade out and open the workspace.
 *
 * Everything is procedural (no model files): buildings are ONE instanced mesh with a generated window texture, lane dashes
 * another; cars and train cars are extruded side profiles (bevelled bodies, glass, metallic wheels) whose parts are merged
 * into a few meshes each. Lights that should glow (neon, head/tail lights, sign faces) are HDR colours above 1.0: the
 * EffectComposer renders without tone mapping, Bloom picks up everything over BLOOM_THRESHOLD, then ToneMapping (ACES) runs
 * last. With `still` (prefers-reduced-motion) nothing moves.
 */

export const BG = "#0A0A0C";
const RED = "#E31E24";
const BUILDING = "#121218";
export const FLIGHT_S = 2.4; // camera flight from wherever it is into the billboard

const FACE_W = 4.2;
const FACE_H = 2.1;
const BOARD = { z: -2, y: 6.2, s: 1.8 }; // main billboard: face centre height and scale
const ROAD = { z0: -0.3, z1: 5.5 };
const LANES = [
  { z: 0.55, dir: 1, speed: 9 },
  { z: 2.0, dir: 1, speed: 12.5 },
  { z: 3.2, dir: -1, speed: 11 },
  { z: 4.65, dir: -1, speed: 8.5 },
];
const LOOP = 150; // cars wrap at x = +/- LOOP/2
const DECK = { y: 8.4, z: -12 };
const TRAIN_CYCLE = 22; // seconds between two passes of a train
const TRAIN_RUN = 10; // seconds a pass takes
// HDR colours (well above the bloom threshold) for everything that should glow
const NEON = [5, 0.28, 0.34];
const HEAD = [7, 7, 6.2];
const TAIL = [6, 0.25, 0.35];
// sign faces stay just under the bloom threshold (1.15): bright and crisp, only their neon borders glow
const SIGN_WHITE = [1.08, 1.08, 1.08];
const BLOOM_THRESHOLD = 1.15;
// buildings that carry lit signs on their street-facing facade (front face at z + d/2)
const FACADES = [
  { x: -19, z: -18.5, w: 8, d: 7, h: 24, sign: { y: 11.8, w: 5.6, h: 2.6 } },
  { x: 18, z: -18.5, w: 9, d: 7, h: 28, sign: { y: 11.8, w: 5.6, h: 2.6 } },
  // these two sit under the metro deck and beside the main board: low and towards the outer edge of their facade
  { x: -8.6, z: -18.5, w: 7.5, d: 7, h: 17, sign: { y: 5.7, dx: -1, w: 4.6, h: 2.3 } },
  { x: 8.9, z: -18.5, w: 7.5, d: 7, h: 19, sign: { y: 5.7, dx: 0.9, w: 4.6, h: 2.3 } },
];

// deterministic pseudo-random numbers, so the city is the same on every load
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function canvasTexture(w, h, draw) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  draw(c.getContext("2d"), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function useTextures() {
  return useMemo(() => {
    const rnd = mulberry32(7);
    const windows = canvasTexture(64, 128, (g, w, h) => {
      g.fillStyle = BUILDING;
      g.fillRect(0, 0, w, h);
      for (let row = 0; row < 16; row++) {
        for (let col = 0; col < 4; col++) {
          const lit = rnd() < 0.3;
          g.fillStyle = lit ? (rnd() < 0.12 ? "#ff5a5f" : "#ffd9a8") : "#1a1a23";
          g.fillRect(4 + col * 15, 4 + row * 7.8, 10, 4.2);
        }
      }
    });
    windows.wrapS = windows.wrapT = THREE.RepeatWrapping;
    windows.repeat.set(2, 2);
    // alpha ramps: opaque at u = 0, clear at u = 1 (headlight pools, taillight trails)
    const ramp = canvasTexture(128, 8, (g, w, h) => {
      const gr = g.createLinearGradient(0, 0, w, 0);
      gr.addColorStop(0, "rgba(255,255,255,1)");
      gr.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = gr;
      g.fillRect(0, 0, w, h);
    });
    const halo = canvasTexture(128, 128, (g, w, h) => {
      const gr = g.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
      gr.addColorStop(0, "rgba(255,255,255,0.9)");
      gr.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = gr;
      g.fillRect(0, 0, w, h);
    });
    // a headlight pool: bright at the lamp end (u = 0), fading forwards AND towards both sides
    const beam = canvasTexture(128, 64, (g, w, h) => {
      const along = g.createLinearGradient(0, 0, w, 0);
      along.addColorStop(0, "rgba(255,255,255,1)");
      along.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = along;
      g.fillRect(0, 0, w, h);
      g.globalCompositeOperation = "destination-in";
      const across = g.createLinearGradient(0, 0, 0, h);
      across.addColorStop(0, "rgba(0,0,0,0)");
      across.addColorStop(0.5, "rgba(0,0,0,1)");
      across.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = across;
      g.fillRect(0, 0, w, h);
    });
    return { windows, ramp, halo, beam };
  }, []);
}

function useLogo() {
  const logo = useTexture(logoImg);
  const { gl } = useThree();
  useLayoutEffect(() => {
    // logo.jpeg is 1600x1440 with wide white margins: show only the logo's own box (plus a little air), unstretched at ~2:1
    logo.colorSpace = THREE.SRGBColorSpace;
    logo.generateMipmaps = false;
    logo.minFilter = THREE.LinearFilter;
    logo.anisotropy = gl.capabilities.getMaxAnisotropy();
    logo.repeat.set(0.85, 0.46);
    logo.offset.set(0.075, 0.3);
    logo.needsUpdate = true;
  }, [logo, gl]);
  return logo;
}

// ---------------------------------------------------------------- static city

function Buildings({ windows }) {
  const mesh = useRef();
  const list = useMemo(() => {
    const rnd = mulberry32(21);
    const out = FACADES.map(({ x, z, w, d, h }) => ({ x, z, w, d, h }));
    for (const [rowZ, minH, maxH] of [[-18.5, 9, 22], [-28, 13, 32], [-40, 18, 40]]) {
      for (let x = -80; x < 80; ) {
        const w = 5 + rnd() * 4;
        const cx = x + w / 2;
        x += w + 0.8 + rnd() * 1.5;
        if (rowZ === -18.5 && FACADES.some((f) => Math.abs(cx - f.x) < (f.w + w) / 2 + 0.6)) continue;
        out.push({ x: cx, z: rowZ - rnd() * 3, w, d: 5 + rnd() * 3, h: minH + rnd() * (maxH - minH) });
      }
    }
    for (const side of [-1, 1]) {
      // the near side of the street, only at the edges of the view
      for (let x = 20; x < 80; ) {
        const w = 6 + rnd() * 4;
        out.push({ x: side * (x + w / 2), z: 11 + rnd() * 6, w, d: 6, h: 7 + rnd() * 14 });
        x += w + 1 + rnd() * 2;
      }
    }
    return out;
  }, []);
  const [geometry, materials] = useMemo(() => {
    const side = new THREE.MeshStandardMaterial({ color: "#ffffff", map: windows, emissive: "#ffffff", emissiveMap: windows, emissiveIntensity: 0.6, roughness: 0.85, metalness: 0.1 });
    const roof = new THREE.MeshStandardMaterial({ color: BUILDING, roughness: 0.9 });
    // BoxGeometry face groups: +x, -x, +y, -y, +z, -z
    return [new THREE.BoxGeometry(1, 1, 1), [side, side, roof, roof, side, side]];
  }, [windows]);
  useLayoutEffect(() => {
    const m = new THREE.Matrix4();
    list.forEach((b, i) => {
      m.compose(new THREE.Vector3(b.x, b.h / 2, b.z), new THREE.Quaternion(), new THREE.Vector3(b.w, b.h, b.d));
      mesh.current.setMatrixAt(i, m);
    });
    mesh.current.instanceMatrix.needsUpdate = true;
  }, [list]);
  return <instancedMesh ref={mesh} args={[geometry, materials, list.length]} />;
}

function Street({ halo }) {
  const dashes = useRef();
  const dashList = useMemo(() => {
    const out = [];
    for (const z of [1.3, 3.9]) for (let x = -90; x <= 90; x += 4) out.push([x, z]);
    return out;
  }, []);
  useLayoutEffect(() => {
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(-Math.PI / 2, 0, 0));
    dashList.forEach(([x, z], i) => {
      m.compose(new THREE.Vector3(x, 0.012, z), q, new THREE.Vector3(1.8, 0.1, 1));
      dashes.current.setMatrixAt(i, m);
    });
    dashes.current.instanceMatrix.needsUpdate = true;
  }, [dashList]);
  const roadW = ROAD.z1 - ROAD.z0;
  const lamps = [-56, -42, -28, -14, 14, 28, 42, 56];
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} position={[0, 0, 0]}>
        <planeGeometry args={[400, 400]} />
        <meshStandardMaterial color={BG} roughness={1} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.005, (ROAD.z0 + ROAD.z1) / 2]}>
        <planeGeometry args={[400, roadW]} />
        <meshStandardMaterial color="#101015" roughness={0.42} metalness={0.4} envMapIntensity={0.9} />
      </mesh>
      {/* the solid centre line and the dashed lane lines */}
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.013, 2.6]}>
        <planeGeometry args={[400, 0.08]} />
        <meshBasicMaterial color="#6b5a3a" />
      </mesh>
      <instancedMesh ref={dashes} args={[undefined, undefined, dashList.length]}>
        <planeGeometry args={[1, 1]} />
        <meshBasicMaterial color="#3d3d48" />
      </instancedMesh>
      {/* pavements: far (the billboard's) and near */}
      <mesh position={[0, 0.07, ROAD.z0 - 2.2]}>
        <boxGeometry args={[400, 0.14, 4.4]} />
        <meshStandardMaterial color="#16161c" roughness={0.95} />
      </mesh>
      <mesh position={[0, 0.07, ROAD.z1 + 2.2]}>
        <boxGeometry args={[400, 0.14, 4.4]} />
        <meshStandardMaterial color="#16161c" roughness={0.95} />
      </mesh>
      {/* street lamps on the far pavement, each with a warm pool on the road */}
      {lamps.map((x) => (
        <group key={x} position={[x, 0, ROAD.z0 - 0.5]}>
          <mesh position={[0, 2.6, 0]}>
            <cylinderGeometry args={[0.06, 0.08, 5.2, 8]} />
            <meshStandardMaterial color="#2a2a32" metalness={0.6} roughness={0.4} />
          </mesh>
          <mesh position={[0, 5.2, 0.55]}>
            <boxGeometry args={[0.12, 0.08, 1.2]} />
            <meshStandardMaterial color="#2a2a32" metalness={0.6} roughness={0.4} />
          </mesh>
          <mesh position={[0, 5.12, 1.05]}>
            <boxGeometry args={[0.3, 0.06, 0.3]} />
            <meshBasicMaterial color="#ffe3b3" toneMapped={false} />
          </mesh>
          <mesh rotation-x={-Math.PI / 2} position={[0, 0.02, 1.8]}>
            <planeGeometry args={[5.6, 5.6]} />
            <meshBasicMaterial map={halo} color="#ffcf8a" transparent opacity={0.16} blending={THREE.AdditiveBlending} depthWrite={false} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

function MainBillboard({ logo }) {
  const s = BOARD.s;
  return (
    <group position={[0, 0, BOARD.z]}>
      <group position={[0, BOARD.y, 0]} scale={s}>
        {/* outer light-silver rim, then the dark chrome frame on top of it */}
        <RoundedBox args={[FACE_W + 0.5, FACE_H + 0.5, 0.18]} radius={0.08} smoothness={4} position={[0, 0, -0.02]}>
          <meshStandardMaterial color="#D9DBE1" metalness={0.9} roughness={0.22} envMapIntensity={1.2} />
        </RoundedBox>
        <RoundedBox args={[FACE_W + 0.3, FACE_H + 0.3, 0.26]} radius={0.06} smoothness={4}>
          <meshStandardMaterial color="#23252D" metalness={0.9} roughness={0.18} envMapIntensity={1.4} />
        </RoundedBox>
        {/* the sign face: self-lit like a backlit panel, just under the bloom threshold so the logo stays crisp */}
        <mesh position={[0, 0, 0.135]}>
          <planeGeometry args={[FACE_W, FACE_H]} />
          <meshBasicMaterial map={logo} color={SIGN_WHITE} toneMapped={false} />
        </mesh>
        {/* a thin red light line under the board */}
        <mesh position={[0, -(FACE_H / 2 + 0.3), 0.1]}>
          <boxGeometry args={[FACE_W + 0.5, 0.05, 0.05]} />
          <meshBasicMaterial color={NEON} toneMapped={false} />
        </mesh>
      </group>
      {/* poles from the pavement up into the frame */}
      {[-1.7 * s, 1.7 * s].map((x) => (
        <mesh key={x} position={[x, BOARD.y / 2, -0.3 * s]}>
          <cylinderGeometry args={[0.16, 0.16, BOARD.y, 24]} />
          <meshStandardMaterial color="#2E3039" metalness={0.85} roughness={0.25} envMapIntensity={1.1} />
        </mesh>
      ))}
      <pointLight position={[0, BOARD.y + 0.5, 4]} intensity={40} distance={14} color="#fff1dc" />
    </group>
  );
}

/**
 * An Adinn sign with a red neon border: a roadside hoarding on a pole (`poleH`) or a lightbox on a building facade (`wall`,
 * with two brackets). The face is self-lit (unlit material, HDR white) so it reads clearly against dark buildings whatever
 * the scene lighting, and blooms a little; `position` is the face centre.
 */
function Sign({ logo, halo, position, rotationY = 0, w, h, poleH = 0, wall = false }) {
  const b = 0.08; // neon tube thickness
  const neon = useMemo(() => {
    const parts = [
      new THREE.BoxGeometry(w + 0.3 + b, b, b).translate(0, h / 2 + 0.15, 0.13),
      new THREE.BoxGeometry(w + 0.3 + b, b, b).translate(0, -(h / 2 + 0.15), 0.13),
      new THREE.BoxGeometry(b, h + 0.3, b).translate(-(w / 2 + 0.15), 0, 0.13),
      new THREE.BoxGeometry(b, h + 0.3, b).translate(w / 2 + 0.15, 0, 0.13),
    ];
    return mergeGeometries(parts);
  }, [w, h]);
  return (
    <group position={position} rotation-y={rotationY}>
      <mesh position={[0, 0, -0.16]}>
        <planeGeometry args={[w * 2.1, h * 2.6]} />
        <meshBasicMaterial map={halo} color={RED} transparent opacity={0.4} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
      <mesh>
        <boxGeometry args={[w + 0.3, h + 0.3, 0.22]} />
        <meshStandardMaterial color="#1b1b21" metalness={0.8} roughness={0.28} envMapIntensity={1.3} />
      </mesh>
      <mesh position={[0, 0, 0.115]}>
        <planeGeometry args={[w, h]} />
        <meshBasicMaterial map={logo} color={SIGN_WHITE} toneMapped={false} />
      </mesh>
      <mesh geometry={neon}>
        <meshBasicMaterial color={NEON} toneMapped={false} />
      </mesh>
      <pointLight position={[0, -h * 0.3, 1.6]} intensity={wall ? 8 : 5} distance={wall ? 9 : 7} color={RED} />
      {wall && [-w * 0.3, w * 0.3].map((x) => (
        <mesh key={x} position={[x, 0, -0.2]}>
          <boxGeometry args={[0.18, 0.18, 0.3]} />
          <meshStandardMaterial color="#2a2a32" metalness={0.8} roughness={0.3} />
        </mesh>
      ))}
      {poleH > 0 && (
        <mesh position={[0, -(h / 2 + 0.15) - poleH / 2, -0.15]}>
          <cylinderGeometry args={[0.12, 0.12, poleH, 12]} />
          <meshStandardMaterial color="#2a2a32" metalness={0.8} roughness={0.3} />
        </mesh>
      )}
    </group>
  );
}

// ---------------------------------------------------------------- traffic

const CAR_COLOURS = ["#1c2230", "#2e3440", "#6b0f16", "#c9ccd4", "#101216", "#23364f", "#8a8f99"];

const CARS = (() => {
  const rnd = mulberry32(3);
  const out = [];
  LANES.forEach((lane, li) => {
    for (let k = 0; k < 3; k++) {
      out.push({ ...lane, x0: -LOOP / 2 + (k + rnd() * 0.6) * (LOOP / 3), colour: CAR_COLOURS[(li * 3 + k) % CAR_COLOURS.length], van: rnd() < 0.25 });
    }
  });
  return out;
})();

const box = (w, h, d, x, y, z) => new THREE.BoxGeometry(w, h, d).translate(x, y, z).toNonIndexed();

// side profile (x = length, y = height) extruded across the car's width; `top` draws the upper outline from the rear
// bottom corner to the front, then the underside is traced back with two wheel arches
function profile(half, wx, r, top) {
  const s = new THREE.Shape();
  s.moveTo(-half, 0.2);
  top(s);
  s.lineTo(half, 0.2);
  s.lineTo(wx + r, 0.2);
  s.absarc(wx, 0.2, r, 0, Math.PI, false);
  s.lineTo(-wx + r, 0.2);
  s.absarc(-wx, 0.2, r, 0, Math.PI, false);
  s.lineTo(-half, 0.2);
  return s;
}

function extrude(shape, depth, bevel) {
  const g = new THREE.ExtrudeGeometry(shape, bevel
    ? { depth, bevelEnabled: true, bevelThickness: bevel, bevelSize: bevel * 0.8, bevelSegments: 3, curveSegments: 10 }
    : { depth, bevelEnabled: false, curveSegments: 10 });
  return g.translate(0, 0, -depth / 2);
}

function wheels(wx, zs = 0.46) {
  const tyres = [], rims = [];
  for (const x of [-wx, wx]) {
    for (const z of [-zs, zs]) {
      tyres.push(new THREE.CylinderGeometry(0.2, 0.2, 0.16, 18).rotateX(Math.PI / 2).translate(x, 0.2, z));
      rims.push(new THREE.CylinderGeometry(0.12, 0.12, 0.17, 12).rotateX(Math.PI / 2).translate(x, 0.2, z));
    }
  }
  return [mergeGeometries(tyres), mergeGeometries(rims)];
}

let CAR_KIT = null;
function carKit() {
  if (CAR_KIT) return CAR_KIT;
  const sedanHalf = 1.03, vanHalf = 1.36;
  const sedan = profile(sedanHalf, 0.64, 0.26, (s) => {
    s.lineTo(-1.03, 0.5);
    s.quadraticCurveTo(-1.01, 0.63, -0.84, 0.65);
    s.lineTo(-0.6, 0.67);
    s.quadraticCurveTo(-0.44, 0.93, -0.25, 0.95);
    s.lineTo(0.2, 0.95);
    s.quadraticCurveTo(0.34, 0.93, 0.54, 0.69);
    s.lineTo(0.9, 0.61);
    s.quadraticCurveTo(1.03, 0.57, 1.03, 0.42);
  });
  const sedanGlass = new THREE.Shape();
  sedanGlass.moveTo(-0.54, 0.68);
  sedanGlass.quadraticCurveTo(-0.41, 0.9, -0.25, 0.915);
  sedanGlass.lineTo(0.19, 0.915);
  sedanGlass.quadraticCurveTo(0.31, 0.9, 0.49, 0.69);
  sedanGlass.lineTo(-0.54, 0.68);
  const van = profile(vanHalf, 0.88, 0.27, (s) => {
    s.lineTo(-1.36, 1.12);
    s.quadraticCurveTo(-1.36, 1.26, -1.2, 1.26);
    s.lineTo(0.5, 1.26);
    s.quadraticCurveTo(0.9, 1.22, 1.12, 0.78);
    s.lineTo(1.3, 0.66);
    s.quadraticCurveTo(1.36, 0.62, 1.36, 0.5);
  });
  const vanGlass = new THREE.Shape();
  vanGlass.moveTo(-1.2, 0.82);
  vanGlass.lineTo(-1.2, 1.12);
  vanGlass.lineTo(0.48, 1.12);
  vanGlass.quadraticCurveTo(0.8, 1.08, 1.0, 0.8);
  vanGlass.lineTo(-1.2, 0.82);

  const kit = (shape, glassShape, half, wx, lampY, tailY, pillarX, pillarY, pillarH) => {
    const [tyres, rims] = wheels(wx);
    return {
      half, tailY,
      body: mergeGeometries([extrude(shape, 0.86, 0.06), box(0.07, pillarH, 1.0, pillarX, pillarY, 0)]),
      glass: extrude(glassShape, 1.0, 0),
      tyres, rims,
      heads: mergeGeometries([box(0.03, 0.08, 0.22, half + 0.04, lampY, -0.32), box(0.03, 0.08, 0.22, half + 0.04, lampY, 0.32), box(0.02, 0.025, 0.6, half + 0.045, lampY - 0.09, 0)]),
      tails: mergeGeometries([box(0.03, 0.07, 0.24, -half - 0.04, tailY, -0.34), box(0.03, 0.07, 0.24, -half - 0.04, tailY, 0.34), box(0.02, 0.025, 0.5, -half - 0.045, tailY, 0)]),
    };
  };
  const trail = (half, y) => mergeGeometries([-0.34, 0.34].map((z) => new THREE.PlaneGeometry(2.8, 0.06).rotateY(Math.PI).translate(-half - 1.45, y, z)));
  const sedanKit = kit(sedan, sedanGlass, sedanHalf, 0.64, 0.48, 0.55, -0.04, 0.8, 0.26);
  const vanKit = kit(van, vanGlass, vanHalf, 0.88, 0.55, 0.9, -0.35, 0.97, 0.3);
  sedanKit.trail = trail(sedanHalf, 0.55);
  vanKit.trail = trail(vanHalf, 0.9);
  CAR_KIT = {
    sedan: sedanKit,
    van: vanKit,
    glass: new THREE.MeshPhysicalMaterial({ color: "#07080c", metalness: 0.9, roughness: 0.04, transparent: true, opacity: 0.82, envMapIntensity: 2.2, clearcoat: 1 }),
    tyre: new THREE.MeshStandardMaterial({ color: "#0b0b0d", roughness: 0.85 }),
    rim: new THREE.MeshStandardMaterial({ color: "#d4d8df", metalness: 1, roughness: 0.22, envMapIntensity: 1.6 }),
    head: new THREE.MeshBasicMaterial({ color: new THREE.Color(...HEAD), toneMapped: false }),
    tail: new THREE.MeshBasicMaterial({ color: new THREE.Color(...TAIL), toneMapped: false }),
    paint: {},
  };
  return CAR_KIT;
}

function paint(colour) {
  const k = carKit();
  if (!k.paint[colour]) {
    k.paint[colour] = new THREE.MeshPhysicalMaterial({ color: colour, metalness: 0.55, roughness: 0.3, clearcoat: 1, clearcoatRoughness: 0.06, envMapIntensity: 1.5 });
  }
  return k.paint[colour];
}

function Car({ car, ramp, beam, carRef }) {
  const k = carKit();
  const g = car.van ? k.van : k.sedan;
  return (
    <group ref={carRef} position={[car.x0, 0, car.z]} rotation-y={car.dir > 0 ? 0 : Math.PI}>
      <mesh geometry={g.body} material={paint(car.colour)} />
      <mesh geometry={g.glass} material={k.glass} />
      <mesh geometry={g.tyres} material={k.tyre} />
      <mesh geometry={g.rims} material={k.rim} />
      <mesh geometry={g.heads} material={k.head} />
      <mesh geometry={g.tails} material={k.tail} />
      {/* headlight pool on the road ahead, taillight trails behind */}
      <mesh rotation-x={-Math.PI / 2} position={[g.half + 2.5, 0.02, 0]}>
        <planeGeometry args={[4.8, 1.6]} />
        <meshBasicMaterial map={beam} color="#fff6e0" transparent opacity={0.35} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
      <mesh geometry={g.trail}>
        <meshBasicMaterial map={ramp} color={TAIL} transparent opacity={0.55} blending={THREE.AdditiveBlending} depthWrite={false} side={THREE.DoubleSide} toneMapped={false} />
      </mesh>
    </group>
  );
}

function Traffic({ ramp, beam, still }) {
  const refs = useRef([]);
  useFrame((state) => {
    if (still) return;
    const t = state.clock.elapsedTime;
    CARS.forEach((car, i) => {
      const g = refs.current[i];
      if (!g) return;
      const x = car.x0 + car.dir * car.speed * t + LOOP / 2;
      g.position.x = (((x % LOOP) + LOOP) % LOOP) - LOOP / 2;
    });
  });
  return CARS.map((car, i) => <Car key={i} car={car} ramp={ramp} beam={beam} carRef={(el) => (refs.current[i] = el)} />);
}

// ---------------------------------------------------------------- metro

function Metro() {
  const pillars = [];
  for (let x = -84; x <= 84; x += 14) pillars.push(x);
  return (
    <group position={[0, 0, DECK.z]}>
      <mesh position={[0, DECK.y, 0]}>
        <boxGeometry args={[320, 0.7, 4.4]} />
        <meshStandardMaterial color="#2a2b31" roughness={0.9} />
      </mesh>
      {[-2.1, 2.1].map((z) => (
        <mesh key={z} position={[0, DECK.y + 0.6, z]}>
          <boxGeometry args={[320, 0.5, 0.2]} />
          <meshStandardMaterial color="#33343b" roughness={0.9} />
        </mesh>
      ))}
      {/* a red light line along the front edge of the deck */}
      <mesh position={[0, DECK.y - 0.05, 2.22]}>
        <boxGeometry args={[320, 0.05, 0.05]} />
        <meshBasicMaterial color="#ff2f36" toneMapped={false} />
      </mesh>
      {[-1.6, -0.6, 0.6, 1.6].map((z) => (
        <mesh key={z} position={[0, DECK.y + 0.4, z]}>
          <boxGeometry args={[320, 0.08, 0.08]} />
          <meshStandardMaterial color="#5b5d66" metalness={0.8} roughness={0.3} />
        </mesh>
      ))}
      {pillars.map((x) => (
        <mesh key={x} position={[x, DECK.y / 2, 0]}>
          <boxGeometry args={[0.9, DECK.y, 1.4]} />
          <meshStandardMaterial color="#26272d" roughness={0.95} />
        </mesh>
      ))}
    </group>
  );
}

let TRAIN_KIT = null;
function trainKit() {
  if (TRAIN_KIT) return TRAIN_KIT;
  // side profiles, x = length, y = height; extruded to 1.9 + bevels = ~2.1 wide
  const nose = new THREE.Shape();
  nose.moveTo(-2.9, -0.75);
  nose.lineTo(2.3, -0.75);
  nose.quadraticCurveTo(3.15, -0.72, 3.15, -0.2);
  nose.quadraticCurveTo(3.05, 0.38, 2.05, 0.8);
  nose.lineTo(-2.55, 0.8);
  nose.quadraticCurveTo(-2.9, 0.8, -2.9, 0.45);
  nose.lineTo(-2.9, -0.75);
  const mid = new THREE.Shape();
  mid.moveTo(-2.9, -0.75);
  mid.lineTo(2.9, -0.75);
  mid.lineTo(2.9, 0.45);
  mid.quadraticCurveTo(2.9, 0.8, 2.55, 0.8);
  mid.lineTo(-2.55, 0.8);
  mid.quadraticCurveTo(-2.9, 0.8, -2.9, 0.45);
  mid.lineTo(-2.9, -0.75);
  const shield = new THREE.Shape(); // the nose car's windscreen, seen on its flanks
  shield.moveTo(1.75, 0.0);
  shield.lineTo(2.95, 0.0);
  shield.quadraticCurveTo(2.85, 0.42, 2.02, 0.74);
  shield.lineTo(1.75, 0.74);
  shield.lineTo(1.75, 0.0);

  const under = (x0, x1) => {
    const parts = [];
    for (const bx of [x0 + 1.1, x1 - 1.1]) {
      parts.push(box(1.5, 0.3, 1.7, bx, -0.95, 0));
      for (const dx of [-0.45, 0.45]) {
        for (const z of [-0.78, 0.78]) parts.push(new THREE.CylinderGeometry(0.22, 0.22, 0.1, 14).rotateX(Math.PI / 2).translate(bx + dx, -1.05, z).toNonIndexed());
      }
    }
    parts.push(box(x1 - x0 - 0.4, 0.12, 1.9, (x0 + x1) / 2, -0.85, 0));
    return mergeGeometries(parts);
  };
  const doors = (xs) => mergeGeometries(xs.map((x) => box(0.9, 1.15, 2.13, x, -0.12, 0)));
  const roof = (x) => box(1.3, 0.16, 1.2, x, 0.93, 0);
  TRAIN_KIT = {
    nose: {
      body: mergeGeometries([extrude(nose, 1.9, 0.1), roof(-1.2)]),
      windows: mergeGeometries([box(4.2, 0.44, 2.14, -0.5, 0.22, 0), extrude(shield, 2.16, 0)]),
      doors: doors([-1.9, 0.9]),
      stripe: box(6.0, 0.08, 2.14, 0.1, -0.42, 0),
      lights: mergeGeometries([box(0.05, 0.12, 0.34, 3.13, -0.34, -0.6), box(0.05, 0.12, 0.34, 3.13, -0.34, 0.6)]),
      under: under(-2.9, 3.0),
    },
    mid: {
      body: mergeGeometries([extrude(mid, 1.9, 0.1), roof(0)]),
      windows: box(5.2, 0.44, 2.14, 0, 0.22, 0),
      doors: doors([-1.5, 1.5]),
      stripe: box(5.8, 0.08, 2.14, 0, -0.42, 0),
      under: under(-2.9, 2.9),
    },
    shell: new THREE.MeshPhysicalMaterial({ color: "#d3d6dd", metalness: 0.75, roughness: 0.22, clearcoat: 0.6, clearcoatRoughness: 0.1, envMapIntensity: 1.5 }),
    windows: new THREE.MeshStandardMaterial({ color: "#0b0d13", emissive: "#ffd9a8", emissiveIntensity: 0.72, metalness: 0.7, roughness: 0.08, envMapIntensity: 1.8 }),
    doors: new THREE.MeshStandardMaterial({ color: "#8d939e", metalness: 0.85, roughness: 0.3, envMapIntensity: 1.2 }),
    under: new THREE.MeshStandardMaterial({ color: "#15161b", metalness: 0.5, roughness: 0.6 }),
    stripe: new THREE.MeshBasicMaterial({ color: new THREE.Color(...NEON), toneMapped: false }),
    head: new THREE.MeshBasicMaterial({ color: new THREE.Color(...HEAD), toneMapped: false }),
    tail: new THREE.MeshBasicMaterial({ color: new THREE.Color(...TAIL), toneMapped: false }),
  };
  return TRAIN_KIT;
}

function TrainCar({ x, kind, flip, lamp }) {
  const k = trainKit();
  const g = k[kind];
  return (
    <group position={[x, 0, 0]} rotation-y={flip ? Math.PI : 0}>
      <mesh geometry={g.body} material={k.shell} />
      <mesh geometry={g.windows} material={k.windows} />
      <mesh geometry={g.doors} material={k.doors} />
      <mesh geometry={g.stripe} material={k.stripe} />
      <mesh geometry={g.under} material={k.under} />
      {g.lights && <mesh geometry={g.lights} material={lamp === "tail" ? k.tail : k.head} />}
    </group>
  );
}

// the lead car's nose points along +x; the group is turned round for trains running towards -x
const CONSIST = [
  { x: 0, kind: "nose", lamp: "head" },
  { x: -6.0, kind: "mid" },
  { x: -12.0, kind: "mid" },
  { x: -18.05, kind: "nose", flip: true, lamp: "tail" },
];

function Train({ dir, z, phase, still }) {
  const g = useRef();
  useFrame((state) => {
    if (!g.current) return;
    if (still) {
      g.current.position.x = dir * 6;
      return;
    }
    const tt = (state.clock.elapsedTime + phase) % TRAIN_CYCLE;
    const run = Math.min(1, tt / TRAIN_RUN);
    g.current.visible = tt < TRAIN_RUN;
    g.current.position.x = dir * (-95 + run * 190);
  });
  return (
    <group ref={g} position={[0, DECK.y + 1.9, DECK.z + z]} rotation-y={dir > 0 ? 0 : Math.PI}>
      {CONSIST.map((c, i) => <TrainCar key={i} {...c} />)}
    </group>
  );
}

// ---------------------------------------------------------------- camera

const smooth = (x) => { const u = Math.min(1, Math.max(0, x)); return u * u * (3 - 2 * u); };
const easeInOutCubic = (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2);

// how far back the camera stands so the main billboard (with a margin) always fits the canvas width
function baseDistance(aspect, fovDeg) {
  const tanH = Math.tan((fovDeg * Math.PI) / 360) * aspect;
  const halfW = (FACE_W * BOARD.s + 2) / 2;
  return Math.max(24, (halfW / tanH) * 1.35);
}

function CameraRig({ leaving, still, onArrive }) {
  const { camera, pointer, size } = useThree();
  const look = useRef(new THREE.Vector3(0, 5.6, 0));
  const inited = useRef(false);
  const flight = useRef(null);
  const tmp = useMemo(() => new THREE.Vector3(), []);

  useFrame((state, dt) => {
    const t = still ? 0 : state.clock.elapsedTime;
    if (leaving && !still) {
      if (!flight.current) {
        const p0 = camera.position.clone();
        flight.current = {
          t0: state.clock.elapsedTime,
          arrived: false,
          look0: look.current.clone(),
          // down over the traffic, low across the lanes, then up into the board's face
          curve: new THREE.CatmullRomCurve3([
            p0,
            new THREE.Vector3(p0.x * 0.4 - 3.2, 5, 11),
            new THREE.Vector3(2.4, 1.9, 3.0),
            new THREE.Vector3(0.8, BOARD.y - 1, BOARD.z + 5),
            new THREE.Vector3(0, BOARD.y, BOARD.z + 0.6),
          ], false, "centripetal"),
        };
      }
      const f = flight.current;
      const u = Math.min(1, (state.clock.elapsedTime - f.t0) / FLIGHT_S);
      camera.position.copy(f.curve.getPointAt(easeInOutCubic(u)));
      const low = tmp.set(0.6, 2.4, BOARD.z); // look along the street while low, then up at the board
      if (u < 0.55) look.current.lerpVectors(f.look0, low, smooth(u / 0.55));
      else look.current.lerpVectors(low, new THREE.Vector3(0, BOARD.y, BOARD.z), smooth((u - 0.55) / 0.45));
      camera.lookAt(look.current);
      if (u >= 0.92 && !f.arrived) {
        f.arrived = true;
        onArrive?.();
      }
      return;
    }
    const d = baseDistance(size.width / Math.max(1, size.height), camera.fov);
    const lift = (d - 24) * 0.2;
    const px = still ? 0 : pointer.x;
    const py = still ? 0 : pointer.y;
    const target = tmp.set(Math.sin(t * 0.13) * 1.8 + px * 1.2, 14 + lift + Math.sin(t * 0.21) * 0.3 + py * 0.4, BOARD.z + d + Math.sin(t * 0.1) * 0.8);
    if (!inited.current) {
      camera.position.copy(target);
      inited.current = true;
    } else {
      camera.position.lerp(target, 1 - Math.exp(-dt * 2));
    }
    look.current.set(Math.sin(t * 0.09) * 0.4, 5.6 + lift * 0.3, 0);
    camera.lookAt(look.current);
  });
  return null;
}

// ---------------------------------------------------------------- scene

function City({ leaving, still, onArrive }) {
  const logo = useLogo();
  const { windows, ramp, halo, beam } = useTextures();
  return (
    <>
      <Buildings windows={windows} />
      <Street halo={halo} />
      <MainBillboard logo={logo} />
      <Sign logo={logo} halo={halo} position={[-12.5, 3.6, -2.6]} rotationY={0.22} w={4.4} h={2.2} poleH={2.4} />
      <Sign logo={logo} halo={halo} position={[13, 3.8, -3]} rotationY={-0.24} w={4.4} h={2.2} poleH={2.6} />
      {FACADES.map((f) => (
        <Sign key={f.x} logo={logo} halo={halo} position={[f.x + (f.sign.dx || 0), f.sign.y, f.z + f.d / 2 + 0.26]} w={f.sign.w} h={f.sign.h} wall />
      ))}
      <Metro />
      <Train dir={1} z={-1.1} phase={0} still={still} />
      <Train dir={-1} z={1.1} phase={TRAIN_CYCLE / 2} still={still} />
      <Traffic ramp={ramp} beam={beam} still={still} />
      <CameraRig leaving={leaving} still={still} onArrive={onArrive} />
    </>
  );
}

export default function CityScene({ leaving, still, onArrive }) {
  return (
    <>
      <color attach="background" args={[BG]} />
      <fog attach="fog" args={[BG, 34, 110]} />
      <ambientLight intensity={0.22} color="#fff4e6" />
      <hemisphereLight args={["#2a2a38", BG, 0.35]} />
      <directionalLight position={[-6, 14, 10]} intensity={0.6} color="#ffe9d2" />
      <pointLight position={[0, 9, -4]} intensity={30} distance={30} color={RED} />
      {/* reflections for the chrome, the car paint, the train shell and the wet road: white, warm and red strips */}
      <Environment resolution={256} frames={1}>
        <Lightformer form="rect" intensity={2.6} position={[0, 6, 4]} scale={[14, 2, 1]} color="#ffffff" />
        <Lightformer form="rect" intensity={1.4} position={[-7, 1.5, 1]} scale={[2, 8, 1]} color="#ffe2c0" />
        <Lightformer form="rect" intensity={1.6} position={[7, 0.5, -2]} scale={[2, 7, 1]} color={RED} />
        <Lightformer form="rect" intensity={0.8} position={[0, -2, -6]} scale={[18, 1, 1]} color="#8fa3c8" />
      </Environment>
      <Stars radius={90} depth={40} count={still ? 300 : 700} factor={3} saturation={0} fade speed={still ? 0 : 0.2} />
      <Suspense fallback={null}>
        <City leaving={leaving} still={still} onArrive={onArrive} />
      </Suspense>
      <EffectComposer multisampling={4}>
        <Bloom mipmapBlur luminanceThreshold={BLOOM_THRESHOLD} luminanceSmoothing={0.2} intensity={0.9} radius={0.72} />
        <Vignette offset={0.3} darkness={0.6} />
        <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
      </EffectComposer>
    </>
  );
}
