import { useEffect, useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { live, type TerrainData } from '../net/frames';
import { useStore, type Overlay } from '../state/store';
import { cellColor } from './terrainColor';

const tmpA = new THREE.Color();

function paintColors(
  colors: THREE.BufferAttribute,
  t: TerrainData,
  seaLevel: number,
  flora: Uint8Array | null,
  overlay: Overlay,
): void {
  const S = t.size;
  const c = tmpA;
  for (let vy = 0; vy < S; vy++) {
    for (let vx = 0; vx < S; vx++) {
      // Plane vertices run row-major over vy; sim data is row-major [x][y].
      const v = vy * S + vx;
      const d = vx * S + vy;
      cellColor(c, t.height[d], t.water[d] !== 0, flora ? flora[d] / 255 : null, seaLevel, overlay);
      colors.setXYZ(v, c.r, c.g, c.b);
    }
  }
  colors.needsUpdate = true;
}

export function Terrain() {
  const terrainVersion = useStore((s) => s.terrainVersion);
  const heightScale = useStore((s) => s.sync?.heightScale ?? 24);
  const seaLevel = useStore((s) => s.sync?.seaLevel ?? 0.3);
  const xray = useStore((s) => s.strata[0]);
  const overlay = useStore((s) => s.overlay);
  const matRef = useRef<THREE.MeshStandardMaterial>(null);
  const floraSeen = useRef(-1);
  const overlaySeen = useRef<Overlay>('none');

  const geometry = useMemo(() => {
    const t = live.terrain;
    if (!t || terrainVersion === 0) return null;
    const S = t.size;
    const g = new THREE.PlaneGeometry(S - 1, S - 1, S - 1, S - 1);
    const pos = g.attributes.position as THREE.BufferAttribute;
    // Rebuild positions so vertex (vx, vy) sits at world (x=vx, z=vy) with
    // height[vx*S + vy] * heightScale as elevation.
    for (let vy = 0; vy < S; vy++) {
      for (let vx = 0; vx < S; vx++) {
        pos.setXYZ(vy * S + vx, vx, t.height[vx * S + vy] * heightScale, vy);
      }
    }
    const colors = new THREE.BufferAttribute(new Float32Array(S * S * 3), 3);
    g.setAttribute('color', colors);
    paintColors(colors, t, seaLevel, live.flora, overlay);
    floraSeen.current = live.floraVersion;
    overlaySeen.current = overlay;
    g.computeVertexNormals();
    return g;
    // overlay intentionally excluded: overlay changes retint in useFrame, no rebuild.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [terrainVersion, heightScale, seaLevel]);

  useEffect(() => () => geometry?.dispose(), [geometry]);

  // Underground x-ray: see through the crust to stratum-0 entities.
  useEffect(() => {
    const m = matRef.current;
    if (!m) return;
    m.transparent = xray;
    m.opacity = xray ? 0.35 : 1;
    m.needsUpdate = true;
  }, [xray, geometry]);

  // Retint whenever a new flora frame lands or the overlay mode changes.
  useFrame(() => {
    if (!geometry || !live.terrain) return;
    if (live.floraVersion === floraSeen.current && overlay === overlaySeen.current) return;
    floraSeen.current = live.floraVersion;
    overlaySeen.current = overlay;
    paintColors(
      geometry.attributes.color as THREE.BufferAttribute,
      live.terrain,
      seaLevel,
      live.flora,
      overlay,
    );
  });

  if (!geometry) return null;
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial ref={matRef} vertexColors roughness={0.95} metalness={0} />
    </mesh>
  );
}
