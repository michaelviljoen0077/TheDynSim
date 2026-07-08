import * as THREE from 'three';

// Six-face cube geometry — a direct mirror of engine/cube.py (FACES, to_3d,
// _cube_to_sphere) and docs/protocol.md. A face-local point (fu, fv) in [0,1]^2
// maps to the [-1,1]^3 cube via:  corner + fu*2*r + fv*2*u.
// For a sim cell/entity at grid (x, y) on an S-grid, fu = (x+0.5)/S, fv = (y+0.5)/S,
// exactly as the engine's to_3d samples, so terrain and entities line up.

export const N_FACES = 6;

export interface CubeFace {
  corner: THREE.Vector3;
  r: THREE.Vector3;
  u: THREE.Vector3;
  normal: THREE.Vector3; // outward unit normal = normalize(cross(r, u))
}

function mk(
  corner: [number, number, number],
  r: [number, number, number],
  u: [number, number, number],
): CubeFace {
  const vc = new THREE.Vector3(...corner);
  const vr = new THREE.Vector3(...r);
  const vu = new THREE.Vector3(...u);
  const normal = new THREE.Vector3().crossVectors(vr, vu).normalize();
  return { corner: vc, r: vr, u: vu, normal };
}

// Order and values match FACES in engine/cube.py / the protocol table.
export const FACES: CubeFace[] = [
  mk([-1, -1, 1], [1, 0, 0], [0, 1, 0]), // 0 front  (+Z)
  mk([1, -1, 1], [0, 0, -1], [0, 1, 0]), // 1 right  (+X)
  mk([1, -1, -1], [-1, 0, 0], [0, 1, 0]), // 2 back   (-Z)
  mk([-1, -1, -1], [0, 0, 1], [0, 1, 0]), // 3 left   (-X)
  mk([-1, 1, 1], [1, 0, 0], [0, 0, -1]), // 4 top    (+Y)
  mk([-1, -1, -1], [1, 0, 0], [0, 0, 1]), // 5 bottom (-Y)
];

/** Face-local normalized (fu, fv in [0,1]) -> point on the [-1,1]^3 cube. */
export function cubePoint(face: number, fu: number, fv: number, out: THREE.Vector3): THREE.Vector3 {
  const f = FACES[face];
  out.copy(f.corner);
  out.addScaledVector(f.r, fu * 2);
  out.addScaledVector(f.u, fv * 2);
  return out;
}

/** Map a point on the [-1,1]^3 cube surface onto the unit sphere (equal-ish area). */
export function cubeToSphere(c: THREE.Vector3, out: THREE.Vector3): THREE.Vector3 {
  const x = c.x;
  const y = c.y;
  const z = c.z;
  const x2 = x * x;
  const y2 = y * y;
  const z2 = z * z;
  out.set(
    x * Math.sqrt(1 - y2 / 2 - z2 / 2 + (y2 * z2) / 3),
    y * Math.sqrt(1 - z2 / 2 - x2 / 2 + (z2 * x2) / 3),
    z * Math.sqrt(1 - x2 / 2 - y2 / 2 + (x2 * y2) / 3),
  );
  return out;
}

const _cube = new THREE.Vector3();
const _sph = new THREE.Vector3();
const _sphNorm = new THREE.Vector3();

/**
 * Surface point for a face-local grid position, morphed cube<->sphere by
 * `spherify` (0 = folded cube, 1 = globe), then displaced outward by
 * `outward` world units along the (morphed) surface normal.
 * Writes both the position and the unit displacement normal.
 *
 * @param fu,fv normalized face coords in [0,1]
 * @param radius half-width of the cube in world units (face is 2*radius wide)
 */
export function surfacePoint(
  face: number,
  fu: number,
  fv: number,
  spherify: number,
  radius: number,
  outward: number,
  outPos: THREE.Vector3,
  outNormal: THREE.Vector3,
): void {
  cubePoint(face, fu, fv, _cube);
  if (spherify > 0) {
    cubeToSphere(_cube, _sph);
    _cube.lerp(_sph, spherify);
  }
  // Displace along the RADIAL direction (from the cube centre), not the face
  // normal. Adjacent faces share the same 3D point at a seam, hence the same
  // radial vector, so their edges move together and no gap opens as terrain
  // rises. It also inflates the cube gently toward a planet. _sphNorm reused as
  // scratch for the radial. (Shading normals are recomputed from the displaced
  // mesh, so this vector only sets the displacement direction.)
  _sphNorm.copy(_cube).normalize();
  outNormal.copy(_sphNorm);
  outPos.copy(_cube).multiplyScalar(radius).addScaledVector(_sphNorm, outward);
}
