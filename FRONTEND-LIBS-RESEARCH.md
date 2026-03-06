# Frontend Libraries Research for Dashboard Widget System

> Research date: 2026-03-05
> Target: claude-manager canvas widget system (GridStack + `new Function('root','host', js)` execution model)

---

## Executive Summary

The claude-manager widget system runs vanilla JS inside `new Function()` contexts with access to a DOM container (`root`) and host element (`host`). Widgets can create canvas elements, SVGs, or any DOM — but currently use no external libraries. This research identifies **40+ libraries** across 14 categories that could dramatically improve visual quality.

### Top 7 Recommended Starter Kit

| Priority | Library | Category | Size (gzip) | Why |
|----------|---------|----------|-------------|-----|
| 1 | **Three.js** | 3D Graphics | ~180 KB | Unlocks WebGL 3D scenes, shader effects, particle galaxies |
| 2 | **GSAP** | Animation | ~25 KB | Industry-standard animation engine, works everywhere |
| 3 | **D3.js** (modular) | Data Viz | ~30 KB (subset) | World-class data visualization, import only what you need |
| 4 | **xterm.js** | Terminal | ~90 KB | Real terminal emulator in widgets — transforms terminal widget |
| 5 | **p5.js** (instance mode) | Creative Coding | ~300 KB | Instant generative art, perfect for void aesthetic |
| 6 | **Cytoscape.js** | Graph/Network | ~112 KB | Agent dependency graphs, task networks, constellation overlays |
| 7 | **Anime.js v4** | Animation | ~10 KB | Lightweight alternative to GSAP for simpler animations |

### Integration Strategy Summary

All recommended libraries support CDN loading via `<script>` tags. The widget system should adopt a **lazy-load CDN strategy**: widgets declare their dependencies, and the CanvasEngine loads them on demand before executing widget JS. Libraries are loaded once and cached globally — subsequent widgets reuse the same global objects.

---

## 1. 3D Graphics (WebGL/WebGPU)

### Three.js

- **URL**: https://threejs.org/
- **What it does**: Full-featured 3D engine — scenes, cameras, lights, materials, geometries, post-processing
- **Bundle size**: ~680 KB min, ~180 KB gzip
- **CDN**: `https://cdn.jsdelivr.net/npm/three@0.172/build/three.module.min.js` (ESM) or `https://cdnjs.cloudflare.com/ajax/libs/three.js/0.172.0/three.min.js` (UMD)
- **Integration difficulty**: Medium. Needs a `<canvas>` element inside `root`. Must manage WebGL context limits (browsers cap at ~8-16 active contexts).
- **Widget compatibility**: Renders into any container. **Critical gotcha**: multiple Three.js widgets will each consume a WebGL context. Use the scissor/viewport technique to share a single offscreen canvas across widgets, or limit to 1-2 3D widgets at a time.
- **Visual wow factor**: 5/5
- **Use cases**: 3D constellation maps, rotating planet dashboards, particle nebulae, 3D task dependency graphs, holographic agent avatars
- **Example snippet**:
```javascript
// Widget JS (bare function body — receives root, host)
const canvas = document.createElement('canvas');
canvas.style.cssText = 'width:100%;height:100%';
root.appendChild(canvas);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(75, root.offsetWidth / root.offsetHeight, 0.1, 1000);
const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
renderer.setSize(root.offsetWidth, root.offsetHeight);
renderer.setPixelRatio(window.devicePixelRatio);

// Starfield
const starGeo = new THREE.BufferGeometry();
const positions = new Float32Array(3000);
for (let i = 0; i < 3000; i++) positions[i] = (Math.random() - 0.5) * 600;
starGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
const starMat = new THREE.PointsMaterial({ color: 0x00ffff, size: 1.5, transparent: true, opacity: 0.8 });
scene.add(new THREE.Points(starGeo, starMat));

camera.position.z = 200;
(function animate() {
  requestAnimationFrame(animate);
  scene.rotation.y += 0.001;
  renderer.render(scene, camera);
})();
```

### WebGPU (Native API)

- **URL**: https://developer.mozilla.org/en-US/docs/Web/API/WebGPU_API
- **What it does**: Next-gen GPU API — compute shaders, advanced rendering, parallel data processing
- **Bundle size**: 0 KB (native browser API)
- **CDN**: N/A — built into Chrome 113+, Firefox 141+, Safari (macOS Tahoe+)
- **Integration difficulty**: Hard. Raw WGSL shader language, manual pipeline setup. No abstraction layer.
- **Widget compatibility**: Needs a `<canvas>` element. Context creation is similar to WebGL.
- **Visual wow factor**: 5/5 (when mastered)
- **Use cases**: GPU-accelerated particle simulations, real-time fluid dynamics, ML inference visualization, compute-heavy generative art
- **Example snippet**:
```javascript
const canvas = document.createElement('canvas');
canvas.style.cssText = 'width:100%;height:100%';
root.appendChild(canvas);

if (!navigator.gpu) { root.textContent = 'WebGPU not supported'; return; }
const adapter = await navigator.gpu.requestAdapter();
const device = await adapter.requestDevice();
const ctx = canvas.getContext('webgpu');
ctx.configure({ device, format: navigator.gpu.getPreferredCanvasFormat() });
// ... shader pipeline setup (verbose but incredibly powerful)
```

### gpu.js

- **URL**: https://github.com/gpujs/gpu.js
- **What it does**: Write JS functions that run on the GPU — automatic GLSL transpilation
- **Bundle size**: ~100 KB min
- **CDN**: `https://cdn.jsdelivr.net/npm/gpu.js@latest/dist/gpu-browser.min.js`
- **Integration difficulty**: Low. Write normal JS, it compiles to shaders automatically.
- **Widget compatibility**: Works standalone, renders to canvas or returns arrays.
- **Visual wow factor**: 3/5 (computation-focused, not directly visual)
- **Use cases**: Real-time data crunching, physics simulations feeding visualizations, fractal computation
- **Example snippet**:
```javascript
const gpu = new GPU({ canvas: document.createElement('canvas') });
const render = gpu.createKernel(function(time) {
  const x = this.thread.x / this.output.x;
  const y = this.thread.y / this.output.y;
  this.color(
    Math.sin(x * 10 + time) * 0.5 + 0.5,
    Math.sin(y * 10 + time * 0.7) * 0.5 + 0.5,
    Math.sin((x + y) * 5 + time * 1.3) * 0.5 + 0.5, 1
  );
}).setOutput([root.offsetWidth, root.offsetHeight]).setGraphical(true);

root.appendChild(render.canvas);
let t = 0;
(function loop() { requestAnimationFrame(loop); render(t += 0.02); })();
```

### GlslCanvas (Shadertoy-style)

