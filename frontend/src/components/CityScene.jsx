import { Suspense, memo, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { Environment, Lightformer, RoundedBox, Stars, useTexture } from "@react-three/drei";
import { Bloom, EffectComposer, ToneMapping, Vignette } from "@react-three/postprocessing";
import { ToneMappingMode } from "postprocessing";
import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import logoImg from "../assets/logo.jpeg";
import { ARRIVE_AT, FLIGHT_S } from "./cityTiming.js";

/**
 * The launch screen's 3D backdrop: a minimal low-poly night street. The main Adinn billboard stands on the far pavement
 * facing the camera - the one brand sign in the scene - in front of a row of dimly lit shops with generic names, behind them a
 * New York-style skyline of glass towers (setbacks, crowns, a few masts);
 * cars run both ways on a four-lane road in front of it (headlight pools, taillight trails); an elevated metro line runs
 * behind the billboard with a train on each track at intervals. While waiting the camera drifts slowly (plus pointer
 * parallax); once `leaving` is set it flies a curved path down over the traffic and into the billboard's face, and calls
 * `onArrive` just before contact so the caller can fade out and open the workspace.
 *
 * Everything is procedural (no model files): the towers are ONE merged mesh with a generated curtain-wall texture, lane dashes
 * another; cars and train cars are extruded side profiles (bevelled bodies, glass, metallic wheels) whose parts are merged
 * into a few meshes each. Lights that should glow (neon, head/tail lights, sign faces) are HDR colours above 1.0: the
 * EffectComposer renders without tone mapping, Bloom picks up everything over BLOOM_THRESHOLD, then ToneMapping (ACES) runs
 * last. With `still` (prefers-reduced-motion) nothing moves.
 */

export const BG = "#0A0A0C";
const RED = "#E31E24";
const BUILDING = "#121218";
export { FLIGHT_S };

const FACE_W = 4.2;
const FACE_H = 2.1;
const BOARD = { z: -2, y: 6.2, s: 1.8 }; // main billboard: face centre height and scale
const BOARD_FACE_Z = BOARD.z + 0.135 * BOARD.s; // world z of the sign face (MainBillboard puts it 0.135 in front, scaled)
const FLIGHT_END_DIST = 1.6; // where the fly-in ends: this far in front of the face, which then fills the whole frame
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
// the sign face stays just under the bloom threshold (1.15): bright and crisp, only its neon line glows
const SIGN_WHITE = [1.08, 1.08, 1.08];
const BLOOM_THRESHOLD = 1.15;
const BUILDING_KEEP = 0.55; // share of generated building slots actually built: a sparser skyline behind the hero board
// the curtain-wall texture tile: 4 panels across x 8 floors, each panel 0.9 wide and 0.85 tall in world units - towers get
// UVs from their real size, so a floor is the same height on every tower (no stretched windows on the tall ones)
const GLASS_TILE = { cols: 4, rows: 8, panelW: 0.9, floorH: 0.85 };
const FRAME_EDGE_MIN_Z = -24; // towers further back than this get no outline (see Skyline)
const LEDGE_MIN_Z = -34; // ledge slabs on the front and middle rows only (sub-pixel further back)
const LEDGE_FLOORS = 4; // a ledge every 4 floors
// the two towers flanking the main billboard, each carrying a large LED Adinn board on its street face
const FLANK_TOWERS = [
  { x: -17, z: -19.5, w: 10, d: 7, h: 32, boardY: 16.5, boardW: 7 },
  { x: 17.5, z: -19.5, w: 10, d: 7, h: 35, boardY: 17.5, boardW: 7 },
];
const LED_WHITE = [1.12, 1.12, 1.12]; // LED faces: a touch brighter than the main board's, still just under the bloom threshold
const SHOP_FRONT_Z = -5.2; // storefront row, just behind the far pavement (which ends at ROAD.z0 - 4.4)

// the logo is the only image the scene loads: start fetching/decoding it as soon as this module is imported, so it is
// ready by the time <City> mounts instead of the board popping in a moment after the rest of the street
useTexture.preload(logoImg);

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
    // glass curtain wall, one tile = GLASS_TILE.cols x GLASS_TILE.rows panels: dark blue-grey glass with a faint vertical
    // sheen per panel, framed by thin metallic mullions (vertical) and spandrels (horizontal, slightly heavier)
    const { cols, rows } = GLASS_TILE;
    const pw = 32, ph = 32;
    const lit = []; // which panels are lit, shared by the colour map and the emissive map so they line up
    for (let i = 0; i < cols * rows; i++) lit.push(rnd() < 0.12 ? (rnd() < 0.3 ? "#c9d6f2" : "#e8c79a") : null);
    const glass = canvasTexture(cols * pw, rows * ph, (g) => {
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const x = c * pw, y = r * ph;
          const tone = 30 + Math.floor(rnd() * 14);
          const gr = g.createLinearGradient(x, y, x + pw, y + ph);
          gr.addColorStop(0, `rgb(${tone + 8}, ${tone + 20}, ${tone + 42})`); // a cool sheen top-left
          gr.addColorStop(1, `rgb(${tone - 12}, ${tone - 6}, ${tone + 8})`);
          g.fillStyle = gr;
          g.fillRect(x, y, pw, ph);
          if (lit[r * cols + c]) {
            g.fillStyle = lit[r * cols + c];
            g.fillRect(x + 3, y + 5, pw - 6, ph - 10);
          }
        }
      }
      g.fillStyle = "#4a5568"; // mullions
      for (let c = 0; c <= cols; c++) g.fillRect(c * pw - 1, 0, 2, rows * ph);
      g.fillStyle = "#3a4353"; // spandrels
      for (let r = 0; r <= rows; r++) g.fillRect(0, r * ph - 2, cols * pw, 4);
    });
    const glassLit = canvasTexture(cols * pw, rows * ph, (g) => {
      g.fillStyle = "#000";
      g.fillRect(0, 0, cols * pw, rows * ph);
      lit.forEach((colour, i) => {
        if (!colour) return;
        g.fillStyle = colour;
        g.fillRect((i % cols) * pw + 3, Math.floor(i / cols) * ph + 5, pw - 6, ph - 10);
      });
    });
    for (const t of [glass, glassLit]) {
      t.wrapS = t.wrapT = THREE.RepeatWrapping;
      t.anisotropy = 4; // the towers are seen at grazing angles
    }
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
    return { glass, glassLit, ramp, halo, beam };
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

