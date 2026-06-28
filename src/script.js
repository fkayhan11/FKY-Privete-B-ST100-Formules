import * as THREE from 'three';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import Lenis from 'lenis';
import { vertexShader, fragmentShader } from './shaders.js';

// Verbatim CONFIG object
const CONFIG = {
  totalImages: 10,
  tilesPerRevolution: 15,
  revolutions: 5,
  startRadius: 5,
  endRadius: 3.5,
  tileHeightRatio: 1.1,
  tileSegments: 24,
  spiralGap: 0.35,
  tileOverlap: 0.005,
  cameraZ: 12,
  cameraSmoothing: 0.075,
  baseRotationSpeed: 0.001,
  scrollRotationMultiplier: 0.0035,
  rotationDecay: 0.9,
  scrollMultiplier: 1.25,
  cameraYMultiplier: 0.2,
  parallaxStrength: 0.1,
  spiralOffsetY: -2.0,
};

// State management
const state = {
  isMobile: window.innerWidth < 768,
  width: 0,
  height: 0,
  scrollProgress: 0,
  scrollVelocity: 0,
  spinVelocity: 0,
  targetCameraY: 0,
  currentCameraY: 0,
  mouseX: 0,
  mouseY: 0,
  targetTiltX: 0,
  targetTiltZ: 0,
  currentTiltX: 0,
  currentTiltZ: 0,
};

// ── LENIS SMOOTH SCROLL ───────────────────────────────────────────────────
const lenis = new Lenis({
  duration: 1.2,
  smoothWheel: true,
  smoothTouch: false,
  easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
});

// Update state on scroll
lenis.on('scroll', ({ scroll, limit, velocity }) => {
  state.scrollProgress = Math.min(scroll / Math.max(limit, 1), 1);
  state.scrollVelocity = velocity;
  state.spinVelocity += velocity * CONFIG.scrollRotationMultiplier * CONFIG.scrollMultiplier;
  
  // Sync GSAP ScrollTrigger
  ScrollTrigger.update();
});

// Lenis RAF loop
function raf(time) {
  lenis.raf(time);
  requestAnimationFrame(raf);
}
requestAnimationFrame(raf);

// ── GSAP SCROLL REVEALS ───────────────────────────────────────────────────
gsap.registerPlugin(ScrollTrigger);

// Connect ScrollTrigger to Lenis scroll updates
ScrollTrigger.scrollerProxy(document.body, {
  scrollTop(value) {
    return arguments.length ? lenis.scrollTo(value, { immediate: true }) : lenis.scroll;
  },
  getBoundingClientRect() {
    return { top: 0, left: 0, width: window.innerWidth, height: window.innerHeight };
  },
});

const ctx = gsap.context(() => {
  gsap.utils.toArray('.reveal-text').forEach((el) => {
    gsap.fromTo(el,
      { opacity: 0, y: 50 },
      {
        opacity: 1,
        y: 0,
        duration: 1.4,
        ease: 'power3.out',
        scrollTrigger: {
          trigger: el,
          start: 'top 80%',
          toggleActions: 'play none none none',
          once: true,
        },
      }
    );
  });
});

if (import.meta.hot) {
  import.meta.hot.dispose(() => ctx.revert());
}

window.addEventListener('load', () => ScrollTrigger.refresh());

// ── THREE.JS ENGINE ───────────────────────────────────────────────────────
const heroEl = document.querySelector('.hero');
state.width = heroEl.clientWidth;
state.height = heroEl.clientHeight;

let scene, camera, renderer, spiralGroup;
let tiles = [];

// Curved BufferGeometry builder
function createCurvedTileGeometry(radius, arcAngle, tileHeight, segments) {
  const geometry = new THREE.BufferGeometry();
  const vertices = [];
  const uvs = [];
  const indices = [];

  for (let j = 0; j <= segments; j++) {
    const t = j / segments;
    const theta = (t - 0.5) * arcAngle; // centered around Z-axis

    const x = Math.sin(theta) * radius;
    const z = Math.cos(theta) * radius;

    // Top and Bottom vertices along the curved arc slice
    vertices.push(x, tileHeight / 2, z);
    vertices.push(x, -tileHeight / 2, z);

    // UV coordinates mapped to geometry boundaries
    uvs.push(t, 1);
    uvs.push(t, 0);
  }

  // Generate triangles stitching adjacent slices
  for (let j = 0; j < segments; j++) {
    const idx1 = j * 2;
    const idx2 = j * 2 + 1;
    const idx3 = (j + 1) * 2;
    const idx4 = (j + 1) * 2 + 1;

    indices.push(idx1, idx2, idx3);
    indices.push(idx2, idx4, idx3);
  }

  geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();

  return geometry;
}

// Promise wrapper for loading textures with custom fallback
function loadTextures(rendererInstance) {
  const loader = new THREE.TextureLoader();
  const promises = [];

  for (let i = 1; i <= CONFIG.totalImages; i++) {
    const path = `/images/img${i}.jpg`;
    promises.push(
      new Promise((resolve) => {
        loader.load(
          path,
          (texture) => {
            texture.colorSpace = THREE.SRGBColorSpace;
            texture.anisotropy = rendererInstance.capabilities.getMaxAnisotropy();
            texture.minFilter = THREE.LinearMipmapLinearFilter;
            texture.magFilter = THREE.LinearFilter;
            resolve(texture);
          },
          undefined,
          () => {
            // Fallback to a dark grey 1x1 DataTexture on error
            const color = new Uint8Array([22, 22, 24, 255]);
            const fallback = new THREE.DataTexture(color, 1, 1, THREE.RGBAFormat);
            fallback.needsUpdate = true;
            resolve(fallback);
          }
        );
      })
    );
  }

  return Promise.all(promises);
}