- **URL**: https://github.com/nicoptere/glslCanvas (or patriciogonzalezvivo/glslCanvas)
- **What it does**: Run GLSL fragment shaders on a canvas element — Shadertoy compatible
- **Bundle size**: ~15 KB min
- **CDN**: `https://cdn.jsdelivr.net/npm/glslCanvas/dist/GlslCanvas.min.js`
- **Integration difficulty**: Very low. Create canvas, set shader code, done.
- **Widget compatibility**: Perfect. Renders to a `<canvas>` inside `root`.
- **Visual wow factor**: 5/5 (shader art is stunning)
- **Use cases**: Animated backgrounds, procedural textures, plasma effects, void-aesthetic generative patterns, audio-reactive visuals
- **Example snippet**:
```javascript
const canvas = document.createElement('canvas');
canvas.style.cssText = 'width:100%;height:100%';
canvas.width = root.offsetWidth; canvas.height = root.offsetHeight;
canvas.className = 'glslCanvas';
canvas.dataset.fragmentString = `
  precision mediump float;
  uniform float u_time;
  uniform vec2 u_resolution;
  void main() {
    vec2 uv = gl_FragCoord.xy / u_resolution;
    float d = length(uv - 0.5);
    vec3 col = vec3(0.0, sin(d * 20.0 - u_time * 3.0) * 0.5 + 0.5, 1.0);
    col *= smoothstep(0.5, 0.2, d);
    gl_FragColor = vec4(col, 1.0);
  }
`;
root.appendChild(canvas);
new GlslCanvas(canvas);
```

---

## 2. Advanced 2D Graphics

### PixiJS v8

- **URL**: https://pixijs.com/
- **What it does**: High-performance 2D renderer — WebGPU with WebGL fallback, sprites, filters, text
- **Bundle size**: ~200 KB gzip (full), much less with tree-shaking
- **CDN**: `https://cdn.jsdelivr.net/npm/pixi.js@8/dist/pixi.min.mjs`
- **Integration difficulty**: Medium. Needs async initialization (`await Application.init()`). Renders to canvas.
- **Widget compatibility**: Good. Creates its own canvas. Must be sized to container.
- **Visual wow factor**: 5/5
- **Use cases**: Smooth sprite-based agent avatars, GPU-accelerated particle systems, rich 2D game-like dashboards, animated infographics
- **Example snippet**:
```javascript
const app = new PIXI.Application();
await app.init({ resizeTo: root, backgroundAlpha: 0 });
root.appendChild(app.canvas);

// Particle starfield
for (let i = 0; i < 200; i++) {
  const star = new PIXI.Graphics().circle(0, 0, Math.random() * 2).fill(0x00ffff);
  star.x = Math.random() * root.offsetWidth;
  star.y = Math.random() * root.offsetHeight;
  star.alpha = Math.random();
  app.stage.addChild(star);
}
app.ticker.add((dt) => {
  app.stage.children.forEach(s => {
    s.alpha = 0.3 + Math.sin(Date.now() * 0.003 + s.x) * 0.5;
  });
});
```

### Konva.js

- **URL**: https://konvajs.org/
- **What it does**: Object-oriented 2D canvas framework — shapes, layers, event handling, drag-and-drop
- **Bundle size**: ~155 KB min, ~55 KB gzip
- **CDN**: `https://cdn.jsdelivr.net/npm/konva@9/konva.min.js`
- **Integration difficulty**: Low. Creates a stage from a container div.
- **Widget compatibility**: Excellent. `new Konva.Stage({ container: root, ... })` — works directly with widget root.
- **Visual wow factor**: 3/5 (great for interactive diagrams, less for pure visual art)
- **Use cases**: Interactive node editors, drag-and-drop task boards, agent workflow diagrams, whiteboard widgets
- **Example snippet**:
```javascript
const stage = new Konva.Stage({ container: root, width: root.offsetWidth, height: root.offsetHeight });
const layer = new Konva.Layer();
stage.add(layer);

const circle = new Konva.Circle({
  x: root.offsetWidth / 2, y: root.offsetHeight / 2,
  radius: 40, fill: '#0ff', shadowColor: '#0ff',
  shadowBlur: 20, draggable: true
});
layer.add(circle);
layer.draw();
```

### Paper.js

- **URL**: http://paperjs.org/
- **What it does**: Vector graphics scripting — Bezier paths, boolean operations, smooth animations
- **Bundle size**: ~230 KB min (paper-core without PaperScript)
- **CDN**: `https://cdn.jsdelivr.net/npm/paper@0.12/dist/paper-core.min.js`
- **Integration difficulty**: Low. Attach to a canvas element.
- **Widget compatibility**: Good. Needs a `<canvas>` element.
- **Visual wow factor**: 4/5 (beautiful vector art, smooth curves)
- **Use cases**: Animated bezier paths, flowcharts, organic shapes, generative vector art, circuit-board visualizations
- **Example snippet**:
```javascript
const canvas = document.createElement('canvas');
canvas.style.cssText = 'width:100%;height:100%';
canvas.width = root.offsetWidth; canvas.height = root.offsetHeight;
root.appendChild(canvas);

paper.setup(canvas);
const path = new paper.Path();
path.strokeColor = '#0ff';
path.strokeWidth = 2;
for (let i = 0; i < 50; i++) {
  path.add(new paper.Point(Math.random() * canvas.width, Math.random() * canvas.height));
}
path.smooth();
paper.view.onFrame = (e) => {
  path.segments.forEach((seg, i) => {
    seg.point.y += Math.sin(e.time * 2 + i * 0.5) * 0.5;
  });
};
```

---

## 3. Data Visualization

### D3.js v7 (Modular)

- **URL**: https://d3js.org/
- **What it does**: Low-level data visualization primitives — scales, axes, shapes, force layouts, transitions
- **Bundle size**: ~90 KB gzip (full), ~30 KB for common subset (d3-selection + d3-scale + d3-shape + d3-transition)
- **CDN**: `https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js`
- **Integration difficulty**: Medium-high (steep learning curve). Works purely with DOM.
- **Widget compatibility**: Excellent. D3 manipulates DOM directly — perfect for widget containers.
- **Visual wow factor**: 4/5 (depends on the visualization)
- **Use cases**: Real-time agent metrics charts, force-directed task graphs, streaming data sparklines, animated bar charts, treemaps
- **Example snippet**:
```javascript
const svg = d3.select(root).append('svg')
  .attr('width', root.offsetWidth)
  .attr('height', root.offsetHeight);

const data = Array.from({length: 20}, (_, i) => ({ x: i, y: Math.random() * 100 }));
const x = d3.scaleLinear().domain([0, 19]).range([20, root.offsetWidth - 20]);
const y = d3.scaleLinear().domain([0, 100]).range([root.offsetHeight - 20, 20]);
const line = d3.line().x(d => x(d.x)).y(d => y(d.y)).curve(d3.curveCatmullRom);

svg.append('path')
  .datum(data).attr('d', line)
  .attr('fill', 'none').attr('stroke', '#0ff').attr('stroke-width', 2)
  .attr('filter', 'drop-shadow(0 0 6px #0ff)');
```

### Observable Plot

- **URL**: https://observablehq.com/plot/
- **What it does**: High-level charting built on D3 — one-liner charts from data
- **Bundle size**: ~90 KB gzip (includes D3 dependencies)
- **CDN**: `https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6/+esm`
- **Integration difficulty**: Very low. Pass data, get an SVG.
- **Widget compatibility**: Returns an SVG element — append to `root`.
- **Visual wow factor**: 3/5 (clean and professional, not flashy)
- **Use cases**: Quick agent performance charts, data exploration widgets, auto-generated dashboards
- **Example snippet**:
```javascript
const data = Array.from({length: 50}, (_, i) => ({
  time: i, cpu: Math.random() * 100, mem: 30 + Math.random() * 40
}));
const chart = Plot.plot({
  marks: [
    Plot.lineY(data, {x: "time", y: "cpu", stroke: "#0ff"}),
    Plot.lineY(data, {x: "time", y: "mem", stroke: "#f0f"}),
  ],
  style: { background: "transparent", color: "#888" }
});
root.appendChild(chart);
```

