import { useCallback, useRef } from 'react';
import { useFrame, type ThreeEvent } from '@react-three/fiber';
import * as THREE from 'three';
import { heightAt, live } from '../net/frames';
import { useStore, type SpeciesInfo } from '../state/store';

const CAPACITY = 20000; // instances per species; draw count is set per frame
const tmpMatrix = new THREE.Matrix4();
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

export function Entities() {
  const species = useStore((s) => s.sync?.species);
  const meshes = useRef(new Map<number, THREE.InstancedMesh>());
  // Per-species map from packed instance slot -> generational entity id, so a
  // click's instanceId resolves back to the entity the inspector queries.
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
    const prev = live.prev;
    // Render between the last two frames: alpha in [0,1] over the frame interval.
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

      let x = curr.x[i];
      let y = curr.y[i];
      let z = curr.z[i];
      const j = curr.prevIndex[i];
      if (prev && j >= 0) {
        x = prev.x[j] + (x - prev.x[j]) * alpha;
        y = prev.y[j] + (y - prev.y[j]) * alpha;
        z = prev.z[j] + (z - prev.z[j]) * alpha;
      }
      const ground = heightAt(x, y) * heightScale;
      // Stratum band offsets: surface hugs the ground, sky floats in a band,
      // underground sits just below the crust (visible in x-ray mode).
      const worldY =
        stratum === 1 ? ground + 0.4 : stratum === 2 ? ground + 6 + z : ground - 2;

      const k = counters.get(spId) ?? 0;
      if (k >= CAPACITY) continue;
      tmpMatrix.makeTranslation(x, worldY, y);
      mesh.setMatrixAt(k, tmpMatrix);
      let ids = idBuffers.current.get(spId);
      if (!ids) {
        ids = new Uint32Array(CAPACITY);
        idBuffers.current.set(spId, ids);
      }
      ids[k] = curr.id[i];
      counters.set(spId, k + 1);
    }
    const S = sync?.size ?? 64;
    map.forEach((m, id) => {
      m.count = counters.get(id) ?? 0;
      m.instanceMatrix.needsUpdate = true;
      // refresh the raycast broad-phase sphere each frame (instances move, and
      // three caches it once) so clicking a creature actually registers
      if (!m.boundingSphere) m.boundingSphere = new THREE.Sphere();
      m.boundingSphere.center.set(S / 2, 0, S / 2);
      m.boundingSphere.radius = S * 1.5;
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