// a box with UVs from its real size, so the curtain-wall texture keeps one panel width / floor height on every face
function glassBox(w, h, d, x, y, z, uShift) {
  const g = new THREE.BoxGeometry(w, h, d);
  const uv = g.attributes.uv;
  const tileW = GLASS_TILE.cols * GLASS_TILE.panelW;
  const tileH = GLASS_TILE.rows * GLASS_TILE.floorH;
  // BoxGeometry faces, 4 vertices each: +x, -x, +y, -y, +z, -z; (face width, face height) of each
  const faces = [[d, h], [d, h], [w, d], [w, d], [w, h], [w, h]];
  faces.forEach(([fw, fh], f) => {
    for (let v = f * 4; v < f * 4 + 4; v++) uv.setXY(v, uv.getX(v) * (fw / tileW) + uShift, uv.getY(v) * (fh / tileH));
  });
  return g.translate(x, y, z);
}

/**
 * The skyline: New York-style glass towers. Every tower is a glass shaft, often stepped back into a narrower upper block and
 * a crown; on the nearer rows the facades carry real structure - corner columns, vertical fins (mullions) across the street
 * face and a ledge slab every few floors - and every roof carries plant: HVAC boxes, sometimes a cooling drum, a spire or a
 * mast with a red aviation light. Two fixed towers flank the main billboard and carry large LED Adinn boards.
 *
 * Draw calls stay flat whatever the count: all glass is ONE merged mesh (curtain-wall texture, UVs from each box's real size),
 * all structure ONE merged mesh (dark metal), outlines ONE line mesh. Fine detail (fins, ledges, outlines) is only built on
 * the near rows: further back it is thinner than a pixel and would crawl as the camera sways.
 */
