import { useEffect, useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { live, type TerrainData } from '../net/frames';
import { useStore } from '../state/store';

const C_WATERBED = new THREE.Color('#155084');
const C_DEEP = new THREE.Color('#092b4d');
const C_SAND = new THREE.Color('#c8b47e');
const C_GRASS_DRY = new THREE.Color('#8a7f4a');
const C_GRASS = new THREE.Color('#3f8f4d');
const C_ROCK = new THREE.Color('#7d7f84');
const C_PEAK = new THREE.Color('#b8bcc4');
const tmpA = new THREE.Color();
const tmpB = new THREE.Color();

function smoothstep(x: number, a: number, b: number): number {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

function paintColors(
  colors: THREE.BufferAttribute,
  t: TerrainData,
  seaLevel: number,
  flora: Uint8Array | null,
): void {
  const S = t.size;
  const c = tmpA;
  for (let vy = 0; vy < S; vy++) {
    for (let vx = 0; vx < S; vx++) {
      // Plane vertices run row-major over vy; sim data is row-major [x][y].
      const v = vy * S + vx;
      const d = vx * S + vy;
      const h = t.height[d];
      if (t.water[d]) {
        c.copy(C_WATERBED).lerp(C_DEEP, smoothstep(seaLevel - h, 0, 0.25));
      } else {
        const f = flora ? flora[d] / 255 : 0.45;
        const grass = tmpB.copy(C_GRASS_DRY).lerp(C_GRASS, f);
        if (h < seaLevel + 0.03) {
          c.copy(C_SAND);
        } else if (h < 0.6) {
          c.copy(C_SAND).lerp(grass, smoothstep(h, seaLevel + 0.03, seaLevel + 0.12));
        } else if (h < 0.78) {
          c.copy(grass).lerp(C_ROCK, smoothstep(h, 0.6, 0.78));
        } else {
          c.copy(C_ROCK).lerp(C_PEAK, smoothstep(h, 0.78, 0.95));
        }
      }
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
  const matRef = useRef<THREE.MeshStandardMaterial>(null);
  const floraSeen = useRef(-1);

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
    paintColors(colors, t, seaLevel, live.flora);
    floraSeen.current = live.floraVersion;
    g.computeVertexNormals();
    return g;
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

  // Flora overlay: retint vertex colors whenever a kind-3 fieldId-0 frame lands.
  useFrame(() => {
    if (!geometry || !live.terrain || live.floraVersion === floraSeen.current) return;
    floraSeen.current = live.floraVersion;
    paintColors(
      geometry.attributes.color as THREE.BufferAttribute,
      live.terrain,
      seaLevel,
      live.flora,
    );
  });

  if (!geometry) return null;
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial ref={matRef} vertexColors roughness={0.95} metalness={0} />
    </mesh>
  );
}
