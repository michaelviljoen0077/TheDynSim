import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { useStore } from '../state/store';

export function Water() {
  const size = useStore((s) => s.sync?.size);
  const seaLevel = useStore((s) => s.sync?.seaLevel ?? 0.3);
  const heightScale = useStore((s) => s.sync?.heightScale ?? 24);
  const matRef = useRef<THREE.MeshStandardMaterial>(null);

  useFrame(({ clock }) => {
    const m = matRef.current;
    if (m) m.opacity = 0.6 + Math.sin(clock.elapsedTime * 0.9) * 0.07;
  });

  if (!size) return null;
  const c = (size - 1) / 2;
  return (
    <mesh position={[c, seaLevel * heightScale, c]} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[size - 1, size - 1]} />
      <meshStandardMaterial
        ref={matRef}
        color="#1c6fae"
        transparent
        opacity={0.6}
        roughness={0.2}
        metalness={0.15}
        depthWrite={false}
      />
    </mesh>
  );
}