function Skyline({ glass, glassLit, logo, halo }) {
  const parts = useMemo(() => {
    const rnd = mulberry32(21);
    const boxes = []; // glass: [w, h, d, cx, cy, cz]
    const metal = []; // structure geometries, merged at the end
    const masts = []; // [x, y0, z, h]
    const add = (w, h, d, x, y, z) => metal.push(new THREE.BoxGeometry(w, h, d).translate(x, y, z));

    const facade = (w, h, d, x, y0, z, near) => {
      boxes.push([w, h, d, x, y0 + h / 2, z]);
      if (z < LEDGE_MIN_Z) return;
      // ledge slabs every LEDGE_FLOORS floors, all the way round
      const step = LEDGE_FLOORS * GLASS_TILE.floorH;
      for (let y = y0 + step; y < y0 + h - 0.5; y += step) add(w + 0.18, 0.1, d + 0.18, x, y, z);
      if (!near) return;
      // corner columns, and vertical fins across the street-facing (+z) face every two panels
      for (const sx of [-1, 1]) for (const sz of [-1, 1]) add(0.28, h, 0.28, x + sx * (w / 2 - 0.1), y0 + h / 2, z + sz * (d / 2 - 0.1));
      const pitch = GLASS_TILE.panelW * 2;
      for (let fx = -w / 2 + pitch; fx < w / 2 - 0.4; fx += pitch) add(0.1, h, 0.24, x + fx, y0 + h / 2, z + d / 2 + 0.12);
    };

    const roof = (w, d, x, top, z) => {
      // parapet lip, then 2-4 HVAC boxes and sometimes a cooling drum, kept inside the roof footprint
      add(w + 0.1, 0.35, d + 0.1, x, top + 0.17, z);
      const n = 2 + Math.floor(rnd() * 3);
      for (let i = 0; i < n; i++) {
        const bw = w * (0.14 + rnd() * 0.16), bd = d * (0.14 + rnd() * 0.16), bh = 0.6 + rnd() * 0.8;
        add(bw, bh, bd, x + (rnd() - 0.5) * (w - bw) * 0.8, top + 0.35 + bh / 2, z + (rnd() - 0.5) * (d - bd) * 0.8);
      }
      if (rnd() < 0.35) {
        const r = Math.min(w, d) * 0.12;
        metal.push(new THREE.CylinderGeometry(r, r * 1.1, 1.4, 12).translate(x + w * 0.22, top + 1.05, z - d * 0.2));
      }
    };

    // one tower: shaft, optional setback + crown, roof plant, optional spire / mast
    const tower = (x, z, w, d, h, { tiers = true, near = false, spire = true } = {}) => {
      let tw = w, td = d, top;
      if (!tiers || h < 20 || rnd() > 0.6) {
        facade(w, h, d, x, 0, z, near);
        top = h;
      } else {
        const h1 = h * 0.72, h2 = h * 0.2;
        facade(w, h1, d, x, 0, z, near);
        roof(w, d, x, h1, z); // the setback terrace carries plant too
        tw = w * 0.72; td = d * 0.72;
        facade(tw, h2, td, x, h1, z, near);
        top = h1 + h2;
        if (rnd() < 0.5) {
          const h3 = h * 0.08;
          tw = w * 0.42; td = d * 0.42;
          facade(tw, h3, td, x, top, z, false);
          top += h3;
        }
      }
      roof(tw, td, x, top, z);
      if (spire && h > 26) {
        const r = rnd();
        if (r < 0.18) {
          // a tapered spire on a small drum
          const sh = 5 + rnd() * 5;
          metal.push(
            new THREE.CylinderGeometry(tw * 0.18, tw * 0.2, 1.2, 16).translate(x, top + 0.95, z),
            new THREE.CylinderGeometry(0.04, tw * 0.12, sh, 12).translate(x, top + 1.55 + sh / 2, z),
          );
        } else if (r < 0.4) {
          masts.push([x, top + 0.35, z, 3 + rnd() * 5]);
        }
      }
      return top;
    };

    // the two LED-board towers flanking the billboard, fixed so the boards always frame it
    for (const t of FLANK_TOWERS) tower(t.x, t.z, t.w, t.d, t.h, { near: true, spire: true });
    // three rows behind the metro, each taller than the one in front: the back row reaches past the top of the frame
    for (const [rowZ, minH, maxH] of [[-18.5, 12, 28], [-28, 20, 42], [-40, 30, 58]]) {
      for (let x = -80; x < 80; ) {
        const w = 5 + rnd() * 4;
        const cx = x + w / 2;
        x += w + 0.8 + rnd() * 1.5;
        const z = rowZ - rnd() * 3, d = 5 + rnd() * 3, h = minH + rnd() * (maxH - minH);
        if (rnd() >= BUILDING_KEEP) continue; // decided after the slot's own numbers, so the kept ones don't shift around
        if (rowZ === -18.5 && FLANK_TOWERS.some((t) => Math.abs(cx - t.x) < (t.w + w) / 2 + 0.8)) continue;
        tower(cx, z, w, d, h, { near: z > FRAME_EDGE_MIN_Z });
      }
    }
    for (const side of [-1, 1]) {
      // the near side of the street, only at the edges of the view
      for (let x = 20; x < 80; ) {
        const w = 6 + rnd() * 4;
        const cx = side * (x + w / 2), z = 11 + rnd() * 6, h = 8 + rnd() * 16;
        x += w + 1 + rnd() * 2;
        if (rnd() < BUILDING_KEEP) tower(cx, z, w, 6, h, { tiers: false, near: true, spire: false });
      }
    }

    const geos = boxes.map(([w, h, d, x, y, z]) => glassBox(w, h, d, x, y, z, rnd()));
    // frame outlines only on the near towers (front row behind the metro, and the near side of the street): on the rows
    // 28-40+ units back a 1 px line is thinner than the tower's own pixels and crawls/shimmers as the camera sways
    const edges = boxes
      .filter(([, , , , , z]) => z > FRAME_EDGE_MIN_Z)
      .map(([w, h, d, x, y, z]) => new THREE.EdgesGeometry(new THREE.BoxGeometry(w, h, d)).translate(x, y, z));
    const mastGeo = masts.length ? mergeGeometries(masts.map(([x, y0, z, h]) => new THREE.BoxGeometry(0.16, h, 0.16).translate(x, y0 + h / 2, z))) : null;
    const beaconGeo = masts.length ? mergeGeometries(masts.map(([x, y0, z, h]) => new THREE.BoxGeometry(0.3, 0.3, 0.3).translate(x, y0 + h, z))) : null;
    return { towers: mergeGeometries(geos), structure: mergeGeometries(metal), frames: mergeGeometries(edges), mastGeo, beaconGeo };
  }, []);
  // standard (not physical) material: no clearcoat layer to shade; high metalness makes the glass mirror the Environment's
  // light strips (the sheen), and the curtain-wall map tints those reflections panel by panel
  const material = useMemo(() => new THREE.MeshStandardMaterial({
    color: "#ffffff", map: glass, emissive: "#ffffff", emissiveMap: glassLit, emissiveIntensity: 0.38,
    metalness: 0.85, roughness: 0.15, envMapIntensity: 1.5,
  }), [glass, glassLit]);
  return (
    <group>
      <mesh geometry={parts.towers} material={material} />
      <mesh geometry={parts.structure}>
        <meshStandardMaterial color="#2b3341" metalness={0.8} roughness={0.35} envMapIntensity={1.2} />
      </mesh>
      <lineSegments geometry={parts.frames}>
        <lineBasicMaterial color="#6b7789" transparent opacity={0.35} depthWrite={false} />
      </lineSegments>
      {parts.mastGeo && (
        <mesh geometry={parts.mastGeo}>
          <meshStandardMaterial color="#2a2f38" metalness={0.8} roughness={0.35} />
        </mesh>
      )}
      {/* aviation lights: a small red point on each mast, barely over the bloom threshold */}
      {parts.beaconGeo && (
        <mesh geometry={parts.beaconGeo}>
          <meshBasicMaterial color={[1.6, 0.12, 0.12]} toneMapped={false} />
        </mesh>
      )}
      {FLANK_TOWERS.map((t) => (
        <LedBoard key={t.x} logo={logo} halo={halo} position={[t.x, t.boardY, t.z + t.d / 2 + 0.4]} w={t.boardW} />
      ))}
    </group>
  );
}