### Vega-Lite + Vega-Embed

- **URL**: https://vega.github.io/vega-lite/
- **What it does**: Declarative JSON grammar for interactive visualizations — spec in, chart out
- **Bundle size**: ~350 KB gzip (vega + vega-lite + vega-embed combined)
- **CDN**: `https://cdn.jsdelivr.net/npm/vega@5`, `https://cdn.jsdelivr.net/npm/vega-lite@5`, `https://cdn.jsdelivr.net/npm/vega-embed@6`
- **Integration difficulty**: Very low. JSON spec + `vegaEmbed(root, spec)`.
- **Widget compatibility**: Excellent — renders into any container.
- **Visual wow factor**: 3/5 (statistical charts, not artistic)
- **Use cases**: Agent-generated charts from data, interactive filtering dashboards, auto-visualizations
- **Example snippet**:
```javascript
const spec = {
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "data": { "values": [{"a":"A","b":28},{"a":"B","b":55},{"a":"C","b":43}] },
  "mark": "bar",
  "encoding": {
    "x": {"field":"a","type":"nominal"},
    "y": {"field":"b","type":"quantitative"},
    "color": {"value":"#0ff"}
  },
  "background": "transparent",
  "config": {"axis":{"labelColor":"#888","titleColor":"#888","gridColor":"#333"}}
};
vegaEmbed(root, spec, {actions: false});
```

---

## 4. Animation Libraries

### GSAP (GreenSock Animation Platform)

- **URL**: https://gsap.com/
- **What it does**: Industry-standard animation engine — timelines, easing, scroll triggers, morphing, motion paths
- **Bundle size**: ~72 KB min, ~25 KB gzip (core). Plugins add ~5-15 KB each.
- **CDN**: `https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js`
- **Integration difficulty**: Very low. Call `gsap.to()` on any DOM element.
- **Widget compatibility**: Perfect. Animates any DOM element by reference.
- **Visual wow factor**: 5/5 (the animations it enables are stunning)
- **Use cases**: Smooth number counters, staggered card reveals, morphing shapes, elastic bounces, scroll-linked animations within widgets
- **Example snippet**:
```javascript
const items = [];
for (let i = 0; i < 8; i++) {
  const el = document.createElement('div');
  el.style.cssText = `width:40px;height:40px;border-radius:50%;background:#0ff;
    position:absolute;top:50%;left:${10 + i * 12}%;transform:translate(-50%,-50%);opacity:0;
    box-shadow:0 0 15px #0ff;`;
  root.appendChild(el);
  items.push(el);
}
root.style.position = 'relative';

gsap.to(items, {
  opacity: 1, scale: 1.3, duration: 0.8,
  stagger: { each: 0.1, repeat: -1, yoyo: true },
  ease: "elastic.out(1, 0.3)"
});
```

### Anime.js v4

- **URL**: https://animejs.com/
- **What it does**: Lightweight animation engine — CSS, SVG, DOM, JS objects
- **Bundle size**: ~17 KB min, ~7 KB gzip (core), full UMD ~10 KB gzip
- **CDN**: `https://cdn.jsdelivr.net/npm/animejs/dist/bundles/anime.umd.min.js`
- **Integration difficulty**: Very low. Similar API to GSAP but simpler.
- **Widget compatibility**: Perfect. Targets DOM elements or JS objects.
- **Visual wow factor**: 4/5
- **Use cases**: SVG path animations, staggered grid reveals, pulsing effects, morphing shapes
- **Example snippet**:
```javascript
const grid = document.createElement('div');
grid.style.cssText = 'display:grid;grid-template-columns:repeat(10,1fr);gap:2px;width:100%;height:100%;';
root.appendChild(grid);

for (let i = 0; i < 100; i++) {
  const cell = document.createElement('div');
  cell.style.cssText = 'background:#0ff;border-radius:2px;opacity:0;';
  grid.appendChild(cell);
}

anime({
  targets: grid.children,
  opacity: [0, 0.8],
  scale: [0, 1],
  delay: anime.stagger(30, { grid: [10, 10], from: 'center' }),
  loop: true, direction: 'alternate',
  easing: 'easeInOutQuad', duration: 1500
});
```

### Motion (formerly Motion One / Framer Motion)

- **URL**: https://motion.dev/
- **What it does**: Modern animation library built on Web Animations API — hardware-accelerated by default
- **Bundle size**: ~3.8 KB gzip (core `animate()` function)
- **CDN**: `https://cdn.jsdelivr.net/npm/motion@latest/dist/motion.js`
- **Integration difficulty**: Very low. Uses native browser animation APIs.
- **Widget compatibility**: Good. Vanilla JS `animate()` works on any element.
- **Visual wow factor**: 4/5
- **Use cases**: GPU-accelerated transitions, smooth enter/exit animations, spring physics, layout animations
- **Example snippet**:
```javascript
const el = document.createElement('div');
el.style.cssText = 'width:60px;height:60px;background:#0ff;border-radius:50%;
  position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);box-shadow:0 0 20px #0ff;';
root.style.position = 'relative';
root.appendChild(el);

Motion.animate(el, { scale: [1, 1.5, 1], opacity: [0.5, 1, 0.5] }, {
  duration: 2, repeat: Infinity, easing: 'ease-in-out'
});
```

### Lottie (lottie-web / dotLottie)

- **URL**: https://airbnb.io/lottie/
- **What it does**: Renders After Effects animations exported as JSON — designer-to-code pipeline
- **Bundle size**: lottie-web ~82 KB gzip; dotLottie-player ~40 KB gzip (lighter format)
- **CDN**: `https://cdn.jsdelivr.net/npm/lottie-web@5/build/player/lottie.min.js` or `https://cdn.jsdelivr.net/npm/@dotlottie/player-component@latest/dist/dotlottie-player.mjs`
- **Integration difficulty**: Low. Load animation JSON, call `lottie.loadAnimation()`.
- **Widget compatibility**: Renders into any container div.
- **Visual wow factor**: 5/5 (production-quality motion design)
- **Use cases**: Loading states, success/error animations, onboarding sequences, branded motion graphics, agent status indicators
- **Example snippet**:
```javascript
const container = document.createElement('div');
container.style.cssText = 'width:100%;height:100%;';
root.appendChild(container);

lottie.loadAnimation({
  container,
  renderer: 'svg',
  loop: true,
  autoplay: true,
  // Use a public Lottie animation URL
  path: 'https://assets2.lottiefiles.com/packages/lf20_uwR49r.json'
});
```

---

## 5. Particle / Physics Engines

### tsParticles

