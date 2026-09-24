"use strict";

(() => {
  const host = document.getElementById("background-flow");
  if (!host) return;

  const canvas = document.createElement("canvas");
  canvas.className = "mark-field";
  canvas.setAttribute("aria-hidden", "true");
  host.prepend(canvas);
  const ctx = canvas.getContext("2d");

  const VIEW_WIDTH = 500;
  const VIEW_HEIGHT = 340;
  // This is a decorative background, not a real-time simulation. A sparse,
  // low-frequency redraw keeps page navigation responsive on ordinary laptops.
  const GRID = 8;
  const FRAME_MS = 160;
  const EDGE_FADE = 24;
  const SPREAD = {cx: 250, cy: 170, left: 320, right: 230, ry: 250, reach: 1.02, softness: 0.55};
  const LETTER = [[118, 128, 151, 272], [118, 239, 235, 272]];
  const LETTER_CLEARANCE = 1.5;
  const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map(value => value / 16 - 0.5);
  const TONES = [
    {color: "#1A3470", radius: 2.4},
    {color: "#3D7BF0", radius: 2.1},
    {color: "#F6C3AE", radius: 1.9},
  ];

  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const isStatic = () => reducedMotion.matches || document.body.classList.contains("motion-static");
  const clamp = (value) => Math.min(Math.max(value, 0), 1);
  let width = 0, height = 0, ratio = 1, scale = 1, offsetX = 0, offsetY = 0, frame = 0, lastDraw = 0;

  const hash = (x, y) => { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); };
  function noise(x, y) {
    const ix = Math.floor(x), iy = Math.floor(y);
    const fx = x - ix, fy = y - iy;
    const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
    const top = hash(ix, iy) + (hash(ix + 1, iy) - hash(ix, iy)) * u;
    const bottom = hash(ix, iy + 1) + (hash(ix + 1, iy + 1) - hash(ix, iy + 1)) * u;
    return top + (bottom - top) * v;
  }
  function fractal(x, y) {
    let sum = 0, amplitude = 0.5, frequency = 1;
    for (let octave = 0; octave < 4; octave++) {
      sum += amplitude * noise(x * frequency, y * frequency);
      frequency *= 2.03;
      amplitude *= 0.5;
    }
    return sum / 0.9375;
  }

  const inLetter = (x, y) => LETTER.some(([left, top, right, bottom]) =>
    x > left - LETTER_CLEARANCE && x < right + LETTER_CLEARANCE && y > top - LETTER_CLEARANCE && y < bottom + LETTER_CLEARANCE);

  function measure() {
    const hostRect = host.getBoundingClientRect();
    const rect = canvas.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    scale = Math.min(hostRect.width / VIEW_WIDTH, hostRect.height / VIEW_HEIGHT) || 1;
    offsetX = (hostRect.width - VIEW_WIDTH * scale) / 2 + hostRect.left - rect.left;
    offsetY = (hostRect.height - VIEW_HEIGHT * scale) / 2 + hostRect.top - rect.top;
  }

  function draw(time) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!width || !height) return;
    ctx.setTransform(ratio * scale, 0, 0, ratio * scale, ratio * offsetX, ratio * offsetY);
    const seconds = time / 1000;
    const drift = seconds * 0.07;
    const minX = -offsetX / scale, maxX = (width - offsetX) / scale;
    const minY = -offsetY / scale, maxY = (height - offsetY) / scale;
    const fade = EDGE_FADE / scale;
    const startX = Math.floor(minX / GRID) * GRID + GRID / 2;
    const startY = Math.floor(minY / GRID) * GRID + GRID / 2;
    for (let y = startY, row = 0; y < maxY; y += GRID, row++) {
      for (let x = startX, col = 0; x < maxX; x += GRID, col++) {
        if (inLetter(x, y)) continue;
        const edge = clamp(Math.min(x - minX, maxX - x, y - minY, maxY - y) / fade);
        if (edge <= 0) continue;
        const reach = Math.hypot((x - SPREAD.cx) / (x < SPREAD.cx ? SPREAD.left : SPREAD.right), (y - SPREAD.cy) / SPREAD.ry);
        const border = SPREAD.reach + 0.42 * (fractal(x / 95 + drift * 0.35, y / 95 - drift * 0.2) - 0.5);
        const presence = clamp((border - reach) / SPREAD.softness) ** 1.3 * edge;
        const dither = BAYER[(row & 3) * 4 + (col & 3)] + 0.5;
        if (presence * 1.35 <= dither) continue;
        const px = x / 70, py = y / 70;
        const warpX = fractal(px + drift, py - drift * 0.6);
        const warpY = fractal(px - drift * 0.4 + 5.2, py + 1.3);
        const field = fractal(px + warpX * 1.9 + drift * 0.5, py + warpY * 1.9 - drift * 0.3)
          + BAYER[(row & 3) * 4 + (col & 3)] * 0.12;
        const tone = TONES[field < 0.45 ? 0 : field < 0.6 ? 1 : 2];
        ctx.globalAlpha = 0.25 + 0.75 * presence;
        ctx.fillStyle = tone.color;
        ctx.beginPath();
        ctx.arc(x, y, tone.radius * (0.45 + 0.55 * presence), 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
  }

  function tick(time) {
    frame = requestAnimationFrame(tick);
    if (time - lastDraw < FRAME_MS || !host.offsetParent) return;
    lastDraw = time;
    draw(time);
  }

  function start() {
    cancelAnimationFrame(frame);
    frame = 0;
    if (isStatic()) { draw(0); return; }
    frame = requestAnimationFrame(tick);
  }

  new ResizeObserver(() => { measure(); draw(isStatic() ? 0 : lastDraw); }).observe(canvas);
  new MutationObserver(start).observe(document.body, {attributes: true, attributeFilter: ["class"]});
  reducedMotion.addEventListener("change", start);
  measure();
  start();
})();