/**
 * A large LED Adinn board on a tower facade: a dark metal cabinet, the logo face self-lit just under the bloom threshold (so
 * the white stays crisp and the logo readable - above it the whole face would bloom into a white blob), a glowing red neon
 * frame and a soft red halo on the facade behind it. `position` is the face centre; the face is 2:1 like the logo crop.
 */
function LedBoard({ logo, halo, position, w }) {
  const h = w / 2;
  const b = 0.12; // neon tube
  const neon = useMemo(() => mergeGeometries([
    new THREE.BoxGeometry(w + 0.4 + b, b, b).translate(0, h / 2 + 0.2, 0.08),
    new THREE.BoxGeometry(w + 0.4 + b, b, b).translate(0, -(h / 2 + 0.2), 0.08),
    new THREE.BoxGeometry(b, h + 0.4, b).translate(-(w / 2 + 0.2), 0, 0.08),
    new THREE.BoxGeometry(b, h + 0.4, b).translate(w / 2 + 0.2, 0, 0.08),
  ]), [w, h]);
  return (
    <group position={position}>
      <mesh position={[0, 0, -0.3]}>
        <planeGeometry args={[w * 2, h * 2.8]} />
        <meshBasicMaterial map={halo} color={RED} transparent opacity={0.45} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
      <mesh position={[0, 0, -0.12]}>
        <boxGeometry args={[w + 0.4, h + 0.4, 0.24]} />
        <meshStandardMaterial color="#1b1b21" metalness={0.8} roughness={0.3} />
      </mesh>
      <mesh position={[0, 0, 0.01]}>
        <planeGeometry args={[w, h]} />
        <meshBasicMaterial map={logo} color={LED_WHITE} toneMapped={false} />
      </mesh>
      <mesh geometry={neon}>
        <meshBasicMaterial color={NEON} toneMapped={false} />
      </mesh>
    </group>
  );
}

