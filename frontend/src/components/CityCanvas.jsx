import { Canvas } from "@react-three/fiber";
import CityScene from "./CityScene.jsx";

/**
 * The launch screen's WebGL canvas: everything three.js / react-three-fiber lives behind this module so SplashScreen can
 * lazy-load it and show its own title and button before the ~1 MB 3D bundle has arrived.
 *
 * `onReady` fires from inside the city (CityScene's WarmUp) once the logo has loaded, every texture is on the GPU, every
 * shader is compiled and two frames have been drawn - the caller keeps the canvas at opacity 0 until then, so nothing pops
 * in piecemeal and the compile hitch happens out of sight.
 */
export default function CityCanvas({ leaving, still, onArrive, onReady }) {
  return (
    <Canvas
      className="sp3-canvas"
      dpr={[1, 1.5]}
      camera={{ position: [0, 2.2, 22], fov: 42, near: 0.1, far: 220 }}
      // no stencil buffer (nothing uses one); the effect composer draws to its own targets
      gl={{ antialias: false, stencil: false, powerPreference: "high-performance" }}
    >
      <CityScene leaving={leaving} still={still} onArrive={onArrive} onReady={onReady} />
    </Canvas>
  );
}
