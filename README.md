# Aether & Tact — 3D Spiral Gallery Editorial Terminal

A premium dark editorial landing page for a creative studio built on Vite 5, centered around a curved 3D spiral image helix hero. Featuring a fully bespoke Three.js rendering pipeline, custom GLSL shaders, Lenis smooth scrolling, and GSAP scroll-triggered typography reveals.

---

## 🚀 Features

* **3D Spiral Gallery:** A custom geometry helix of 75 curved image tiles loading 10 unique textures, tapering cone-like towards the top.
* **Vercel-Compatible Architecture:** Deferral of GPU tasks behind `requestIdleCallback` (with double `requestAnimationFrame` fallback) to secure optimal LCP score.
* **Smooth Inertial Scrolling:** Full-page scroll velocity driving camera tracking and gallery spin deceleration.
* **Custom GLSL Shader:** Subtly applies edge vignettes and camera distance depth desaturation per tile (without heavy post-processing libraries).
* **Responsive Layout:** Adaptive desktop mouse parallax and mobile viewport adjustments for full device compatibility.
* **Editorial Design System:** EB Garamond display serif, Manrope body sans-serif, and IBM Plex Mono metadata styling inside a responsive page stack.

---

## 🛠️ Installation & Setup

1. **Install Dependencies:**
   ```bash
   npm install
   ```

2. **Run Dev Server:**
   ```bash
   npm run dev
   ```

3. **Build Production Assets:**
   ```bash
   npm run build
   ```

4. **Preview Local Build:**
   ```bash
   npm run preview
   ```

---

## 📂 Project Structure

```
project-root/
├── index.html                # Semantic HTML layout
├── package.json              # NPM dependencies & scripts
├── vite.config.js            # Vite custom configuration
├── .gitignore                # System and lockfile filters
├── public/
│   └── images/
│       └── img1.jpg ... 10    # Editorial images dataset
└── src/
    ├── script.js             # Entry: Three.js setup + Lenis + GSAP reveals
    ├── shaders.js            # GLSL Vertex & Fragment custom shaders
    └── styles.css            # Dark editorial style definitions
```

---

## ⚙️ The CONFIG Explained

At the top of `src/script.js` lies the `CONFIG` object, which manages the physical layout and physics of the 3D gallery:

* **totalImages (10):** The number of unique local assets loaded.
* **tilesPerRevolution (15):** Number of curved tiles placed in a single 360-degree rotation of the helix.
* **revolutions (5):** Total number of spiral loops, dictating overall gallery height (`totalTiles = 15 * 5 = 75`).
* **startRadius (5):** Radius of the spiral helix at the bottom (`y` start).
* **endRadius (3.5):** Radius of the spiral helix at the top (`y` end), making the spiral taper into a cone.
* **tileHeightRatio (1.1):** Aspect ratio ratio multiplying tile chord width to resolve heights.
* **tileSegments (24):** Horizontal segmentation slices of the curved tile geometry.
* **spiralGap (0.35):** Vertical distance between adjacent tiles along the Y axis.
* **tileOverlap (0.005):** Tiny angular correction factor to close visual seams between adjacent tiles.
* **cameraZ (12):** Initial focal distance of the camera along the Z axis.
* **cameraSmoothing (0.075):** Lerp coefficient driving camera movements and mouse parallax dampening.
* **baseRotationSpeed (0.001):** Persistent idle rotation speed of the helix group.
* **scrollRotationMultiplier (0.0035):** Factor mapping scroll velocity into rotational spin impulse.
* **rotationDecay (0.9):** Friction dampening coefficient (rotational inertia decay).
* **scrollMultiplier (1.25):** Amplifies the momentum transfer of scrolling to the gallery rotation.
* **cameraYMultiplier (0.2):** Vertical camera movement scaling factor as scroll progresses.
* **parallaxStrength (0.1):** Desktop mouse tilt responsiveness scale.
* **spiralOffsetY (-2.0):** Center offset of the spiral group relative to the world coordinates.

---

## 🧩 How It Works

### 1. Geometry (Curved Tiles)
Rather than placing flat planes, a custom `BufferGeometry` is created for each tile (`createCurvedTileGeometry()`). The script walks 25 slices along an arc, offsetting vertices in the Z direction using:
$$x = \sin(\theta) \times \text{radius}$$
$$z = \cos(\theta) \times \text{radius}$$
This curves each photo slightly around the central column, creating a three-dimensional helix.

### 2. Scroll → Camera Y
As the user scrolls, the scroll progress $[0, 1]$ is scaled and updates `targetCameraY`. The camera follows with linear interpolation (LERP):
$$\text{Camera } y \leftarrow \text{Camera } y + (\text{Target } y - \text{Camera } y) \times 0.075$$
At the same time, the camera is constrained to continuously look at the center of the viewport.

### 3. Scroll Velocity → Spin
Every scroll update captures scroll velocity from Lenis. This is multiplied and added to `spinVelocity`. In the render tick, this spin is added to the rotation, and then decayed by `0.9` on every frame to simulate physical friction:
$$\theta_{\text{rotation}} \leftarrow \theta_{\text{rotation}} + \omega_{\text{base}} + \omega_{\text{spin}}$$
$$\omega_{\text{spin}} \leftarrow \omega_{\text{spin}} \times 0.90$$

### 4. Mouse Parallax
When moving the mouse on desktop viewports, normalized coordinates $[-1, 1]$ are generated. These coordinates update target tilt goals on the X and Z axes, which are then smoothed into the spiral group's rotation.

### 5. Image Replacement
To substitute showcase visuals, replace the files in `public/images/img1.jpg` through `img10.jpg` with your own images. They must maintain a 3:4 aspect ratio.

### 6. Tuning for Custom Looks
* **Wide Cone look:** Increase `startRadius` to `8` and decrease `endRadius` to `2`.
* **Tight Column look:** Set `startRadius` and `endRadius` both to `4`.
* **Deep Helix look:** Increase `spiralGap` to `0.6` and `revolutions` to `8`.
* **Hyper-active rotation:** Set `scrollRotationMultiplier` to `0.01` and `rotationDecay` to `0.95`.