/**
 * Street-level shops along the back of the billboard's pavement: low units with a warmly lit shop window and an illuminated
 * Adinn lightbox on the fascia (dark cabinet, backlit logo face, a thin red neon line under it). Bodies, windows, cabinets,
 * faces and neon lines are each ONE merged mesh - every face shares the logo texture - so the whole row is five draw calls.
 */
function Storefronts({ logo }) {
  const { bodies, windows, cabinets, faces, neons, glazing } = useMemo(() => {
    const rnd = mulberry32(44);
    const units = [];
    for (let x = -48; x < 48; ) {
      const w = 4 + rnd() * 2.2;
      const cx = x + w / 2;
      x += w + 0.25;
      if (Math.abs(cx) < 4.5) continue; // directly behind the billboard's poles: hidden anyway, and keeps the board clean
      units.push({ x: cx, w, h: 3.4 + rnd() * 1.1 });
    }
    const depth = 3;
    const fz = SHOP_FRONT_Z;
    const bodies = mergeGeometries(units.map((u) => new THREE.BoxGeometry(u.w, u.h, depth).translate(u.x, u.h / 2, fz - depth / 2)));
    const windows = mergeGeometries(units.map((u) => new THREE.PlaneGeometry(u.w - 0.7, u.h * 0.5).translate(u.x, 0.35 + u.h * 0.25, fz + 0.01)));
    // the lightbox fills the fascia band above the window, at the logo's 2:1
    const boxes = units.map((u) => {
      const band0 = 0.35 + u.h * 0.5, band = u.h - band0;
      const sh = Math.min(band - 0.3, 1.2);
      return { x: u.x, y: band0 + band / 2, sw: sh * 2, sh };
    });
    const cabinets = mergeGeometries(boxes.map((s) => new THREE.BoxGeometry(s.sw + 0.16, s.sh + 0.16, 0.14).translate(s.x, s.y, fz + 0.07)));
    const faces = mergeGeometries(boxes.map((s) => new THREE.PlaneGeometry(s.sw, s.sh).translate(s.x, s.y, fz + 0.145)));
    const neons = mergeGeometries(boxes.map((s) => new THREE.BoxGeometry(s.sw + 0.16, 0.05, 0.05).translate(s.x, s.y - s.sh / 2 - 0.14, fz + 0.12)));
    // a shop window: warm interior light falling off towards the floor, framed glazing bars and a transom
    const glazing = canvasTexture(256, 128, (g, w, h) => {
      const gr = g.createLinearGradient(0, 0, 0, h);
      gr.addColorStop(0, "#a07a4c");
      gr.addColorStop(1, "#2e241a");
      g.fillStyle = gr;
      g.fillRect(0, 0, w, h);
      g.fillStyle = "#15161b";
      g.fillRect(0, 0, w, 6); g.fillRect(0, h - 6, w, 6); g.fillRect(0, 0, 6, h); g.fillRect(w - 6, 0, 6, h);
      g.fillRect(0, h * 0.24, w, 5);
      for (const fx of [1 / 3, 2 / 3]) g.fillRect(w * fx - 3, 0, 6, h);
    });
    return { bodies, windows, cabinets, faces, neons, glazing };
  }, []);
  return (
    <group>
      <mesh geometry={bodies}>
        <meshStandardMaterial color={BUILDING} roughness={0.9} />
      </mesh>
      {/* shop windows: a warm interior glow (tone mapped, far below the bloom threshold) */}
      <mesh geometry={windows}>
        <meshBasicMaterial map={glazing} color={[0.62, 0.62, 0.62]} />
      </mesh>
      <mesh geometry={cabinets}>
        <meshStandardMaterial color="#1b1b21" metalness={0.7} roughness={0.35} />
      </mesh>
      {/* backlit faces: bright, but under the bloom threshold so the logo stays legible */}
      <mesh geometry={faces}>
        <meshBasicMaterial map={logo} color={SIGN_WHITE} toneMapped={false} />
      </mesh>
      <mesh geometry={neons}>
        <meshBasicMaterial color={NEON} toneMapped={false} />
      </mesh>
    </group>
  );
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
        <meshStandardMaterial color={BG} roughness={1} depthWrite />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.005, (ROAD.z0 + ROAD.z1) / 2]}>
        <planeGeometry args={[400, roadW]} />
        <meshStandardMaterial color="#101015" roughness={0.42} metalness={0.4} envMapIntensity={0.9} depthWrite polygonOffset polygonOffsetFactor={-1} polygonOffsetUnits={-1} />
      </mesh>
      {/* the solid centre line and the dashed lane lines */}
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.013, 2.6]}>
        <planeGeometry args={[400, 0.08]} />
        <meshBasicMaterial color="#6b5a3a" depthWrite polygonOffset polygonOffsetFactor={-2} polygonOffsetUnits={-2} />
      </mesh>
      <instancedMesh ref={dashes} args={[undefined, undefined, dashList.length]}>
        <planeGeometry args={[1, 1]} />
        <meshBasicMaterial color="#3d3d48" depthWrite polygonOffset polygonOffsetFactor={-2} polygonOffsetUnits={-2} />
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
            <meshBasicMaterial map={halo} color="#ffcf8a" transparent opacity={0.16} blending={THREE.AdditiveBlending} depthWrite={false} polygonOffset polygonOffsetFactor={-3} polygonOffsetUnits={-3} />
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
        <meshBasicMaterial map={beam} color="#fff6e0" transparent opacity={0.35} blending={THREE.AdditiveBlending} depthWrite={false} polygonOffset polygonOffsetFactor={-3} polygonOffsetUnits={-3} />
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

