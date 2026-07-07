import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { useStore } from '../state/store';
import { Terrain } from './Terrain';
import { Water } from './Water';
import { Entities } from './Entities';
import { SunLight } from './SunLight';

export function WorldCanvas() {
  const size = useStore((s) => s.sync?.size);

  if (!size) {
    return <div className="world-waiting">awaiting world sync…</div>;
  }

  const c = (size - 1) / 2;
  return (
    <Canvas
      dpr={[1, 2]}
      camera={{
        position: [c + size * 0.65, size * 0.5, c + size * 0.85],
        fov: 50,
        near: 0.5,
        far: size * 10,
      }}
    >
      <SunLight />
      <Terrain />
      <Water />
      <Entities />
      <OrbitControls
        target={[c, 0, c]}
        maxPolarAngle={Math.PI * 0.495}
        minDistance={4}
        maxDistance={size * 3}
      />
    </Canvas>
  );
}