- **URL**: https://particles.js.org/
- **What it does**: Highly configurable particle system — JSON config drives everything
- **Bundle size**: Slim bundle ~25 KB gzip, full ~60 KB gzip
- **CDN**: `https://cdn.jsdelivr.net/npm/@tsparticles/slim@3/tsparticles.slim.bundle.min.js`
- **Integration difficulty**: Low. Create a div, call `tsParticles.load()` with config.
- **Widget compatibility**: Renders into any container with an ID.
- **Visual wow factor**: 4/5 (preconfigured presets look great)
- **Use cases**: Background particle effects, confetti celebrations, connection-line networks, fireworks on task completion
- **Example snippet**:
```javascript
root.id = root.id || `tspart-${Date.now()}`;
tsParticles.load({
  id: root.id,
  options: {
    background: { color: "transparent" },
    particles: {
      number: { value: 80 },
      color: { value: "#0ff" },
      links: { enable: true, color: "#0ff", opacity: 0.3 },
      move: { enable: true, speed: 1 },
      opacity: { value: 0.6 },
      size: { value: { min: 1, max: 3 } }
    }
  }
});
```

### Matter.js

- **URL**: https://brm.io/matter-js/
- **What it does**: 2D rigid body physics engine — gravity, collisions, constraints, composites
- **Bundle size**: ~90 KB min, ~30 KB gzip
- **CDN**: `https://cdn.jsdelivr.net/npm/matter-js@0.20/build/matter.min.js`
- **Integration difficulty**: Medium. Needs a render loop and canvas.
- **Widget compatibility**: Renders to canvas inside container.
- **Visual wow factor**: 4/5 (physics-based interactions are delightful)
- **Use cases**: Physics-based task boards (drag and drop with gravity), bouncing agent avatars, constraint-based node graphs, interactive toy widgets
- **Example snippet**:
```javascript
const { Engine, Render, World, Bodies, Mouse, MouseConstraint } = Matter;
const engine = Engine.create();
const render = Render.create({
  element: root,
  engine: engine,
  options: { width: root.offsetWidth, height: root.offsetHeight,
    wireframes: false, background: 'transparent' }
});

// Ground + falling circles
World.add(engine.world, Bodies.rectangle(root.offsetWidth/2, root.offsetHeight, root.offsetWidth, 20,
  { isStatic: true, render: { fillStyle: '#333' } }));
for (let i = 0; i < 15; i++) {
  World.add(engine.world, Bodies.circle(
    Math.random() * root.offsetWidth, -50 - Math.random() * 200, 15 + Math.random() * 15,
    { render: { fillStyle: `hsl(${180 + Math.random()*40}, 100%, 60%)` } }
  ));
}

const mouse = Mouse.create(render.canvas);
World.add(engine.world, MouseConstraint.create(engine, { mouse }));
Render.run(render);
Matter.Runner.run(Matter.Runner.create(), engine);
```

### p5.js (Instance Mode)

- **URL**: https://p5js.org/
- **What it does**: Creative coding framework — drawing, interaction, sound, video, WebGL mode
- **Bundle size**: ~340 KB min, ~100 KB gzip (full); ~300 KB min for p5.min.js
- **CDN**: `https://cdn.jsdelivr.net/npm/p5@1/lib/p5.min.js`
- **Integration difficulty**: Low in instance mode. Must use `new p5(sketch, root)` — NOT global mode.
- **Widget compatibility**: Instance mode is specifically designed for embedding. The sketch renders into the provided container.
- **Visual wow factor**: 5/5 (the creative coding ecosystem is unmatched)
- **Use cases**: Generative art backgrounds, interactive particle systems, Perlin noise landscapes, fractal visualizations, audio-reactive art
- **Example snippet**:
```javascript
// p5.js INSTANCE MODE — scoped to widget container
new p5((s) => {
  s.setup = () => {
    s.createCanvas(root.offsetWidth, root.offsetHeight);
    s.background(0, 0);
  };
  s.draw = () => {
    s.background(0, 10); // fade trail
    for (let i = 0; i < 5; i++) {
      const x = s.noise(s.frameCount * 0.01 + i * 100) * s.width;
      const y = s.noise(s.frameCount * 0.01 + i * 200 + 50) * s.height;
      s.noStroke();
      s.fill(0, 255, 255, 100);
      s.circle(x, y, 8 + s.sin(s.frameCount * 0.05 + i) * 5);
    }
  };
}, root);
```

---

## 6. Shader / GPU Compute

### WebGPU Compute Shaders (Native API)

See the WebGPU entry in Section 1. Key addition: compute shaders enable parallel data processing on the GPU without any rendering — useful for physics simulations, data aggregation, and ML inference that feeds into other widgets.

### Shadertoy-Compatible GLSL (via GlslCanvas)

See GlslCanvas in Section 1. The Shadertoy ecosystem has thousands of open-source GLSL shaders that can be adapted for widget backgrounds:
- Plasma effects: `sin(uv.x * 10.0 + u_time) * cos(uv.y * 10.0 - u_time)`
- Voronoi noise fields
- Raymarched 3D scenes in fragment shaders
- Fractal zoom animations (Mandelbrot, Julia sets)

**Widget idea: "Shader Gallery"** — a widget that cycles through GLSL shaders as animated backgrounds, with agent metrics overlaid as text.

---

## 7. Real-Time Video & Streaming

### xterm.js

- **URL**: https://xtermjs.org/
- **What it does**: Full terminal emulator in the browser — ANSI escape codes, Unicode, WebGL renderer
- **Bundle size**: ~265 KB min, ~90 KB gzip (core + WebGL addon)
- **CDN**: `https://cdn.jsdelivr.net/npm/@xterm/xterm@5/lib/xterm.min.js` + CSS: `https://cdn.jsdelivr.net/npm/@xterm/xterm@5/css/xterm.min.css`
- **Integration difficulty**: Low-medium. Create Terminal, open into container. Needs CSS import.
- **Widget compatibility**: Excellent — `terminal.open(root)` works directly.
- **Visual wow factor**: 4/5 (real terminal is always impressive)
- **Use cases**: Live agent terminal output, SSH sessions, log tailing, interactive REPL widgets
- **Example snippet**:
```javascript
// Load CSS (widget system should handle this via link tag)
const term = new Terminal({
  theme: { background: '#0a0a0f', foreground: '#0ff', cursor: '#0ff',
    selectionBackground: 'rgba(0,255,255,0.2)' },
  fontFamily: '"JetBrains Mono", "Fira Code", monospace',
  fontSize: 13, cursorBlink: true
});
term.open(root);
const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);
fitAddon.fit();

// Connect to WebSocket for live agent output
const ws = new WebSocket(`ws://${location.host}/ws/agent-terminal/${agentId}`);
ws.onmessage = (e) => term.write(e.data);
term.onData((data) => ws.send(data));
```

### asciinema-player

- **URL**: https://docs.asciinema.org/manual/player/
- **What it does**: Plays terminal session recordings (.cast files) — lightweight text-based video
- **Bundle size**: ~150 KB gzip (JS + WASM)
- **CDN**: `https://cdn.jsdelivr.net/npm/asciinema-player@3/dist/bundle/asciinema-player.min.js` + CSS
- **Integration difficulty**: Very low. `AsciinemaPlayer.create(src, container)`.
- **Widget compatibility**: Renders into any container element.
- **Visual wow factor**: 4/5 (terminal recordings are surprisingly engaging)
- **Use cases**: Agent work replays, tutorial recordings, CI/CD build log playback, demo widgets
- **Example snippet**:
```javascript
AsciinemaPlayer.create('/api/recordings/agent-session.cast', root, {
  theme: 'monokai',
  fit: 'width',
  autoPlay: true,
  speed: 2,
  idleTimeLimit: 2
});
```