// Waiting view: a flat, near eye-level shot across the road at the main billboard, looking slightly UP at it (the board's
// face is centred at BOARD.y). Low enough that the road reads as a horizontal band in front, high enough that passing cars
// don't cover the board. The sway only slides sideways, with a barely-there vertical bob.
// Low eye, tilted up ~13 deg: the bottom edge of the frame lands on the road's near kerb (no empty dark foreground) and the
// billboard sits just under the centre with the skyline rising above it. (From y 2.2 the kerb, ~15.5 ahead, is ~8 deg down;
// with the 21 deg half-fov that needs a 13 deg upward pitch -> aim at y ~7.6 over the board's 24-unit distance.)
export const CAMERA_EYE_Y = 2.2;
const CAMERA_LOOK_Y = 7.6;
const SWAY_X = 1.2; // amplitude of the sideways drift
const SWAY_Y = 0.1; // amplitude of the vertical bob

const BASE_FOV = 42; // vertical field of view of the VISIBLE picture (the area above the launch controls)

/**
 * The canvas always covers the whole screen, with the title/controls overlaid on its bottom `inset` px. So the waiting
 * view keeps its framing - the board centred in the area ABOVE the controls - the camera renders a virtual frame
 * H + inset tall (setViewOffset) whose field of view is widened so the visible area above the controls spans BASE_FOV:
 * the camera's axis then lands in the middle of that area, at the same scale as a BASE_FOV shot of it. During the fly-in
 * `inset` eases to 0 and this becomes an ordinary full-screen BASE_FOV shot, so the close-up fills the entire screen - no
 * dark band under it (the old "half-black screen": the controls' area was not canvas at all), and no canvas resize mid-
 * flight (resizing re-allocates the bloom targets every frame, which stuttered).
 */
function frameProjection(camera, width, height, inset) {
  const i = Math.min(Math.max(0, inset), height * 0.6);
  const fullH = height + i;
  const half = Math.tan((BASE_FOV * Math.PI) / 360) * (fullH / (height - i));
  camera.fov = (Math.atan(half) * 360) / Math.PI;
  camera.aspect = width / fullH;
  camera.setViewOffset(width, fullH, 0, i, width, height); // also updates the projection matrix
  return height - i; // the visible picture's height
}

