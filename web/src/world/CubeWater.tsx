import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { useStore } from '../state/store';
import { cubeRadius } from './CubeTerrain';

// A translucent ocean shell at sea-level radius around the cube/globe centre.
// Terrain above sea level pokes through; lowlands sit submerged. On a cube this
// reads as a rounded sea; on the spherified globe it becomes a clean ocean.
export function CubeWater() {
  const size = useStore((s) => s.sync?.size);
  const seaLevel = useStore((s) => s.sync?.seaLevel ?? 0.3);
  const heightScale = useStore((s) => s.sync?.heightScale ?? 24);
  const matRef = useRef<THREE.MeshStandardMaterial>(null);

  useFrame(({ clock }) => {
    const m = matRef.current;
    if (m) m.opacity = 0.45 + Math.sin(clock.elapsedTime * 0.9) * 0.06;
  });

  if (!size) return null;
  const r = cubeRadius(size) + seaLevel * heightScale;
  return (
    <mesh>
      <sphereGeometry args={[r, 64, 48]} />
      <meshStandardMaterial
        ref={matRef}
        color="#1c6fae"
        transparent
        opacity={0.45}
        roughness={0.2}
        metalness={0.15}
        depthWrite={false}
      />
    </mesh>
  );
}