// Deferred WebGL Initialization using requestIdleCallback
const initWebGL = () => {
  // Scene Setup
  scene = new THREE.Scene();

  // Camera Setup
  camera = new THREE.PerspectiveCamera(45, state.width / state.height, 0.1, 100);
  camera.position.set(0, 0, CONFIG.cameraZ + (state.isMobile ? 3 : 0));

  // Renderer Setup
  renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: true,
    powerPreference: 'high-performance',
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(state.width, state.height);
  renderer.setClearColor(0x000000, 0);

  const canvas = renderer.domElement;
  canvas.classList.add('hero__canvas');
  heroEl.appendChild(canvas);

  // Group container
  spiralGroup = new THREE.Group();
  spiralGroup.position.y = CONFIG.spiralOffsetY;
  scene.add(spiralGroup);

  // Load resources and assemble tiles
  loadTextures(renderer).then((loadedTextures) => {
    const totalTiles = CONFIG.tilesPerRevolution * CONFIG.revolutions;
    const angleStep = (Math.PI * 2) / CONFIG.tilesPerRevolution;
    const arcAngle = angleStep + CONFIG.tileOverlap;
    const chord = 2 * CONFIG.startRadius * Math.sin(angleStep / 2);
    const tileHeight = chord * CONFIG.tileHeightRatio;

    const startY = ((totalTiles - 1) * CONFIG.spiralGap) / 2;

    for (let i = 0; i < totalTiles; i++) {
      const fraction = i / (totalTiles - 1);
      const radius = CONFIG.startRadius + fraction * (CONFIG.endRadius - CONFIG.startRadius);

      // Select texture
      const texture = loadedTextures[i % CONFIG.totalImages];

      // Mesh & Geometry
      const geometry = createCurvedTileGeometry(radius, arcAngle, tileHeight, CONFIG.tileSegments);
      const material = new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        uniforms: {
          uMap: { value: texture },
          uCameraPosition: { value: camera.position },
        },
        side: THREE.DoubleSide,
        transparent: true,
      });

      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.y = startY - i * CONFIG.spiralGap;
      mesh.rotation.y = i * angleStep;

      spiralGroup.add(mesh);
      tiles.push(mesh);
    }

    // Trigger canvas load reveal transition
    canvas.classList.add('is-loaded');

    // Start rendering loops
    tick();
  });
};

// Render Loop
const tick = () => {
  // Rotate spiral
  spiralGroup.rotation.y += CONFIG.baseRotationSpeed + state.spinVelocity;
  state.spinVelocity *= CONFIG.rotationDecay;

  // Apply tilt parallax on desktop only
  if (!state.isMobile) {
    state.currentTiltX += (state.targetTiltX - state.currentTiltX) * CONFIG.cameraSmoothing;
    state.currentTiltZ += (state.targetTiltZ - state.currentTiltZ) * CONFIG.cameraSmoothing;
    spiralGroup.rotation.x = state.currentTiltX;
    spiralGroup.rotation.z = state.currentTiltZ;
  }

  // Vertical camera movement from scroll
  state.targetCameraY = -state.scrollProgress * CONFIG.cameraYMultiplier * 10;
  state.currentCameraY += (state.targetCameraY - state.currentCameraY) * CONFIG.cameraSmoothing;
  camera.position.y = state.currentCameraY;
  camera.lookAt(0, state.currentCameraY * 0.4, 0);

  // Render Scene
  renderer.render(scene, camera);

  requestAnimationFrame(tick);
};

// Defer script initialization behind requestIdleCallback
if ('requestIdleCallback' in window) {
  window.requestIdleCallback(() => initWebGL(), { timeout: 1500 });
} else {
  // Fallback to double requestAnimationFrame
  requestAnimationFrame(() => {
    requestAnimationFrame(() => initWebGL());
  });
}

// ── EVENTS & LISTENERS ────────────────────────────────────────────────────
window.addEventListener('mousemove', (e) => {
  if (state.isMobile) return;
  state.mouseX = (e.clientX / window.innerWidth) * 2 - 1;
  state.mouseY = (e.clientY / window.innerHeight) * 2 - 1;
  state.targetTiltX = state.mouseY * CONFIG.parallaxStrength;
  state.targetTiltZ = state.mouseX * CONFIG.parallaxStrength * -0.5;
});

window.addEventListener('resize', () => {
  state.isMobile = window.innerWidth < 768;
  state.width = heroEl.clientWidth;
  state.height = heroEl.clientHeight;

  if (camera && renderer) {
    camera.aspect = state.width / state.height;
    camera.updateProjectionMatrix();
    camera.position.z = CONFIG.cameraZ + (state.isMobile ? 3 : 0);

    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(state.width, state.height);
  }

  if (state.isMobile) {
    state.targetTiltX = 0;
    state.targetTiltZ = 0;
    if (spiralGroup) {
      spiralGroup.rotation.x = 0;
      spiralGroup.rotation.z = 0;
    }
  }
});