function CameraRig({ leaving, still, onArrive, inset = 0 }) {
  const { camera, pointer, size } = useThree();
  const look = useRef(new THREE.Vector3(0, CAMERA_LOOK_Y, BOARD.z));
  const inited = useRef(false);
  const flight = useRef(null);
  const tmp = useMemo(() => new THREE.Vector3(), []);

  useFrame((state, dt) => {
    const t = still ? 0 : state.clock.elapsedTime;
    if (leaving && !still) {
      if (!flight.current) {
        const p0 = camera.position.clone();
        flight.current = {
          u: 0,
          arrived: false,
          look0: look.current.clone(),
          // a straight, rising dolly onto the board's axis: it closes the sideways sway, climbs gently over the traffic
          // (never below y 4 above the lanes) and ends square in front of the face, close enough that the face fills the
          // frame when the fade starts - no swing along the street, the board stays centred the whole way
          curve: new THREE.CatmullRomCurve3([
            p0,
            new THREE.Vector3(p0.x * 0.5, 4.2, 13),
            new THREE.Vector3(p0.x * 0.1, 5.8, 5),
            new THREE.Vector3(0, BOARD.y, BOARD_FACE_Z + FLIGHT_END_DIST),
          ], false, "centripetal"),
        };
      }
      const f = flight.current;
      // progress advances by the frame time, capped at 1/30 s: a dropped frame slows the flight a touch instead of jumping it
      f.u = Math.min(1, f.u + Math.min(dt, 1 / 30) / FLIGHT_S);
      const u = f.u;
      // the picture grows from "above the controls" to the whole screen over the first half of the flight
      frameProjection(camera, size.width, size.height, inset * (1 - smooth(u / 0.5)));
      camera.position.copy(f.curve.getPointAt(easeInOutCubic(u)));
      // the aim settles on the face centre early (it starts only a little above it) and then holds there
      look.current.lerpVectors(f.look0, tmp.set(0, BOARD.y, BOARD_FACE_Z), smooth(u / 0.45));
      camera.lookAt(look.current);
      if (u >= ARRIVE_AT && !f.arrived) {
        f.arrived = true;
        onArrive?.();
      }
      return;
    }
    const visibleH = frameProjection(camera, size.width, size.height, inset);
    const d = baseDistance(size.width / Math.max(1, visibleH), BASE_FOV);
    const lift = (d - 24) * 0.05; // narrow (portrait) screens stand further back: rise a touch so the road stays in view
    const px = still ? 0 : pointer.x;
    const py = still ? 0 : pointer.y;
    // one slow sine per axis, fixed distance: a smooth horizontal glide instead of three out-of-phase wobbles
    const target = tmp.set(
      Math.sin(t * 0.2) * SWAY_X + px * 0.8,
      CAMERA_EYE_Y + lift + Math.cos(t * 0.15) * SWAY_Y + py * 0.2,
      BOARD.z + d,
    );
    if (!inited.current) {
      camera.position.copy(target);
      inited.current = true;
    } else {
      camera.position.lerp(target, 1 - Math.exp(-dt * 2));
    }
    look.current.set(camera.position.x * 0.15, CAMERA_LOOK_Y + lift * 0.5, BOARD.z); // follows the glide a little: no swivel
    camera.lookAt(look.current);
  });
  return null;
}

// ---------------------------------------------------------------- scene

const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));

/**
 * Runs once, inside <City> - i.e. only after the logo has loaded and every mesh of the city exists - and calls `onReady` when
 * the scene can be shown without a hitch or a pop:
 *   1. one frame, so everything mounted alongside (the Environment's reflection map, the effect passes) is in the scene
 *      before any shader is built - a program compiled without the env map would be rebuilt the moment it arrived;
 *   2. every texture uploaded to the GPU up front (initTexture), instead of mid-frame when a mesh first draws with it;
 *   3. every shader program compiled - compileAsync uses KHR_parallel_shader_compile where the driver has it, so the page
 *      stays responsive meanwhile (plain compile() on older three/drivers);
 *   4. two rendered frames (the effect composer builds its own passes on its first render), still invisible behind the
 *      canvas's opacity 0.
 * Guarded so React's StrictMode double effect doesn't run it twice.
 */
