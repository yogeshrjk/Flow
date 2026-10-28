/* Animation styles — fullscreen black canvas, GSAP-tweened motion rig.
   Styles: mobius (thick colorful infinity mobius strip) | aurora (silk ribbons) |
           particles (gold->cyan dot ribbon) | nebula (drifting color clouds).
   API: Orb.set(state), Orb.setStyle(name), Orb.style, Orb.styles.
   Persisted in localStorage. Rendering runs on gsap.ticker. */
(function () {
  // live-tweened motion rig (GSAP owns these values)
  const rig = { amp: 14, speed: 0.35, glow: 0.5, spread: 30, lift: 0 };
  const PRESETS = {
    IDLE: { amp: 14, speed: 0.35, glow: 0.5, spread: 30, lift: 0 },
    LISTENING: { amp: 30, speed: 0.7, glow: 0.75, spread: 44, lift: 0 },
    THINKING: { amp: 22, speed: 1.1, glow: 0.65, spread: 38, lift: 6 },
    SPEAKING: { amp: 46, speed: 1.5, glow: 1.0, spread: 60, lift: 0 },
    INTERRUPTED: { amp: 56, speed: 2.2, glow: 1.0, spread: 70, lift: 0 },
  };
  let state = 'IDLE';

  function set(s) {
    state = s in PRESETS ? s : 'IDLE';
    try {
      if (window.gsap) gsap.to(rig, { ...PRESETS[state], duration: 1.1, ease: 'power2.inOut', overwrite: true });
      else Object.assign(rig, PRESETS[state]);
    } catch (e) {
      Object.assign(rig, PRESETS[state]);
    }
  }

  // lens envelope: paper-thin at screen edges, wide in the middle
  function lens(x, t) {
    const c = 0.55 + 0.06 * Math.sin(t * 0.22);
    const d = (x - c) / 0.60;
    const g = Math.exp(-d * d * 2.4);
    return Math.max(0, Math.min(1, g * 1.18));
  }

  function pcolor(x01, alpha, hot) {
    // clamp: dots wrap to x<0 and bokeh drifts past 1 — seg would go out of range
    x01 = Math.max(0, Math.min(1, isFinite(x01) ? x01 : 0));
    // gold -> green -> cyan journey across the screen
    const stops = hot || [[245, 197, 66], [163, 230, 120], [74, 222, 128], [30, 225, 255]];
    const seg = Math.min(stops.length - 2, Math.floor(x01 * (stops.length - 1)));
    const f = x01 * (stops.length - 1) - seg;
    const a = stops[seg], b = stops[seg + 1];
    const c = a.map((v, k) => Math.round(v + (b[k] - v) * f));
    return `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;
  }

  /* ---------------- aurora: soft luminous aurora wave ---------------- */
  const AURORA_BANDS = [
    { c1: [255, 25, 160], c2: [180, 45, 255], c3: [40, 130, 255], spread: 42, px1: 0.0, px2: 1.3, speed: 0.35, a: 0.18 },
    { c1: [0, 230, 255], c2: [45, 120, 255], c3: [175, 45, 255], spread: 48, px1: 1.8, px2: 2.8, speed: 0.28, a: 0.17 },
    { c1: [45, 245, 135], c2: [0, 225, 245], c3: [255, 215, 50], spread: 40, px1: 3.4, px2: 4.2, speed: 0.38, a: 0.16 },
    { c1: [255, 210, 45], c2: [255, 50, 150], c3: [170, 40, 255], spread: 36, px1: 4.8, px2: 5.5, speed: 0.32, a: 0.17 }
  ];

  const MOTES = [];
  for (let i = 0; i < 34; i++) {
    MOTES.push({
      x: Math.random(),
      y: Math.random(),
      vy: 0.0006 + Math.random() * 0.0018,
      ph: Math.random() * Math.PI * 2,
      sz: 0.65 + Math.random() * 1.1
    });
  }

  function drawAuroraThread(ctx, W, t, cy, a1, a2, px1, px2, glow, dir) {
    const al = (0.70 * glow + 0.2) * (dir > 0 ? 1 : 0.85);
    const tg = ctx.createLinearGradient(0, 0, W, 0);
    if (dir > 0) {
      tg.addColorStop(0.0, `rgba(255,25,160,${al})`);
      tg.addColorStop(0.35, `rgba(180,45,255,${al})`);
      tg.addColorStop(0.7, `rgba(0,230,255,${al})`);
      tg.addColorStop(1.0, `rgba(255,215,50,${al})`);
    } else {
      tg.addColorStop(0.0, `rgba(0,230,255,${al})`);
      tg.addColorStop(0.4, `rgba(45,245,135,${al})`);
      tg.addColorStop(0.75, `rgba(180,45,255,${al})`);
      tg.addColorStop(1.0, `rgba(255,25,160,${al})`);
    }
    ctx.strokeStyle = tg;
    ctx.lineWidth = dir > 0 ? 1.6 : 1.1;
    ctx.shadowColor = dir > 0 ? 'rgba(230,50,255,0.85)' : 'rgba(0,225,255,0.8)';
    ctx.shadowBlur = 12;
    ctx.beginPath();
    for (let x = 0; x <= W; x += 4) {
      const u = x / W;
      const env = lens(u, t);
      const y = cy
        + Math.sin(u * 6.0 + t * 0.32 + px1) * a1 * env
        + Math.sin(u * 3.0 - t * 0.22 + px2) * a2 * env
        + Math.sin(u * 12.0 + t * 0.5) * (a1 * 0.2) * env;
      if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  function drawMotes(ctx, W, H, t, e, mic, cy, amp) {
    const speedScale = 0.4 + rig.speed * 0.4;
    for (const m of MOTES) {
      m.y -= m.vy * speedScale * (1 + mic * 1.5);
      if (m.y < 0.04) { m.y = 0.96; m.x = Math.random(); }
      const band = Math.exp(-Math.pow((m.y * H - cy) / (H * 0.16 + amp), 2));
      const tw = 0.5 + 0.5 * Math.sin(t * 0.6 + m.ph);
      const a = band * (0.08 + 0.25 * tw) * Math.min(1, e.glow + 0.2);
      if (a < 0.02) continue;
      ctx.fillStyle = pcolor(m.x, a);
      ctx.beginPath();
      ctx.arc(m.x * W, m.y * H, m.sz * (0.75 + tw * 0.5), 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function renderAurora(ctx, W, H, t, e, mic) {
    const cy = H * 0.58 + rig.lift;
    const amp = (rig.amp * 1.6 + mic * 52) * 1.35;

    // Render soft, borderless luminous aura bands via feathered wave layers
    for (const b of AURORA_BANDS) {
      const grad = ctx.createLinearGradient(0, 0, W, 0);
      grad.addColorStop(0.0, `rgba(${b.c1[0]},${b.c1[1]},${b.c1[2]},${b.a * e.glow * 0.9})`);
      grad.addColorStop(0.5, `rgba(${b.c2[0]},${b.c2[1]},${b.c2[2]},${(b.a + 0.05) * e.glow * 1.1})`);
      grad.addColorStop(1.0, `rgba(${b.c3[0]},${b.c3[1]},${b.c3[2]},${b.a * e.glow * 0.9})`);
      ctx.strokeStyle = grad;

      const spread = (b.spread * 1.3 + amp * 0.6) * (rig.spread / 40);
      const layers = 12; // concentric feathered Gaussian passes for zero sharp edges

      for (let j = 0; j < layers; j++) {
        const v = (j / (layers - 1)) * 2 - 1; // -1 to 1
        const gAlpha = Math.exp(-v * v * 2.8); // smooth Gaussian edge feathering
        if (gAlpha < 0.05) continue;

        ctx.globalAlpha = gAlpha * Math.min(1, e.glow + 0.15);
        ctx.lineWidth = 4.0 + (1 - Math.abs(v)) * 5.5; // wider, softer in core

        ctx.beginPath();
        const step = 4;
        for (let x = 0; x <= W; x += step) {
          const u = x / W;
          const env = lens(u, t);
          if (env < 0.01) continue;

          // rich multi-harmonic center wave undulation
          const ySpine = cy
            + Math.sin(u * 6.4 + t * b.speed + b.px1) * amp * 0.58 * env
            + Math.sin(u * 3.2 - t * (b.speed * 0.7) + b.px2) * amp * 0.72 * env
            + Math.sin(u * 10.8 + t * (b.speed * 1.3)) * amp * 0.24 * env
            + Math.sin(u * 17.0 - t * 0.6) * amp * 0.08 * env;

          const twist = u * 4.6 + t * 0.28 + b.px1;
          const yOffset = v * spread * Math.cos(twist) * (0.35 + 0.65 * env);

          const y = ySpine + yOffset;
          if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
    }

    ctx.globalAlpha = 1.0;

    // Glowing iridescent central threads weaving through the waves
    ctx.save();
    drawAuroraThread(ctx, W, t, cy, amp * 0.40, amp * 0.68, 1.8, 3.6, e.glow, 1);
    drawAuroraThread(ctx, W, t, cy, amp * 0.28, amp * 0.48, -1.2, 1.6, e.glow * 0.8, -1);
    ctx.restore();

    // Soft stardust motes
    drawMotes(ctx, W, H, t, e, mic, cy, amp);
  }

  /* ---------------- particles: 3D multi-chromatic particle ribbon ---------------- */
  const RIBBON_STOPS = [
    [255, 30, 160],   // vibrant hot magenta / rose pink
    [175, 45, 255],   // electric violet
    [40, 130, 255],   // royal blue
    [10, 230, 245],   // vivid cyan / turquoise
    [65, 245, 130],   // neon spring emerald
    [255, 215, 45],   // radiant amber gold
    [255, 55, 140]    // seamless loop back
  ];

  function ribbonColor(u01, alpha, depth) {
    u01 = ((u01 % 1) + 1) % 1;
    const len = RIBBON_STOPS.length - 1;
    const idx = Math.min(len - 1, Math.floor(u01 * len));
    const f = u01 * len - idx;
    const a = RIBBON_STOPS[idx], b = RIBBON_STOPS[idx + 1];
    let r = Math.round(a[0] + (b[0] - a[0]) * f);
    let g = Math.round(a[1] + (b[1] - a[1]) * f);
    let bl = Math.round(a[2] + (b[2] - a[2]) * f);
    if (depth > 0.25) {
      const h = (depth - 0.25) * 0.6;
      r = Math.min(255, Math.round(r + (255 - r) * h));
      g = Math.min(255, Math.round(g + (255 - g) * h));
      bl = Math.min(255, Math.round(bl + (255 - bl) * h));
    }
    return `rgba(${r},${g},${bl},${Math.max(0, Math.min(1, alpha))})`;
  }

  const PDOTS = [];
  const TOTAL_DOTS = 2000;
  for (let i = 0; i < TOTAL_DOTS; i++) {
    const rawV = (Math.random() + Math.random() - 1); // centered Gaussian-like density
    PDOTS.push({
      u: Math.random(),
      v: rawV,
      strand: i % 3,
      sz: 0.75 + Math.random() * 2.2,
      speed: 0.0006 + Math.random() * 0.0018,
      tw: Math.random() * Math.PI * 2,
      ts: 0.4 + Math.random() * 0.8,
      isStar: Math.random() < 0.05,
      jitX: (Math.random() - 0.5) * 5,
      jitY: (Math.random() - 0.5) * 4
    });
  }

  const BOKEH = [];
  for (let i = 0; i < 36; i++) {
    BOKEH.push({
      x: Math.random(),
      y: Math.random(),
      r: 8 + Math.random() * 16,
      a: 0.018 + Math.random() * 0.032,
      dr: 0.0003 + Math.random() * 0.0008,
      ph: Math.random() * Math.PI * 2
    });
  }

  const SPARK_MOTES = [];
  for (let i = 0; i < 40; i++) {
    SPARK_MOTES.push({
      x: Math.random(),
      y: Math.random(),
      vy: 0.0008 + Math.random() * 0.0024,
      sz: 0.65 + Math.random() * 1.2,
      ph: Math.random() * Math.PI * 2
    });
  }

  function renderParticles(ctx, W, H, t, e, mic) {
    const cy = H * 0.58 + rig.lift;
    const amp = (rig.amp * 1.65 + mic * 54) * 1.35;
    const speedScale = 0.5 + rig.speed * 0.5;

    // bokeh depth glow
    for (const b of BOKEH) {
      b.x += b.dr * speedScale;
      if (b.x > 1.05) b.x = -0.05;
      const y = cy + (b.y - 0.5) * H * 0.45 + Math.sin(t * 0.2 + b.ph) * 10;
      const r = b.r * (1 + mic * 0.5);
      const g = ctx.createRadialGradient(b.x * W, y, 0, b.x * W, y, r);
      g.addColorStop(0, ribbonColor(b.x, b.a * e.glow, 0));
      g.addColorStop(1, ribbonColor(b.x, 0, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(b.x * W, y, r, 0, Math.PI * 2);
      ctx.fill();
    }

    const ribbonWidth = (rig.spread * 1.45 + amp * 0.95);

    for (let i = 0; i < TOTAL_DOTS; i++) {
      const p = PDOTS[i];
      p.u += p.speed * speedScale;
      if (p.u > 1.04) {
        p.u = -0.04;
        p.v = (Math.random() + Math.random() - 1);
      }

      const env = lens(p.u, t);
      if (env < 0.02) continue;

      const strandPh = p.strand * (Math.PI * 2 / 3);

      // multi-harmonic carrier wave with pronounced wave undulation
      const ySpine = cy
        + Math.sin(p.u * 6.4 + t * 0.38) * amp * 0.55 * env
        + Math.sin(p.u * 3.2 - t * 0.24 + 0.9) * amp * 0.70 * env
        + Math.sin(p.u * 10.5 + t * 0.52 + strandPh) * amp * 0.22 * env;

      // helical 3D strand twist
      const twist = p.u * 5.2 + t * 0.32 + strandPh;
      const cosT = Math.cos(twist);
      const sinT = Math.sin(twist);
      const depth = sinT * 0.75 + p.v * 0.25;

      const yStrand = sinT * (amp * 0.40) * env;
      const yOffset = p.v * ribbonWidth * cosT * (0.35 + 0.65 * env);
      const ripple = Math.sin(p.u * 15.0 + p.v * 4.5 - t * 0.65) * (3.5 + mic * 7.0) * env;

      const px = p.u * W + depth * 22 * env + p.jitX;
      const py = ySpine + yStrand + yOffset + ripple + p.jitY;

      const colorU = p.u + depth * 0.15 + (p.strand * 0.08) + Math.sin(t * 0.1) * 0.06;
      const twk = 0.6 + 0.4 * Math.sin(p.tw + t * p.ts);
      const depthAlpha = 0.45 + 0.55 * ((depth + 1) * 0.5);
      const alpha = Math.min(1, depthAlpha * twk * (e.glow + 0.2) * (0.3 + 0.7 * env));
      if (alpha < 0.03) continue;

      const depthSize = p.sz * (0.75 + 0.55 * ((depth + 1) * 0.5)) * (0.85 + mic * 0.4);

      if (p.isStar) {
        ctx.fillStyle = ribbonColor(colorU, Math.min(1, alpha * 1.3), depth);
        ctx.beginPath();
        ctx.arc(px, py, depthSize * 1.8, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = `rgba(255,255,255,${Math.min(1, alpha * 1.5)})`;
        ctx.beginPath();
        ctx.arc(px, py, depthSize * 0.85, 0, Math.PI * 2);
        ctx.fill();
      } else {
        ctx.fillStyle = ribbonColor(colorU, alpha, depth);
        ctx.beginPath();
        ctx.arc(px, py, depthSize, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // rising stardust motes
    for (const m of SPARK_MOTES) {
      m.y -= m.vy * speedScale * (1 + mic * 1.6);
      if (m.y < 0.04) { m.y = 0.96; m.x = Math.random(); }
      const band = Math.exp(-Math.pow((m.y * H - cy) / (H * 0.16 + amp), 2));
      const tw = 0.5 + 0.5 * Math.sin(t * 0.7 + m.ph);
      const a = band * (0.12 + 0.35 * tw) * Math.min(1, e.glow + 0.25);
      if (a < 0.02) continue;
      ctx.fillStyle = ribbonColor(m.x, a, 0.4);
      ctx.beginPath();
      ctx.arc(m.x * W, m.y * H, m.sz * (0.7 + tw * 0.6), 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /* ---------------- nebula: drifting color clouds ---------------- */
  // faint drifting starlight
  const STARS = [];
  for (let i = 0; i < 110; i++) STARS.push({ x: Math.random(), y: Math.random(), sz: 0.4 + Math.random() * 1.1, tw: Math.random() * 6.28, ts: 0.5 + Math.random() * 1.5, dr: 0.0004 + Math.random() * 0.0014 });
  const BLOBS = [
    { fx: 0.28, fy: 0.52, r: 0.30, c: '255,45,200', dx: 0.05, dy: 0.03, sp: 0.21, ph: 0.0 },
    { fx: 0.45, fy: 0.62, r: 0.34, c: '150,70,255', dx: 0.06, dy: 0.04, sp: 0.16, ph: 1.7 },
    { fx: 0.62, fy: 0.52, r: 0.32, c: '60,120,255', dx: 0.05, dy: 0.03, sp: 0.19, ph: 3.1 },
    { fx: 0.76, fy: 0.60, r: 0.28, c: '30,225,255', dx: 0.04, dy: 0.03, sp: 0.23, ph: 4.4 },
    { fx: 0.52, fy: 0.45, r: 0.24, c: '220,60,255', dx: 0.04, dy: 0.02, sp: 0.27, ph: 5.5 },
  ];
  function renderNebula(ctx, W, H, t, e, mic) {
    const m = Math.min(W, H);
    // gentle gains: mic (already smoothed) swells the drift instead of jerking it
    const drift = 1 + (rig.speed - 0.35) * 0.6 + mic * 0.35;
    for (const b of BLOBS) {
      const x = W * (b.fx + b.dx * Math.sin(t * b.sp * drift + b.ph));
      const y = H * (b.fy + b.dy * Math.cos(t * b.sp * 0.8 * drift + b.ph));
      const r = m * b.r * (1 + 0.08 * Math.sin(t * 0.4 + b.ph)) * (1 + mic * 0.15);
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(${b.c},${0.20 * e.glow + 0.04})`);
      g.addColorStop(0.6, `rgba(${b.c},${0.10 * e.glow + 0.02})`);
      g.addColorStop(1, `rgba(${b.c},0)`);
      ctx.fillStyle = g;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
    }
    for (const st of STARS) {
      st.x += st.dr * (0.15 + rig.speed * 1.5);
      if (st.x > 1.02) st.x = -0.02;
      const a = (0.03 + 0.13 * (0.5 + 0.5 * Math.sin(t * st.ts + st.tw))) * (0.45 + e.glow * 0.7);
      ctx.fillStyle = `rgba(205,216,255,${a})`;
      ctx.beginPath();
      ctx.arc(st.x * W, st.y * H, st.sz, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /* ---------------- mobius: Thick 3D Infinity Möbius Strip (Flowing Boundaries) ---------------- */
  const MOBIUS_N = 96;
  const TOTAL_BUBBLES = 36;

  // Harmonious, rich, non-glaring chromatic palette:
  // Deep royal indigo -> Cobalt blue -> Electric cyan -> Emerald aqua -> Amber gold -> Soft coral rose -> Royal violet -> Loop
  const MOBIUS_PALETTE = [
    [45, 35, 195],    // Royal indigo
    [37, 99, 235],    // Cobalt blue
    [6, 182, 212],    // Electric cyan
    [16, 185, 129],   // Emerald aqua
    [245, 158, 11],   // Golden amber
    [244, 63, 94],    // Soft coral rose
    [139, 92, 246],   // Royal violet
    [45, 35, 195]     // Seamless loop back
  ];

  function getMobiusColor(u01, alpha, diffuse = 1.0, spec = 0.0) {
    u01 = ((u01 % 1) + 1) % 1;
    const len = MOBIUS_PALETTE.length - 1;
    const idx = Math.min(len - 1, Math.floor(u01 * len));
    const f = u01 * len - idx;
    const a = MOBIUS_PALETTE[idx], b = MOBIUS_PALETTE[idx + 1];
    let r = a[0] + (b[0] - a[0]) * f;
    let g = a[1] + (b[1] - a[1]) * f;
    let bl = a[2] + (b[2] - a[2]) * f;

    // Apply soft satin diffuse lighting without harsh white clipping
    r = Math.min(255, Math.round(r * diffuse + spec * 95));
    g = Math.min(255, Math.round(g * diffuse + spec * 95));
    bl = Math.min(255, Math.round(bl * diffuse + spec * 95));

    return `rgba(${r},${g},${bl},${Math.max(0, Math.min(1, alpha))})`;
  }

  // Key directional light vector (top-left-front) & Specular half-vector
  const ML_RAW = [0.35, -0.65, 0.65];
  const ML_LEN = Math.hypot(...ML_RAW);
  const ML = [ML_RAW[0] / ML_LEN, ML_RAW[1] / ML_LEN, ML_RAW[2] / ML_LEN];

  const MH_RAW = [(ML[0] + 0) * 0.5, (ML[1] + 0) * 0.5, (ML[2] + 1) * 0.5];
  const MH_LEN = Math.hypot(...MH_RAW);
  const MH = [MH_RAW[0] / MH_LEN, MH_RAW[1] / MH_LEN, MH_RAW[2] / MH_LEN];

  // Rounded elliptical cross-section (8 smooth points, no hard box corners):
  // this is what removes the sharp creases along the ribbon and gives the
  // silhouette a soft organic edge instead of a faceted slab look.
  const MOBIUS_SIDES = 8;

  // Pre-allocated typed arrays for zero GC allocations
  const mobiusVerts3D = new Float32Array(MOBIUS_N * MOBIUS_SIDES * 3);
  const mobiusVertsRot = new Float32Array(MOBIUS_N * MOBIUS_SIDES * 3);
  const mobiusVertsScreen = new Float32Array(MOBIUS_N * MOBIUS_SIDES * 3);

  const MOBIUS_QUADS = [];
  for (let i = 0; i < MOBIUS_N * MOBIUS_SIDES; i++) {
    MOBIUS_QUADS.push({
      idxA: 0, idxB: 0, idxC: 0, idxD: 0,
      z: 0, u: 0, diffuse: 1, spec: 0, alpha: 0, isEdge: false
    });
  }

  const MOBIUS_BUBBLES = [];
  for (let i = 0; i < TOTAL_BUBBLES; i++) {
    MOBIUS_BUBBLES.push({
      lobe: i % 2,
      rRatio: 0.35 + Math.random() * 0.85,
      theta: Math.random() * Math.PI * 2,
      y: (Math.random() - 0.5) * 110,
      sz: 2.2 + Math.random() * 5.8,
      speed: (0.006 + Math.random() * 0.014) * (Math.random() > 0.5 ? 1 : -1),
      wobblePh: Math.random() * Math.PI * 2,
      wobbleSp: 0.7 + Math.random() * 1.2,
      colorU: Math.random(),
      sx: 0, sy: 0, szRot: 0, appR: 0
    });
  }

  function drawMobiusBubble(ctx, b, glowMult) {
    const r = b.appR;
    const zScale = Math.max(0.2, Math.min(1.2, (b.szRot + 150) / 300));
    const alpha = Math.min(1, (0.42 + 0.58 * zScale) * glowMult);
    if (alpha < 0.03 || r < 0.8) return;

    const x = b.sx, y = b.sy;

    // 1. Delicate tinted translucent interior (Fresnel look)
    const bg = ctx.createRadialGradient(x, y, r * 0.1, x, y, r);
    bg.addColorStop(0.0, 'rgba(255, 255, 255, 0.01)');
    bg.addColorStop(0.7, getMobiusColor(b.colorU, 0.06 * alpha, 1.0, 0.0));
    bg.addColorStop(1.0, getMobiusColor(b.colorU, 0.35 * alpha, 1.1, 0.1));
    ctx.fillStyle = bg;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();

    // 2. Delicate tinted outer rim stroke
    ctx.strokeStyle = getMobiusColor(b.colorU, 0.70 * alpha, 1.2, 0.2);
    ctx.lineWidth = Math.max(0.75, 1.1 * zScale);
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.stroke();

    // 3. Gentle top-left soft specular glint (pastel tinted, not harsh white)
    const glintX = x - r * 0.35;
    const glintY = y - r * 0.35;
    const glintR = Math.max(0.5, r * 0.26);
    ctx.fillStyle = getMobiusColor(b.colorU, 0.85 * alpha, 1.3, 0.3);
    ctx.beginPath();
    ctx.arc(glintX, glintY, glintR, 0, Math.PI * 2);
    ctx.fill();

    // 4. Bottom-right secondary reflection
    const secX = x + r * 0.28;
    const secY = y + r * 0.28;
    const secR = Math.max(0.4, r * 0.16);
    ctx.fillStyle = getMobiusColor(b.colorU, 0.40 * alpha, 1.1, 0.1);
    ctx.beginPath();
    ctx.arc(secX, secY, secR, 0, Math.PI * 2);
    ctx.fill();
  }

  function renderMobiusStrip(ctx, W, H, t, e, mic) {
    const cx = W / 2;
    const cy = H * 0.54 + rig.lift;
    // Generous larger scale for commanding presence
    const Ax = Math.min(W * 0.36, Math.max(200, H * 0.44), 310);
    const Ay = Ax * 0.54;
    const Az = 38;
    const W0 = Math.max(26, Ax * 0.11 + mic * 10);
    const H0 = Math.max(8, Ax * 0.034 + mic * 3.5);
    const D = 580;
    const speedScale = 0.4 + rig.speed * 0.4;
    const glowMult = (e.glow + 0.3) * (0.85 + mic * 0.4);

    // 1. Soft atmospheric colored aura behind the infinity loop
    const auraGrad = ctx.createRadialGradient(cx, cy, Ax * 0.1, cx, cy, Ax * 1.55);
    auraGrad.addColorStop(0.0, `rgba(139, 92, 246, ${0.12 * glowMult + mic * 0.08})`); // Royal violet
    auraGrad.addColorStop(0.40, `rgba(6, 182, 212, ${0.06 * glowMult + mic * 0.04})`);  // Electric cyan
    auraGrad.addColorStop(0.75, `rgba(45, 35, 195, ${0.02 * glowMult})`);                // Deep royal indigo
    auraGrad.addColorStop(1.0, 'rgba(0, 0, 0, 0)');
    ctx.fillStyle = auraGrad;
    ctx.fillRect(cx - Ax * 1.8, cy - Ay * 2.2, Ax * 3.6, Ay * 4.4);

    // 2. Stationary infinity orientation (NO ring spinning — only subtle organic float)
    const rotX = 0.32 + 0.03 * Math.sin(t * 0.4);
    const rotZ = 0.02 * Math.cos(t * 0.3);

    const cosX = Math.cos(rotX), sinX = Math.sin(rotX);
    const cosZ = Math.cos(rotZ), sinZ = Math.sin(rotZ);

    // Flowing boundary rotation along the infinity ribbon (slowed down for smooth, gentle flow)
    const flowTwist = t * 0.15 * speedScale;

    // 3. Compute 3D Lemniscate geometry with flowing Möbius boundary twist
    for (let i = 0; i < MOBIUS_N; i++) {
      const u = (i / MOBIUS_N) * Math.PI * 2;
      const sinU = Math.sin(u);
      const cosU = Math.cos(u);
      const denom = 1 + sinU * sinU;

      // Harmonic fluid breathing modulation
      const wMod = 1 + Math.sin(u * 2 - t * 0.8 * speedScale) * 0.025 * (1 + mic * 1.0);

      const px = (Ax * cosU / denom) * wMod;
      const py = (Ay * sinU * cosU * 1.45 / denom) * wMod;
      const pz = (Az * sinU) * wMod;

      // Numerical tangent vector along the curve
      const eps = 0.002;
      const sinU1 = Math.sin(u - eps), cosU1 = Math.cos(u - eps), d1 = 1 + sinU1 * sinU1;
      const sinU2 = Math.sin(u + eps), cosU2 = Math.cos(u + eps), d2 = 1 + sinU2 * sinU2;
      const tx = (Ax * cosU2 / d2) - (Ax * cosU1 / d1);
      const ty = (Ay * sinU2 * cosU2 * 1.45 / d2) - (Ay * sinU1 * cosU1 * 1.45 / d1);
      const tz = Az * sinU2 - Az * sinU1;
      const tlen = Math.hypot(tx, ty, tz);
      const Tx = tx / tlen, Ty = ty / tlen, Tz = tz / tlen;

      // In-plane normal
      let n1x = -Ty, n1y = Tx, n1z = 0;
      const n1len = Math.hypot(n1x, n1y, n1z);
      n1x /= n1len; n1y /= n1len;

      // Binormal
      const n2x = Ty * n1z - Tz * n1y;
      const n2y = Tz * n1x - Tx * n1z;
      const n2z = Tx * n1y - Ty * n1x;

      // Rotating boundary twist phase!
      const phi = u + flowTwist;
      const cosP = Math.cos(phi), sinP = Math.sin(phi);

      const wx = cosP * n1x + sinP * n2x;
      const wy = cosP * n1y + sinP * n2y;
      const wz = cosP * n1z + sinP * n2z;

      const hx = -sinP * n1x + cosP * n2x;
      const hy = -sinP * n1y + cosP * n2y;
      const hz = -sinP * n1z + cosP * n2z;

      // Rounded elliptical profile point (smooth tube, not a box)
      for (let s = 0; s < MOBIUS_SIDES; s++) {
        const sa = (s / MOBIUS_SIDES) * Math.PI * 2;
        const cw = Math.cos(sa), chh = Math.sin(sa);
        const idx = (i * MOBIUS_SIDES + s) * 3;
        const x0 = px + cw * W0 * wx + chh * H0 * hx;
        const y0 = py + cw * W0 * wy + chh * H0 * hy;
        const z0 = pz + cw * W0 * wz + chh * H0 * hz;

        // Subtle 3D perspective orientation
        const x2 = x0;
        const y2 = y0 * cosX - z0 * sinX;
        const z2 = y0 * sinX + z0 * cosX;

        const x3 = x2 * cosZ - y2 * sinZ;
        const y3 = x2 * sinZ + y2 * cosZ;
        const z3 = z2;

        mobiusVertsRot[idx] = x3;
        mobiusVertsRot[idx + 1] = y3;
        mobiusVertsRot[idx + 2] = z3;

        const k = D / (D + z3);
        mobiusVertsScreen[idx] = cx + x3 * k;
        mobiusVertsScreen[idx + 1] = cy + y3 * k;
        mobiusVertsScreen[idx + 2] = z3;
      }
    }

    // 4. Update quad faces and calculate soft satin lighting.
    // Lighting contrast is deliberately compressed (high ambient floor, gentle
    // diffuse slope) so adjacent quads blend instead of banding into facets.
    let qIdx = 0;
    for (let i = 0; i < MOBIUS_N; i++) {
      const iNext = (i + 1) % MOBIUS_N;

      for (let s = 0; s < MOBIUS_SIDES; s++) {
        const sNext = (s + 1) % MOBIUS_SIDES;
        const q = MOBIUS_QUADS[qIdx++];
        const idxA = (i * MOBIUS_SIDES + s) * 3;
        const idxB = (i * MOBIUS_SIDES + sNext) * 3;
        const idxC = (iNext * MOBIUS_SIDES + sNext) * 3;
        const idxD = (iNext * MOBIUS_SIDES + s) * 3;
        q.idxA = idxA;
        q.idxB = idxB;
        q.idxC = idxC;
        q.idxD = idxD;
        q.isEdge = false;
        q.u = (i / MOBIUS_N) + t * 0.03 * speedScale;

        const zA = mobiusVertsRot[idxA + 2];
        const zB = mobiusVertsRot[idxB + 2];
        const zC = mobiusVertsRot[idxC + 2];
        const zD = mobiusVertsRot[idxD + 2];
        q.z = (zA + zB + zC + zD) * 0.25;

        const v1x = mobiusVertsRot[idxB] - mobiusVertsRot[idxA];
        const v1y = mobiusVertsRot[idxB + 1] - mobiusVertsRot[idxA + 1];
        const v1z = mobiusVertsRot[idxB + 2] - mobiusVertsRot[idxA + 2];

        const v2x = mobiusVertsRot[idxD] - mobiusVertsRot[idxA];
        const v2y = mobiusVertsRot[idxD + 1] - mobiusVertsRot[idxA + 1];
        const v2z = mobiusVertsRot[idxD + 2] - mobiusVertsRot[idxA + 2];

        let nx = v1y * v2z - v1z * v2y;
        let ny = v1z * v2x - v1x * v2z;
        let nz = v1x * v2y - v1y * v2x;
        const nlen = Math.max(0.0001, Math.sqrt(nx * nx + ny * ny + nz * nz));
        nx /= nlen; ny /= nlen; nz /= nlen;

        const dot = Math.abs(nx * ML[0] + ny * ML[1] + nz * ML[2]);
        const sdot = Math.abs(nx * MH[0] + ny * MH[1] + nz * MH[2]);
        const spec = Math.pow(sdot, 8); // Soft satin sheen, not blinding white

        const ambient = 0.45;
        const diff = 0.50 * dot;
        q.diffuse = Math.min(1.0, ambient + diff);
        q.spec = 0.20 * spec;
        q.alpha = Math.min(1, 0.90 * glowMult);
      }
    }

    // Sort quads back to front
    MOBIUS_QUADS.sort((a, b) => a.z - b.z);

    // 5. Update floating bubbles orbiting both lobes of the infinity loop
    for (let i = 0; i < TOTAL_BUBBLES; i++) {
      const b = MOBIUS_BUBBLES[i];
      b.theta += b.speed * speedScale;
      b.y += Math.sin(t * b.wobbleSp + b.wobblePh) * (0.35 + mic * 0.8);

      const lobeX = (b.lobe === 0 ? -Ax * 0.52 : Ax * 0.52);
      const lobeR = (Ax * 0.45) * b.rRatio;
      const bx0 = lobeX + lobeR * Math.cos(b.theta);
      const by0 = b.y + lobeR * 0.55 * Math.sin(b.theta);
      const bz0 = Math.sin(b.theta) * 35;

      const by2 = by0 * cosX - bz0 * sinX;
      const bz2 = by0 * sinX + bz0 * cosX;
      const bx3 = bx0 * cosZ - by2 * sinZ;
      const by3 = bx0 * sinZ + by2 * cosZ;
      const bz3 = bz2;

      const kb = D / (D + bz3);
      b.sx = cx + bx3 * kb;
      b.sy = cy + by3 * kb;
      b.szRot = bz3;
      b.appR = Math.max(1.5, b.sz * kb * (0.9 + mic * 0.25));
    }

    // 6. Draw back bubbles (z < -8)
    for (let i = 0; i < TOTAL_BUBBLES; i++) {
      const b = MOBIUS_BUBBLES[i];
      if (b.szRot < -8) drawMobiusBubble(ctx, b, glowMult);
    }

    // 7. Draw Möbius strip quads (sorted back to front)
    for (let i = 0; i < MOBIUS_QUADS.length; i++) {
      const q = MOBIUS_QUADS[i];
      const idxA = q.idxA, idxB = q.idxB, idxC = q.idxC, idxD = q.idxD;

      ctx.beginPath();
      ctx.moveTo(mobiusVertsScreen[idxA], mobiusVertsScreen[idxA + 1]);
      ctx.lineTo(mobiusVertsScreen[idxB], mobiusVertsScreen[idxB + 1]);
      ctx.lineTo(mobiusVertsScreen[idxC], mobiusVertsScreen[idxC + 1]);
      ctx.lineTo(mobiusVertsScreen[idxD], mobiusVertsScreen[idxD + 1]);
      ctx.closePath();

      ctx.fillStyle = getMobiusColor(q.u, q.alpha, q.diffuse, q.spec);
      ctx.fill();

      // Whisper-thin seam softening (anti-alias feel, not an outline)
      ctx.strokeStyle = getMobiusColor(q.u, q.alpha * 0.45, q.diffuse * 1.05, q.spec);
      ctx.lineWidth = 0.5;
      ctx.stroke();
    }

    // 8. Draw soft tinted seam accents (every other segment is plenty —
    // full-density rails read as sharp wireframe edges)
    for (let c = 0; c < MOBIUS_SIDES; c++) {
      for (let i = 0; i < MOBIUS_N; i += 2) {
        const segNext = (i + 2) % MOBIUS_N;
        const vIdx1 = (i * MOBIUS_SIDES + c) * 3;
        const vIdx2 = (segNext * MOBIUS_SIDES + c) * 3;
        const zAvg = (mobiusVertsRot[vIdx1 + 2] + mobiusVertsRot[vIdx2 + 2]) * 0.5;

        if (zAvg >= -20) {
          const u = (i / MOBIUS_N) + t * 0.03 * speedScale;
          ctx.beginPath();
          ctx.moveTo(mobiusVertsScreen[vIdx1], mobiusVertsScreen[vIdx1 + 1]);
          ctx.lineTo(mobiusVertsScreen[vIdx2], mobiusVertsScreen[vIdx2 + 1]);
          ctx.strokeStyle = getMobiusColor(u, 0.32 * glowMult, 1.05, 0.15);
          ctx.lineWidth = 1.0 + mic * 0.5;
          ctx.stroke();
        }
      }
    }

    // 9. Draw front bubbles (z >= -8)
    for (let i = 0; i < TOTAL_BUBBLES; i++) {
      const b = MOBIUS_BUBBLES[i];
      if (b.szRot >= -8) drawMobiusBubble(ctx, b, glowMult);
    }
  }

  const STYLES = {
    particles: { label: 'Particle Ribbon', render: renderParticles },
    mobius: { label: 'Infinity Möbius', render: renderMobiusStrip },
    ring: { label: 'Infinity Möbius', render: renderMobiusStrip }, // alias for backward compatibility
    aurora: { label: 'Aurora Silk', render: renderAurora },
    nebula: { label: 'Nebula Drift', render: renderNebula },
  };
  let styleName = 'particles';
  let micSmooth = 0; // exponentially smoothed mic level (see frame())
  try {
    const saved = localStorage.getItem('animStyle');
    if (saved && STYLES[saved]) styleName = saved;
    else styleName = 'particles';
  } catch (e) {}
  function setStyle(n) {
    if (!STYLES[n]) return;
    styleName = n;
    try { localStorage.setItem('animStyle', n); } catch (e) {}
  }

  function frame() {
    const cv = document.getElementById('orb');
    if (!cv) return;
    const t = (window.__auroraT = (window.__auroraT || 0) + 0.016);
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const cw = cv.clientWidth || window.innerWidth, ch = cv.clientHeight || window.innerHeight;
    if (cv.width !== Math.round(cw * dpr) || cv.height !== Math.round(ch * dpr)) {
      cv.width = Math.round(cw * dpr);
      cv.height = Math.round(ch * dpr);
    }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const micRaw = Math.min(1, (window.__micLevel || 0) * 22);
    // Fast attack keeps the voice feeling responsive; slow release absorbs
    // frame-to-frame RMS jitter that otherwise reads as blinking/flashing.
    micSmooth += (micRaw - micSmooth) * (micRaw > micSmooth ? 0.35 : 0.08);
    const mic = micSmooth;
    const e = { glow: rig.glow };
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, cw, ch);
    ctx.globalCompositeOperation = 'lighter';
    // soft breathing stage glow behind styles
    const sg = ctx.createRadialGradient(cw / 2, ch * 0.56, 0, cw / 2, ch * 0.56, Math.max(cw, ch) * 0.55);
    const isInfinity = (styleName === 'mobius' || styleName === 'ring');
    if (isInfinity) {
      sg.addColorStop(0, `rgba(124, 58, 237, ${0.05 * e.glow + mic * 0.04})`);
      sg.addColorStop(0.5, `rgba(6, 182, 212, ${0.02 * e.glow + mic * 0.02})`);
      sg.addColorStop(1, 'rgba(0, 0, 0, 0)');
    } else {
      sg.addColorStop(0, `rgba(88,70,220,${0.05 * e.glow + mic * 0.04})`);
      sg.addColorStop(1, 'rgba(88,70,220,0)');
    }
    ctx.fillStyle = sg;
    ctx.fillRect(0, 0, cw, ch);
    STYLES[styleName].render(ctx, cw, ch, t, e, mic);
    ctx.globalCompositeOperation = 'source-over';
  }

  window.Orb = {
    set, setStyle,
    get state() { return state; },
    get style() { return styleName; },
    styles: Object.keys(STYLES),
  };
  try {
    if (window.gsap && gsap.ticker) gsap.ticker.add(frame);
    else (function loop() { frame(); requestAnimationFrame(loop); })();
  } catch (e) {
    (function loop() { frame(); requestAnimationFrame(loop); })();
  }
  set('IDLE');
})();
