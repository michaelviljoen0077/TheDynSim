import { useCallback, useRef } from 'react';
import { useFrame, type ThreeEvent } from '@react-three/fiber';
import * as THREE from 'three';
import { heightAtFace, live } from '../net/frames';
import { useStore, type SpeciesInfo } from '../state/store';
import { surfacePoint } from './cubeGeometry';
import { cubeRadius } from './CubeTerrain';

// Cube entities: one InstancedMesh per species (as the flat renderer), but each
// instance is placed by mapping (face, x, y) through the face basis + an outward
// height/stratum offset (cubeGeometry.surfacePoint). prev->curr interpolation is
// kept, except a frame where an entity's face changed snaps (no cross-face lerp).

const CAPACITY = 20000;
const tmpMatrix = new THREE.Matrix4();
const _pos = new THREE.Vector3();
const _nrm = new THREE.Vector3();
const counters = new Map<number, number>();

function SpeciesMesh({
  info,
  meshes,
  onPick,
}: {
  info: SpeciesInfo;
  meshes: Map<number, THREE.InstancedMesh>;
  onPick: (speciesId: number, instanceId: number) => void;
}) {
  const ref = useCallback(
    (m: THREE.InstancedMesh | null) => {
      if (m) {
        m.count = 0;
        m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        meshes.set(info.id, m);
      } else {
        meshes.delete(info.id);
      }
    },
    [info.id, meshes],
  );
  const radius = info.size > 0 ? info.size : 0.5;
  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    if (e.instanceId === undefined) return;
    e.stopPropagation();
    onPick(info.id, e.instanceId);
  };
  return (
    <instancedMesh
      ref={ref}
      args={[undefined, undefined, CAPACITY]}
      frustumCulled={false}
      onClick={handleClick}
    >
      <sphereGeometry args={[radius, 10, 8]} />
      <meshStandardMaterial color={info.color} roughness={0.55} metalness={0.05} />
    </instancedMesh>
  );
}

export function CubeEntities() {
  const species = useStore((s) => s.sync?.species);
  const meshes = useRef(new Map<number, THREE.InstancedMesh>());
  const idBuffers = useRef(new Map<number, Uint32Array>());

  const onPick = useCallback((speciesId: number, instanceId: number) => {
    const ids = idBuffers.current.get(speciesId);
    if (ids && instanceId < ids.length) {
      useStore.getState().selectEntity(ids[instanceId]);
    }
  }, []);

  useFrame(() => {
    const map = meshes.current;
    const curr = live.curr;
    if (!curr) {
      map.forEach((m) => (m.count = 0));
      return;
    }
    const { hiddenSpecies, strata, sync } = useStore.getState();
    const heightScale = sync?.heightScale ?? 24;
    const S = sync?.size ?? 64;
    const radius = cubeRadius(S);
    const spherify = live.spherify;
    const prev = live.prev;
    const alpha = prev
      ? Math.min(1, (performance.now() - curr.recvTime) / live.frameInterval)
      : 1;

    counters.clear();
    for (let i = 0; i < curr.n; i++) {
      const spId = curr.species[i];
      const mesh = map.get(spId);
      if (!mesh || hiddenSpecies[spId]) continue;
      const stratum = curr.stratum[i];
      if (!strata[stratum]) continue;
      const face = curr.face[i];

      let x = curr.x[i];
      let y = curr.y[i];
      let z = curr.z[i];
      const j = curr.prevIndex[i];
      // Interpolate only within a face; a face change teleports (like an epoch).
      if (prev && j >= 0 && prev.face[j] === face) {
        x = prev.x[j] + (x - prev.x[j]) * alpha;
        y = prev.y[j] + (y - prev.y[j]) * alpha;
        z = prev.z[j] + (z - prev.z[j]) * alpha;
      }
      const ground = heightAtFace(face, x, y) * heightScale;
      // Stratum bands along the outward normal: surface hugs ground, sky floats
      // out, underground sits in a clear inner shell (well below the crust) so
      // burrowers are visible through the x-ray terrain.
      const outward =
        stratum === 1 ? ground + 0.4 : stratum === 2 ? ground + 6 + z : -8;

      const k = counters.get(spId) ?? 0;
      if (k >= CAPACITY) continue;
      surfacePoint(face, (x + 0.5) / S, (y + 0.5) / S, spherify, radius, outward, _pos, _nrm);
      tmpMatrix.makeTranslation(_pos.x, _pos.y, _pos.z);
      mesh.setMatrixAt(k, tmpMatrix);
      let ids = idBuffers.current.get(spId);
      if (!ids) {
        ids = new Uint32Array(CAPACITY);
        idBuffers.current.set(spId, ids);
      }
      ids[k] = curr.id[i];
      counters.set(spId, k + 1);
    }
    map.forEach((m, id) => {
      m.count = counters.get(id) ?? 0;
      m.instanceMatrix.needsUpdate = true;
      // Keep the raycast broad-phase sphere covering the whole globe. Three caches
      // InstancedMesh.boundingSphere once; our instances move every frame, so the
      // stale cached sphere would reject click rays before testing instances —
      // that's why picking a creature silently did nothing. Refresh it each frame.
      if (!m.boundingSphere) m.boundingSphere = new THREE.Sphere();
      m.boundingSphere.center.set(0, 0, 0);
      m.boundingSphere.radius = radius * 2;
    });
  });

  if (!species) return null;
  return (
    <group>
      {species.map((sp) => (
        <SpeciesMesh key={sp.id} info={sp} meshes={meshes.current} onPick={onPick} />
      ))}
    </group>
  );
}