function WarmUp({ textures, onReady }) {
  const { gl, scene, camera } = useThree();
  const started = useRef(false);
  const alive = useRef(true);
  const ready = useRef(onReady);
  ready.current = onReady;
  // mounted-ness tracked on its own: StrictMode's simulated unmount/remount must not cancel the one warm-up that runs
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      await nextFrame();
      for (const t of textures) if (t) gl.initTexture(t);
      try {
        if (gl.compileAsync) await gl.compileAsync(scene, camera);
        else gl.compile(scene, camera);
      } catch {
        /* a compile failure here is not fatal: the first real frame compiles whatever is left */
      }
      await nextFrame();
      await nextFrame();
      if (alive.current) ready.current?.();
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

function City({ leaving, still, onArrive, onReady, inset }) {
  const logo = useLogo();
  const { glass, glassLit, ramp, halo, beam } = useTextures();
  // the city itself only depends on its textures and `still`: built once, so a change of `leaving` / `inset` re-renders
  // the camera rig alone instead of reconciling every mesh of the city
  const city = useMemo(() => (
    <>
      <WarmUp textures={[logo, glass, glassLit, ramp, halo, beam]} onReady={onReady} />
      <Skyline glass={glass} glassLit={glassLit} logo={logo} halo={halo} />
      <Storefronts logo={logo} />
      <Street halo={halo} />
      <MainBillboard logo={logo} />
      <Metro />
      <Train dir={1} z={-1.1} phase={0} still={still} />
      <Train dir={-1} z={1.1} phase={TRAIN_CYCLE / 2} still={still} />
      <Traffic ramp={ramp} beam={beam} still={still} />
    </>
  ), [logo, glass, glassLit, ramp, halo, beam, still]); // eslint-disable-line react-hooks/exhaustive-deps -- WarmUp reads onReady once, through a ref
  return (
    <>
      {city}
      <CameraRig leaving={leaving} still={still} onArrive={onArrive} inset={inset} />
    </>
  );
}

/**
 * Everything that never changes after mount: background, fog, lights, the reflection Environment and the stars. Memoised
 * (its only prop, `still`, is fixed for the screen's lifetime) because drei's <Environment> re-renders its cube camera - six
 * extra renders, a visible hitch - every time its `children` prop is a new element, i.e. on every parent re-render.
 */
const SceneBase = memo(function SceneBase({ still }) {
  return (
    <>
      <color attach="background" args={[BG]} />
      <fog attach="fog" args={[BG, 34, 110]} />
      {/* a fixed lighting baseline: nothing in the scene can fall to pitch black, whatever the camera's position in its loop
          or the flight - the key/fill directionals are static (never animated) */}
      <ambientLight intensity={0.8} color="#fff4e6" />
      <hemisphereLight args={["#2a2a38", BG, 0.35]} />
      <directionalLight position={[-6, 14, 10]} intensity={0.6} color="#ffe9d2" />
      <directionalLight position={[8, 10, 12]} intensity={0.3} color="#dfe6ff" />
      <pointLight position={[0, 9, -4]} intensity={30} distance={30} color={RED} />
      {/* reflections for the chrome, the car paint, the train shell and the wet road: white, warm and red strips */}
      <Environment resolution={256} frames={1}>
        <Lightformer form="rect" intensity={2.6} position={[0, 6, 4]} scale={[14, 2, 1]} color="#ffffff" />
        <Lightformer form="rect" intensity={1.4} position={[-7, 1.5, 1]} scale={[2, 8, 1]} color="#ffe2c0" />
        <Lightformer form="rect" intensity={1.6} position={[7, 0.5, -2]} scale={[2, 7, 1]} color={RED} />
        <Lightformer form="rect" intensity={0.8} position={[0, -2, -6]} scale={[18, 1, 1]} color="#8fa3c8" />
        {/* the night-sky glow on the camera's side, 5-35 deg up: what the towers' street-facing glass mirrors when seen from
            the low camera (their reflections point back up towards the viewer). Only visible in reflections - it gives the
            glass a cool gradient sheen instead of reflecting black, and a soft highlight on the cars' flanks */}
        <Lightformer form="rect" intensity={0.9} position={[0, 3.5, 10]} rotation-y={Math.PI} scale={[44, 8, 1]} color="#51689a" />
      </Environment>
      <Stars radius={90} depth={40} count={still ? 300 : 700} factor={3} saturation={0} fade speed={still ? 0 : 0.2} />
    </>
  );
});

/**
 * The post-processing chain, memoised with no props: @react-three/postprocessing's EffectComposer rebuilds its EffectPass
 * (new fullscreen material, shader recompile) whenever its `children` identity changes - which, unmemoised, was every
 * re-render of the scene (inset, leaving, the splash's own state) and showed as a blink / dropped frame.
 */
const PostFX = memo(function PostFX() {
  return (
    <EffectComposer multisampling={2}>
      <Bloom mipmapBlur luminanceThreshold={BLOOM_THRESHOLD} luminanceSmoothing={0.2} intensity={0.9} radius={0.72} />
      <Vignette offset={0.3} darkness={0.6} />
      <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
    </EffectComposer>
  );
});

export default function CityScene({ leaving, still, onArrive, onReady, inset = 0 }) {
  return (
    <>
      <SceneBase still={still} />
      <Suspense fallback={null}>
        <City leaving={leaving} still={still} onArrive={onArrive} onReady={onReady} inset={inset} />
      </Suspense>
      <PostFX />
    </>
  );
}
