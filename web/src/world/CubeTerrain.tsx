import { useEffect, useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { live, N_FACES, type TerrainData } from '../net/frames';
import { useStore, type Overlay } from '../state/store';
import { cellColor } from './terrainColor';
import { surfacePoint } from './cubeGeometry';

// Folded-cube terrain: one displaced PlaneGeometry per face. Each face vertex
// (gx, gy) is placed on the cube via the face basis (cubeGeometry.surfacePoint,
// a mirror of engine/cube.py to_3d) and pushed OUTWARD along the surface normal
// by height*heightScale. A spherify morph (0..1, animated) lerps every vertex
// from cube toward sphere. Colours reuse the flat renderer's cellColor.

const _pos = new THREE.Vector3();
const _nrm = new THREE.Vector3();
const _col = new THREE.Color();

interface FaceGeom {
  face: number;
  geom: THREE.BufferGeometry;
}

/** Radius (cube half-width) so a face spans `size` world units, matching flat. */
export function cubeRadius(size: number): number {
  return size / 2;
}

// Write positions + recomputed normals for one face at the given morph amount.
function writePositions(
  geom: THREE.BufferGeometry,
  face: number,
  t: TerrainData,
  heightScale: number,
  radius: number,
  spherify: number,
): void {
  const S = t.size;
  const span = S > 1 ? S - 1 : 1;
  const pos = geom.attributes.position as THREE.BufferAttribute;
  for (let gy = 0; gy < S; gy++) {
    for (let gx = 0; gx < S; gx++) {
      const v = gy * S + gx;
      const d = gx * S + gy;
      const outward = t.height[d] * heightScale;
      // vertices span the FULL face [0,1] (gx/(S-1)) so neighbouring faces meet
      // exactly at the shared edge — no half-cell gap. Height comes from cell gx,gy.
      surfacePoint(face, gx / span, gy / span, spherify, radius, outward, _pos, _nrm);
      pos.setXYZ(v, _pos.x, _pos.y, _pos.z);
    }
  }
  pos.needsUpdate = true;
  geom.computeVertexNormals();
}

function paintFace(
  geom: THREE.BufferGeometry,
  face: number,
  t: TerrainData,
  seaLevel: number,
  overlay: Overlay,
): void {
  const S = t.size;
  const flora = live.floras[face];
  const plankton = live.planktons[face];
  const colors = geom.attributes.color as THREE.BufferAttribute;
  for (let gy = 0; gy < S; gy++) {
    for (let gx = 0; gx < S; gx++) {
      const v = gy * S + gx;
      const d = gx * S + gy;
      cellColor(_col, t.height[d], t.water[d] !== 0, flora ? flora[d] / 255 : null,
        seaLevel, overlay, plankton ? plankton[d] / 255 : null);
      colors.setXYZ(v, _col.r, _col.g, _col.b);
    }
  }
  colors.needsUpdate = true;
}

export function CubeTerrain() {
  const terrainVersion = useStore((s) => s.terrainVersion);
  const heightScale = useStore((s) => s.sync?.heightScale ?? 24);
  const seaLevel = useStore((s) => s.sync?.seaLevel ?? 0.3);
  const size = useStore((s) => s.sync?.size ?? 64);
  const spherifyTarget = useStore((s) => s.spherify);
  const overlay = useStore((s) => s.overlay);

  const floraSeen = useRef(-1);
  const planktonSeen = useRef(-1);
  const overlaySeen = useRef<Overlay>('none');
  const builtSpherify = useRef(0);

  const radius = cubeRadius(size);

  const faces = useMemo(() => {
    if (terrainVersion === 0) return null;
    const out: FaceGeom[] = [];
    for (let face = 0; face < N_FACES; face++) {
      const t = live.terrains[face];
      if (!t) continue;
      const S = t.size;
      const g = new THREE.PlaneGeometry(1, 1, S - 1, S - 1); // topology only; positions overwritten
      g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(S * S * 3), 3));
      writePositions(g, face, t, heightScale, radius, live.spherify);
      paintFace(g, face, t, seaLevel, overlay);
      out.push({ face, geom: g });
    }
    builtSpherify.current = live.spherify;
    floraSeen.current = live.floraVersion;
    overlaySeen.current = overlay;
    return out.length ? out : null;
    // overlay excluded: overlay changes retint in useFrame, no full rebuild.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [terrainVersion, heightScale, seaLevel, radius]);

  useEffect(
    () => () => {
      faces?.forEach((f) => f.geom.dispose());
    },
    [faces],
  );

  useFrame((_, delta) => {
    if (!faces) return;
    // Animate the morph toward the checkbox target, framerate-independent.
    const target = spherifyTarget ? 1 : 0;
    if (Math.abs(live.spherify - target) > 1e-4) {
      live.spherify += (target - live.spherify) * Math.min(1, delta * 4);
      if (Math.abs(live.spherify - target) <= 1e-3) live.spherify = target;
    }
    // Rebuild positions only while the morph is actually moving.
    if (Math.abs(live.spherify - builtSpherify.current) > 5e-4) {
      for (const { face, geom } of faces) {
        const t = live.terrains[face];
        if (t) writePositions(geom, face, t, heightScale, radius, live.spherify);
      }
      builtSpherify.current = live.spherify;
    }
    // Retint on a new flora/plankton frame or an overlay change.
    if (live.floraVersion !== floraSeen.current
        || live.planktonVersion !== planktonSeen.current
        || overlay !== overlaySeen.current) {
      floraSeen.current = live.floraVersion;
      planktonSeen.current = live.planktonVersion;
      overlaySeen.current = overlay;
      for (const { face, geom } of faces) {
        const t = live.terrains[face];
        if (t) paintFace(geom, face, t, seaLevel, overlay);
      }
    }
  });

  // x-ray: when the underground layer is shown, make the crust see-through so
  // burrowers are visible inside the planet
  const xray = useStore((s) => s.strata[0]);

  if (!faces) return null;
  return (
    <group>
      {faces.map(({ face, geom }) => (
        <mesh key={face} geometry={geom}>
          <meshStandardMaterial
            vertexColors
            roughness={0.95}
            metalness={0}
            side={THREE.DoubleSide}
            transparent={xray}
            opacity={xray ? 0.35 : 1}
            depthWrite={!xray}
          />
        </mesh>
      ))}
    </group>
  );
}