### HLS.js

- **URL**: https://github.com/video-dev/hls.js
- **What it does**: HTTP Live Streaming (HLS) player — adaptive bitrate, low-latency live streaming
- **Bundle size**: ~70 KB gzip
- **CDN**: `https://cdn.jsdelivr.net/npm/hls.js@latest/dist/hls.min.js`
- **Integration difficulty**: Low. Create video element, attach HLS source.
- **Widget compatibility**: Needs a `<video>` element inside the container.
- **Visual wow factor**: 3/5 (it's video — the wow comes from the content)
- **Use cases**: Live agent screen recordings, CI/CD pipeline streams, webcam feeds from agent environments
- **Example snippet**:
```javascript
const video = document.createElement('video');
video.style.cssText = 'width:100%;height:100%;object-fit:contain;';
video.muted = true;
root.appendChild(video);

if (Hls.isSupported()) {
  const hls = new Hls();
  hls.loadSource('/api/streams/agent-screen/live.m3u8');
  hls.attachMedia(video);
  hls.on(Hls.Events.MANIFEST_PARSED, () => video.play());
}
```

### WebCodecs API (Native)

- **URL**: https://developer.mozilla.org/en-US/docs/Web/API/WebCodecs_API
- **What it does**: Low-level video/audio encode/decode — frame-level access without ffmpeg
- **Bundle size**: 0 KB (native browser API, Chrome 94+, Firefox 130+, Safari 16.4+)
- **Integration difficulty**: High. Manual frame management, codec configuration.
- **Widget compatibility**: Decoded frames render to `<canvas>` via `drawImage()`.
- **Visual wow factor**: 4/5 (enables unique real-time video processing)
- **Use cases**: Agent-pushed video frames (encode on backend, decode in widget), real-time video effects, frame-by-frame analysis displays
- **Example snippet**:
```javascript
const canvas = document.createElement('canvas');
canvas.style.cssText = 'width:100%;height:100%';
root.appendChild(canvas);
const ctx = canvas.getContext('2d');

const decoder = new VideoDecoder({
  output: (frame) => {
    canvas.width = frame.displayWidth;
    canvas.height = frame.displayHeight;
    ctx.drawImage(frame, 0, 0);
    frame.close();
  },
  error: (e) => console.error('Decode error:', e)
});
decoder.configure({ codec: 'avc1.42E01E', optimizeForLatency: true });

// Receive encoded chunks from WebSocket
const ws = new WebSocket(`ws://${location.host}/ws/agent-video/${agentId}`);
ws.binaryType = 'arraybuffer';
ws.onmessage = (e) => {
  decoder.decode(new EncodedVideoChunk({
    type: 'key', // or 'delta'
    timestamp: performance.now() * 1000,
    data: e.data
  }));
};
```

### MediaSource Extensions (Native)

- **URL**: https://developer.mozilla.org/en-US/docs/Web/API/Media_Source_Extensions_API
- **What it does**: Feed byte streams to `<video>` elements — enables custom streaming
- **Bundle size**: 0 KB (native browser API)
- **Integration difficulty**: Medium. Buffer management, codec string formatting.
- **Widget compatibility**: Needs a `<video>` element.
- **Visual wow factor**: 3/5 (enables streaming, the visuals depend on content)
- **Use cases**: Agent screen recordings streamed in real-time, custom adaptive bitrate player, low-latency live feeds

---

## 8. Agent-Fed Real-Time Video (Special Focus)

This section synthesizes approaches for agents to push visual content to dashboard widgets.

### Architecture Options

#### Option A: WebSocket + Canvas Frame Push
Agent encodes frames server-side (ffmpeg/Pillow), sends as base64 or binary over WebSocket, widget draws to canvas.
- **Latency**: Low (~50ms per frame)
- **Bandwidth**: High (uncompressed frames are large)
- **Libraries needed**: None (native Canvas 2D API)

#### Option B: WebSocket + WebCodecs
Agent encodes H.264/VP9 chunks server-side, sends over WebSocket, widget decodes with WebCodecs API.
- **Latency**: Very low (~16ms with `optimizeForLatency`)
- **Bandwidth**: Low (compressed video)
- **Libraries needed**: None (native WebCodecs)

#### Option C: HLS/DASH Streaming
Agent writes to HLS segments on the server, widget plays with HLS.js.
- **Latency**: Medium (2-6 seconds with LL-HLS)
- **Bandwidth**: Optimized (adaptive bitrate)
- **Libraries needed**: HLS.js (~70 KB gzip)

#### Option D: asciinema for Terminal Recording
Agent records terminal sessions as .cast files, widget plays with asciinema-player.
- **Latency**: Near real-time with streaming .cast
- **Bandwidth**: Minimal (text-only)
- **Libraries needed**: asciinema-player (~150 KB gzip)

#### Option E: WebRTC for Screen Capture
Agent runs a headless browser, captures screen via `getDisplayMedia()`, streams via WebRTC to widget.
- **Latency**: Lowest possible (~30ms)
- **Bandwidth**: Adaptive (WebRTC handles it)
- **Libraries needed**: None (native WebRTC), or a signaling library

### Recommended Approach

**For terminal output**: Option D (asciinema) for recordings, xterm.js + WebSocket for live.

**For visual content**: Option B (WebCodecs) for the best latency/bandwidth ratio. The backend would:
1. Capture agent screen or generate frames (e.g., from Remotion)
2. Encode to H.264 using ffmpeg or a hardware encoder
3. Stream encoded chunks over WebSocket
4. Widget decodes with WebCodecs and draws to canvas

**For quick wins**: Option A (raw frames over WebSocket) is simplest to implement but doesn't scale to high resolutions.

---

## 9. Audio Visualization

### Tone.js

- **URL**: https://tonejs.github.io/
- **What it does**: Web Audio framework — synthesizers, effects, scheduling, transport
- **Bundle size**: ~150 KB gzip
- **CDN**: `https://cdn.jsdelivr.net/npm/tone@latest/build/Tone.min.js`
- **Integration difficulty**: Medium. Requires user gesture to start audio context.
- **Widget compatibility**: Audio output is global. Visualization connects to canvas.
- **Visual wow factor**: 4/5 (audio + visual = immersive)
- **Use cases**: Sonification of agent events, notification sounds, ambient soundscapes, audio-reactive visualizations

### Meyda

- **URL**: https://meyda.js.org/
- **What it does**: Audio feature extraction — spectral centroid, loudness, MFCCs, RMS
- **Bundle size**: ~20 KB gzip
- **CDN**: `https://cdn.jsdelivr.net/npm/meyda@5/dist/web/meyda.min.js`
- **Integration difficulty**: Low. Connect to Web Audio API nodes.
- **Widget compatibility**: Pure analysis — feeds data to any visualization.
- **Visual wow factor**: 4/5 (when paired with a renderer)
- **Use cases**: Audio spectrum widgets, voice activity detection for agent calls, music visualization

### Web Audio API (Native)

- **URL**: https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API
- **What it does**: Built-in audio processing — AnalyserNode gives FFT data for visualization
- **Bundle size**: 0 KB (native)
- **Use cases**: Real-time frequency bars, waveform displays, beat detection for animation sync

---

## 10. Generative Art / Creative Coding

### p5.js (Instance Mode) — see Section 5

The premier creative coding library. Key patterns for widgets:
- **Perlin noise flows**: `noise(x * 0.01, y * 0.01, frameCount * 0.005)` for organic movement
- **Attractor systems**: Lorenz, Clifford, De Jong attractors with trail rendering
- **Cellular automata**: Game of Life, Rule 110 on canvas
- **Voronoi tessellation**: Using `p5.voronoi` addon

### Canvas Sketch (by Matt DesLauriers)

- **URL**: https://github.com/mattdesl/canvas-sketch
- **What it does**: Framework for generative art — manages canvas, animation loops, export
- **Bundle size**: ~8 KB gzip (core)
- **Integration difficulty**: Low. Creates a canvas with animation loop.
- **Widget compatibility**: Good, though designed for standalone pages.
- **Visual wow factor**: 5/5 (professional generative art toolkit)
- **Use cases**: Exportable art widgets, print-quality generative designs

---

## 11. Maps / Geospatial

### Leaflet

- **URL**: https://leafletjs.com/
- **What it does**: Lightweight interactive maps — tiles, markers, popups, GeoJSON
- **Bundle size**: ~42 KB gzip (JS) + ~3 KB gzip (CSS)
- **CDN**: `https://cdn.jsdelivr.net/npm/leaflet@1.9/dist/leaflet.min.js` + CSS
- **Integration difficulty**: Very low. `L.map(root).setView([lat, lng], zoom)`.
- **Widget compatibility**: Excellent — initializes directly on a container element.
- **Visual wow factor**: 3/5 (functional maps, customizable with dark tiles)
- **Use cases**: Agent location tracking, deployment region maps, server infrastructure visualization
- **Example snippet**:
```javascript
root.style.height = '100%';
const map = L.map(root).setView([0, 0], 2);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '', maxZoom: 19
}).addTo(map);
// Add agent markers
L.circleMarker([37.7749, -122.4194], {
  radius: 8, color: '#0ff', fillColor: '#0ff', fillOpacity: 0.5
}).addTo(map).bindPopup('Agent: Betelgeuse');
```

### Deck.gl

- **URL**: https://deck.gl/
- **What it does**: Large-scale WebGL data visualization — millions of points, 3D geospatial layers
- **Bundle size**: ~300 KB gzip (core + layers)
- **CDN**: `https://cdn.jsdelivr.net/npm/deck.gl@latest/dist.min.js`
- **Integration difficulty**: Medium. Standalone scripting API available.
- **Widget compatibility**: Can render without Mapbox, or overlay on map tiles.
- **Visual wow factor**: 5/5 (stunning at scale)
- **Use cases**: Massive data point clouds, 3D building visualizations, hexbin aggregations, arc layers for network traffic

---

## 12. Graph / Network Visualization

### Cytoscape.js

- **URL**: https://js.cytoscape.org/
- **What it does**: Graph theory library — nodes, edges, layouts, styling, analysis algorithms
- **Bundle size**: ~365 KB min, ~112 KB gzip
- **CDN**: `https://cdn.jsdelivr.net/npm/cytoscape@3/dist/cytoscape.min.js`
- **Integration difficulty**: Low. `cytoscape({ container: root, elements: [...] })`.
- **Widget compatibility**: Excellent — takes a container element directly.
- **Visual wow factor**: 4/5 (beautiful graph layouts with custom styling)
- **Use cases**: Agent dependency graphs, task flow networks, constellation topology, workflow DAGs
- **Example snippet**:
```javascript
const cy = cytoscape({
  container: root,
  elements: [
    { data: { id: 'betelgeuse', label: 'Betelgeuse' } },
    { data: { id: 'rigel', label: 'Rigel' } },
    { data: { id: 'sirius', label: 'Sirius' } },
    { data: { source: 'betelgeuse', target: 'rigel' } },
    { data: { source: 'rigel', target: 'sirius' } },
  ],
  style: [
    { selector: 'node', style: {
      'background-color': '#0ff', 'label': 'data(label)',
      'color': '#0ff', 'text-valign': 'bottom', 'text-margin-y': 8,
      'font-size': 11, 'width': 20, 'height': 20
    }},
    { selector: 'edge', style: {
      'line-color': 'rgba(0,255,255,0.3)', 'width': 1,
      'curve-style': 'bezier'
    }}
  ],
  layout: { name: 'cose', animate: true }
});
```

### Sigma.js + Graphology

- **URL**: https://www.sigmajs.org/
- **What it does**: WebGL-based graph renderer — handles thousands of nodes at 60fps
- **Bundle size**: ~50 KB gzip (sigma) + ~30 KB gzip (graphology)
- **CDN**: `https://cdn.jsdelivr.net/npm/sigma@3/build/sigma.min.js`
- **Integration difficulty**: Medium. Requires graphology for data model.
- **Widget compatibility**: Takes a container element.
- **Visual wow factor**: 4/5 (WebGL rendering is smooth)
- **Use cases**: Large-scale agent interaction networks, code dependency graphs, real-time connection maps

### 3d-force-graph

- **URL**: https://github.com/vasturiano/3d-force-graph
- **What it does**: 3D force-directed graph using Three.js — interactive node/edge visualization
- **Bundle size**: ~500 KB gzip (includes Three.js)
- **CDN**: `https://cdn.jsdelivr.net/npm/3d-force-graph`
- **Integration difficulty**: Low. `ForceGraph3D()(root).graphData(data)`.
- **Widget compatibility**: Creates its own canvas inside the container.
- **Visual wow factor**: 5/5 (3D graph exploration is mesmerizing)
- **Use cases**: 3D agent constellation (replacing current 2D canvas), code knowledge graphs, task dependency exploration
- **Example snippet**:
```javascript
const data = {
  nodes: [
    { id: 'betelgeuse', color: '#ff4444' },
    { id: 'rigel', color: '#aaaaff' },
    { id: 'sirius', color: '#00ffff' },
  ],
  links: [
    { source: 'betelgeuse', target: 'rigel' },
    { source: 'rigel', target: 'sirius' },
  ]
};

ForceGraph3D()(root)
  .graphData(data)
  .nodeColor('color')
  .linkColor(() => 'rgba(0,255,255,0.2)')
  .backgroundColor('rgba(0,0,0,0)')
  .nodeLabel('id')
  .width(root.offsetWidth)
  .height(root.offsetHeight);
```

### force-graph (2D)

- **URL**: https://github.com/vasturiano/force-graph
- **What it does**: 2D force-directed graph on HTML5 canvas — fast, interactive
- **Bundle size**: ~60 KB gzip
- **CDN**: `https://cdn.jsdelivr.net/npm/force-graph`
- **Integration difficulty**: Very low. Same API as 3d-force-graph but 2D.
- **Widget compatibility**: Excellent — canvas-based, container-aware.
- **Visual wow factor**: 3/5

---

## 13. Terminal Emulators

### xterm.js — see Section 7

The definitive browser terminal. Additional addons worth loading:
- **@xterm/addon-fit**: Auto-resize to container (~2 KB)
- **@xterm/addon-webgl**: GPU-accelerated rendering (~30 KB)
- **@xterm/addon-search**: Text search in terminal buffer (~5 KB)
- **@xterm/addon-web-links**: Clickable URLs (~3 KB)

### xterm-player

- **URL**: https://github.com/JavaCS3/xterm-player
- **What it does**: Plays terminal recordings using xterm.js — supports asciinema .cast format
- **Bundle size**: ~100 KB gzip (includes xterm.js)
- **CDN**: Available via npm, can be bundled for CDN
- **Use cases**: Replay agent terminal sessions with full xterm rendering

---

## 14. Code Editors / Markdown Rendering

### CodeMirror 6

- **URL**: https://codemirror.net/
- **What it does**: Extensible code editor — syntax highlighting, autocomplete, multiple languages
- **Bundle size**: ~124 KB gzip (core + common extensions)
- **CDN**: `https://cdn.jsdelivr.net/npm/codemirror@6` (ESM, needs import map)
- **Integration difficulty**: Medium. Modular architecture requires importing specific extensions.
- **Widget compatibility**: Creates an editor inside a container element.
- **Visual wow factor**: 4/5 (professional code editor in a widget)
- **Use cases**: Live code editing widgets, agent code review panels, config editors, scratch pads
- **Example snippet**:
```javascript
// Using the basic setup bundle
const { EditorView, basicSetup } = CM; // loaded from CDN
const { oneDark } = CMThemes;

const editor = new EditorView({
  doc: '// Agent code output\nfunction hello() {\n  console.log("Hello from widget!");\n}',
  extensions: [basicSetup, oneDark],
  parent: root
});
```

### Monaco Editor

- **URL**: https://microsoft.github.io/monaco-editor/
- **What it does**: VS Code's editor component — IntelliSense, diff view, multi-language support
- **Bundle size**: ~2 MB gzip (heavy!)
- **CDN**: Available via `@monaco-editor/loader` or AMD loader
- **Integration difficulty**: High. AMD module system, complex initialization, large footprint.
- **Widget compatibility**: Can render into containers but requires global setup.
- **Visual wow factor**: 5/5 (it's literally VS Code in a widget)
- **Use cases**: Full code editing, diff views, multi-file editing — but overkill for most widget use cases

**Recommendation**: Use **CodeMirror 6** over Monaco for widgets. 16x smaller, modular, and sufficient for code display/editing.

---

## Integration Architecture

### CDN Loading Strategy

The widget system should implement a **dependency declaration + lazy loading** pattern:

```javascript
// Widget template declares dependencies
{
  "widget_id": "3d-constellation",
  "title": "3D Agent Constellation",
  "deps": ["three"],  // <-- dependency declaration
  "js": "const scene = new THREE.Scene(); ..."
}
```

### Library Registry

A global registry maps library names to CDN URLs and global variable names:

```javascript
// In CanvasEngine or a new LibraryLoader module
const LIBRARY_REGISTRY = {
  'three':        { url: 'https://cdn.jsdelivr.net/npm/three@0.172/build/three.min.js',     global: 'THREE' },
  'gsap':         { url: 'https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js',            global: 'gsap' },
  'd3':           { url: 'https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js',                global: 'd3' },
  'pixi':         { url: 'https://cdn.jsdelivr.net/npm/pixi.js@8/dist/pixi.min.js',         global: 'PIXI' },
  'anime':        { url: 'https://cdn.jsdelivr.net/npm/animejs/dist/bundles/anime.umd.min.js', global: 'anime' },
  'p5':           { url: 'https://cdn.jsdelivr.net/npm/p5@1/lib/p5.min.js',                 global: 'p5' },
  'cytoscape':    { url: 'https://cdn.jsdelivr.net/npm/cytoscape@3/dist/cytoscape.min.js',  global: 'cytoscape' },
  'konva':        { url: 'https://cdn.jsdelivr.net/npm/konva@9/konva.min.js',               global: 'Konva' },
  'matter':       { url: 'https://cdn.jsdelivr.net/npm/matter-js@0.20/build/matter.min.js', global: 'Matter' },
  'xterm':        { url: 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5/lib/xterm.min.js',    global: 'Terminal',
                    css: 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5/css/xterm.min.css' },
  'xterm-fit':    { url: 'https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10/lib/addon-fit.min.js', global: 'FitAddon' },
  'leaflet':      { url: 'https://cdn.jsdelivr.net/npm/leaflet@1.9/dist/leaflet.min.js',    global: 'L',
                    css: 'https://cdn.jsdelivr.net/npm/leaflet@1.9/dist/leaflet.min.css' },
  'tsparticles':  { url: 'https://cdn.jsdelivr.net/npm/@tsparticles/slim@3/tsparticles.slim.bundle.min.js', global: 'tsParticles' },
  'lottie':       { url: 'https://cdn.jsdelivr.net/npm/lottie-web@5/build/player/lottie.min.js', global: 'lottie' },
  'hls':          { url: 'https://cdn.jsdelivr.net/npm/hls.js@latest/dist/hls.min.js',      global: 'Hls' },
  'force-graph':  { url: 'https://cdn.jsdelivr.net/npm/force-graph',                        global: 'ForceGraph' },
  '3d-force-graph': { url: 'https://cdn.jsdelivr.net/npm/3d-force-graph',                   global: 'ForceGraph3D' },
  'sigma':        { url: 'https://cdn.jsdelivr.net/npm/sigma@3/build/sigma.min.js',         global: 'Sigma' },
  'paper':        { url: 'https://cdn.jsdelivr.net/npm/paper@0.12/dist/paper-core.min.js',  global: 'paper' },
  'tone':         { url: 'https://cdn.jsdelivr.net/npm/tone@latest/build/Tone.min.js',      global: 'Tone' },
  'glslcanvas':   { url: 'https://cdn.jsdelivr.net/npm/glslCanvas/dist/GlslCanvas.min.js',  global: 'GlslCanvas' },
  'motion':       { url: 'https://cdn.jsdelivr.net/npm/motion@latest/dist/motion.js',       global: 'Motion' },
  'asciinema':    { url: 'https://cdn.jsdelivr.net/npm/asciinema-player@3/dist/bundle/asciinema-player.min.js', global: 'AsciinemaPlayer',
                    css: 'https://cdn.jsdelivr.net/npm/asciinema-player@3/dist/bundle/asciinema-player.min.css' },
  'gpu':          { url: 'https://cdn.jsdelivr.net/npm/gpu.js@latest/dist/gpu-browser.min.js', global: 'GPU' },
  'plot':         { url: 'https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6/+esm',        global: 'Plot', esm: true },
};
```

### Lazy Loader Implementation

```javascript
// LibraryLoader.js — singleton, loads each library only once
class LibraryLoader {
  constructor() {
    this._loaded = new Map();  // name -> Promise<void>
    this._cssLoaded = new Set();
  }

  async load(name) {
    if (this._loaded.has(name)) return this._loaded.get(name);

    const entry = LIBRARY_REGISTRY[name];
    if (!entry) throw new Error(`Unknown library: ${name}`);

    const promise = new Promise((resolve, reject) => {
      // Load CSS if needed
      if (entry.css && !this._cssLoaded.has(name)) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = entry.css;
        document.head.appendChild(link);
        this._cssLoaded.add(name);
      }

      // Load JS
      const script = document.createElement('script');
      script.src = entry.url;
      if (entry.esm) script.type = 'module';
      script.onload = resolve;
      script.onerror = () => reject(new Error(`Failed to load ${name}`));
      document.head.appendChild(script);
    });

    this._loaded.set(name, promise);
    return promise;
  }

  async loadAll(names) {
    return Promise.all(names.map(n => this.load(n)));
  }
}

// Global singleton
window.__libLoader = window.__libLoader || new LibraryLoader();
```

### Modified WidgetFrame._execJS

```javascript
async _execJS(code) {
  try {
    // Extract deps from widget definition
    const deps = this._def.deps || [];
    if (deps.length > 0) {
      await window.__libLoader.loadAll(deps);
    }
    const fn = new Function('root', 'host', code);
    fn(this._contentEl, this._hostEl);
  } catch (err) {
    this._renderError(err);
  }
}
```

### WebGL Context Management

Browsers limit active WebGL contexts to ~8-16. For a dashboard with multiple widgets:

1. **Limit 3D widgets**: Cap at 2-3 simultaneous WebGL widgets
2. **Scissor technique**: Use one shared Three.js canvas behind the grid, render widget-specific viewports
3. **Context pooling**: When a 3D widget goes off-screen, dispose its renderer and free the context
4. **Prefer Canvas 2D**: For 2D effects, use Canvas 2D API instead of WebGL where possible
5. **PixiJS advantage**: PixiJS v8 uses WebGPU by default with WebGL fallback, and handles context management internally

---

## Widget Ideas by Library

| Widget | Libraries | Description |
|--------|-----------|-------------|
| 3D Star Constellation | Three.js | Replace 2D canvas constellation with 3D rotating starfield, agents as glowing orbs |
| Agent Flow Graph | Cytoscape.js + GSAP | Interactive DAG showing agent task dependencies with animated edges |
| Live Terminal | xterm.js | Real terminal emulator connected to agent subprocess via WebSocket |
| Metrics Dashboard | D3.js / Observable Plot | Real-time sparklines, gauges, and charts for agent CPU/memory/tokens |
| Physics Task Board | Matter.js + GSAP | Tasks as physical objects that fall, stack, and can be dragged |
| Shader Background | GlslCanvas | Per-widget animated shader backgrounds (plasma, voronoi, fractals) |
| Code Review | CodeMirror 6 | Agent code output with syntax highlighting, diff view |
| Audio Visualizer | Tone.js + p5.js | Sonified agent events with reactive waveform visualization |
| Recording Playback | asciinema-player | Replay agent terminal sessions with scrubbing controls |
| Particle Celebration | tsParticles | Confetti/fireworks triggered by task completion events |
| 3D Force Graph | 3d-force-graph | Agent interaction network in explorable 3D space |
| Map Widget | Leaflet | Deployment regions, server locations on dark-themed map tiles |
| Generative Art | p5.js (instance) | Per-project generative art based on project metrics/hash |
| Live Video Stream | HLS.js / WebCodecs | Agent screen capture streamed to dashboard in real-time |
| Lottie Status | lottie-web | Beautiful loading/success/error animations for agent states |

---

## Performance Budget

Recommended loading budget per widget type:

| Category | Max gzip load | Notes |
|----------|---------------|-------|
| Animation only | < 30 KB | GSAP or Anime.js |
| Data visualization | < 100 KB | D3 modular or Observable Plot |
| Terminal | < 100 KB | xterm.js + fit addon |
| 3D scene | < 200 KB | Three.js (load once, share) |
| Full creative | < 300 KB | p5.js (load once) |
| Graph network | < 120 KB | Cytoscape.js |
| Video streaming | < 80 KB | HLS.js |

**Total recommended CDN budget**: ~600-800 KB gzip for the full starter kit (loaded lazily, on demand). Most page loads would pull in only 1-2 libraries at ~100-200 KB.

---

## Summary Comparison Table

| Library | Size (gzip) | CDN | Container-friendly | Wow | Best for |
|---------|-------------|-----|-------------------|-----|----------|
| Three.js | 180 KB | Yes | Yes (canvas) | 5 | 3D scenes, particles |
| PixiJS v8 | 200 KB | Yes | Yes (canvas) | 5 | 2D sprites, filters |
| D3.js v7 | 90 KB | Yes | Yes (SVG/DOM) | 4 | Data charts |
| Observable Plot | 90 KB | Yes | Yes (SVG) | 3 | Quick charts |
| Vega-Lite | 350 KB | Yes | Yes | 3 | Declarative charts |
| GSAP | 25 KB | Yes | Yes | 5 | DOM animation |
| Anime.js v4 | 10 KB | Yes | Yes | 4 | Lightweight animation |
| Motion | 3.8 KB | Yes | Yes | 4 | GPU-accel animation |
| Lottie | 82 KB | Yes | Yes | 5 | Designer animations |
| p5.js | 100 KB | Yes | Yes (instance) | 5 | Creative coding |
| tsParticles | 25 KB | Yes | Yes | 4 | Particle effects |
| Matter.js | 30 KB | Yes | Yes (canvas) | 4 | Physics simulation |
| Cytoscape.js | 112 KB | Yes | Yes | 4 | Graph/network |
| Sigma.js | 80 KB | Yes | Yes | 4 | Large graphs |
| 3d-force-graph | 500 KB | Yes | Yes | 5 | 3D graphs |
| force-graph | 60 KB | Yes | Yes (canvas) | 3 | 2D graphs |
| xterm.js | 90 KB | Yes | Yes | 4 | Terminal emulator |
| asciinema | 150 KB | Yes | Yes | 4 | Terminal replay |
| HLS.js | 70 KB | Yes | Yes (video) | 3 | Live video |
| CodeMirror 6 | 124 KB | Yes | Yes | 4 | Code editor |
| Monaco | 2 MB | Partial | Complex | 5 | Full IDE |
| Konva.js | 55 KB | Yes | Yes | 3 | Interactive canvas |
| Paper.js | 230 KB | Yes | Yes (canvas) | 4 | Vector graphics |
| Leaflet | 42 KB | Yes | Yes | 3 | Maps |
| Deck.gl | 300 KB | Yes | Partial | 5 | Geo data viz |
| GlslCanvas | 15 KB | Yes | Yes (canvas) | 5 | GLSL shaders |
| gpu.js | 100 KB | Yes | Yes | 3 | GPU compute |
| Tone.js | 150 KB | Yes | Global | 4 | Audio synthesis |
| Meyda | 20 KB | Yes | N/A (analysis) | 4 | Audio analysis |

---

*This document was generated by research agent on 2026-03-05. Library versions and sizes may change — verify CDN URLs before deploying to production.*
