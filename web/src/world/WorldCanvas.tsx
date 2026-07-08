import { useRef } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import { useStore } from '../state/store';
import { Terrain } from './Terrain';
import { Water } from './Water';
import { Entities } from './Entities';
import { SunLight } from './SunLight';
import { CubeTerrain, cubeRadius } from './CubeTerrain';
import { CubeEntities } from './CubeEntities';
import { CubeWater } from './CubeWater';

// Flat/wrap worlds render a single displaced plane; a cube world folds six
// faces around the origin. The topology from the sync message picks the path;
// the flat path is untouched from before the cube renderer existed.
function FlatScene({ size }: { size: number }) {
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

// Spins the planet slowly about its axis under the fixed side-sun, so the
// day/night terminator sweeps across the surface (one hemisphere lit, one dark).
function PlanetGroup({ children }: { children: React.ReactNode }) {
  const ref = useRef<THREE.Group>(null);
  useFrame((_, delta) => {
    if (ref.current) ref.current.rotation.y += delta * 0.1; // ~1 rotation / 63s
  });
  return <group ref={ref}>{children}</group>;
}

function CubeScene({ size }: { size: number }) {
  const R = cubeRadius(size);
  // Cube corners sit at ~1.73*R; frame the whole globe with margin.
  const dist = R * 3.4;
  return (
    <Canvas
      dpr={[1, 2]}
      camera={{
        position: [dist * 0.7, dist * 0.5, dist * 0.7],
        fov: 50,
        near: R * 0.05,
        far: R * 40,
      }}
    >
      <SunLight />
      <PlanetGroup>
        <CubeTerrain />
        <CubeWater />
        <CubeEntities />
      </PlanetGroup>
      <OrbitControls
        target={[0, 0, 0]}
        enablePan={false}
        autoRotate={false}
        minDistance={R * 1.4}
        maxDistance={R * 12}
      />
    </Canvas>
  );
}

export function WorldCanvas() {
  const size = useStore((s) => s.sync?.size);
  const topology = useStore((s) => s.sync?.topology);

  if (!size) {
    return <div className="world-waiting">awaiting world sync…</div>;
  }
  return topology === 'cube' ? <CubeScene size={size} /> : <FlatScene size={size} />;
}
