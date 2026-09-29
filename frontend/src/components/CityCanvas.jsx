import { memo } from "react";
import { Canvas } from "@react-three/fiber";
import CityScene from "./CityScene.jsx";

// hoisted: the same objects on every render, so R3F's per-render configure() never sees "new" camera/gl/dpr options
const DPR = [1, 1.5];
const CAMERA = { position: [0, 2.2, 22], fov: 42, near: 0.1, far: 220 };
// no stencil buffer (nothing uses one); the effect composer draws to its own targets
const GL = { antialias: false, stencil: false, powerPreference: "high-performance" };

/**
 * The launch screen's WebGL canvas: everything three.js / react-three-fiber lives behind this module so SplashScreen can
 * lazy-load it and show its own title and button before the ~1 MB 3D bundle has arrived.
 *
 * `onReady` fires from inside the city (CityScene's WarmUp) once the logo has loaded, every texture is on the GPU, every
 * shader is compiled and two frames have been drawn - the caller keeps the canvas at opacity 0 until then, so nothing pops
 * in piecemeal and the compile hitch happens out of sight.
 *
 * Memoised: the splash re-renders on every state change (health check, preloader, button states); with stable props
 * (SplashScreen passes useCallback'd handlers) none of that reaches the WebGL tree - the canvas is never re-rendered, let
 * alone re-mounted, except when `leaving`, `still` or `inset` really change.
 */
function CityCanvas({ leaving, still, onArrive, onReady, inset }) {
  return (
    <Canvas
      className="sp3-canvas"
      dpr={DPR}
      camera={CAMERA}
      gl={GL}
    >
      <CityScene leaving={leaving} still={still} onArrive={onArrive} onReady={onReady} inset={inset} />
    </Canvas>
  );
}

export default memo(CityCanvas);
