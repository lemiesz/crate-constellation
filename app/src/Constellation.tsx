import { useEffect, useRef, useState } from "react";

// point: [id, x, y, genre, title, artist, bpm, energy, color]
type Pt = [string, number, number, string, string | null, string | null, number | null, number | null, string];

type View = { k: number; tx: number; ty: number };

/** Round, anti-aliased dot sprites per color+radius — the Plotly look, on canvas. */
function makeSprite(color: string, r: number, dpr: number): HTMLCanvasElement {
  const s = Math.ceil((r + 1) * 2 * dpr);
  const cv = document.createElement("canvas");
  cv.width = s; cv.height = s;
  const g = cv.getContext("2d")!;
  g.scale(dpr, dpr);
  g.fillStyle = color;
  g.beginPath();
  g.arc(s / (2 * dpr), s / (2 * dpr), r, 0, Math.PI * 2);
  g.fill();
  return cv;
}

export default function Constellation({ selId, similarIds, focus, onSelect, onPlay }: {
  selId: string | null;
  similarIds: string[];
  focus: { id: string; n: number } | null;
  onSelect: (id: string) => void;
  onPlay: (id: string) => void;
}) {
  const [pts, setPts] = useState<Pt[] | null>(null);
  const [legend, setLegend] = useState<[string, string][]>([]);
  const wrapRef = useRef<HTMLDivElement>(null);
  const cvRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<View>({ k: 1, tx: 0, ty: 0 });
  const fitKRef = useRef(1);
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const spriteRef = useRef<Map<string, HTMLCanvasElement>>(new Map());
  const [hover, setHover] = useState<{ x: number; y: number; p: Pt } | null>(null);
  const [, force] = useState(0);
  const redraw = () => force(n => n + 1);

  useEffect(() => {
    fetch("/api/constellation").then(r => r.json()).then(d => {
      setPts(d.rows); setLegend(d.legend ?? []);
    });
  }, []);

  const index = useRef<Map<string, Pt>>(new Map());
  useEffect(() => {
    if (pts) index.current = new Map(pts.map(p => [p[0], p]));
  }, [pts]);

  // fit-to-bounds on first data
  useEffect(() => {
    const el = wrapRef.current;
    if (!pts || !el) return;
    let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
    for (const p of pts) {
      if (p[1] < minX) minX = p[1]; if (p[1] > maxX) maxX = p[1];
      if (p[2] < minY) minY = p[2]; if (p[2] > maxY) maxY = p[2];
    }
    const w = el.clientWidth, h = el.clientHeight, pad = 36;
    const k = Math.min((w - pad * 2) / (maxX - minX), (h - pad * 2) / (maxY - minY));
    fitKRef.current = k;
    viewRef.current = {
      k,
      tx: (w - (maxX + minX) * k) / 2,
      ty: (h - (maxY + minY) * k) / 2,
    };
    redraw();
  }, [pts]);

  // focus a track ("Open in constellation" / neighbor hop) — keep the zoom level
  useEffect(() => {
    const el = wrapRef.current;
    if (!focus || !el) return;
    const p = index.current.get(focus.id);
    if (!p) return;
    const k = Math.max(viewRef.current.k, fitKRef.current * 8);
    viewRef.current = {
      k, tx: el.clientWidth / 2 - p[1] * k, ty: el.clientHeight / 2 - p[2] * k,
    };
    onSelect(focus.id);
    redraw();
  }, [focus?.n]);

  // draw
  useEffect(() => {
    const cv = cvRef.current, el = wrapRef.current;
    if (!cv || !el || !pts) return;
    const dpr = window.devicePixelRatio || 1;
    const w = el.clientWidth, h = el.clientHeight;
    cv.width = w * dpr; cv.height = h * dpr;
    cv.style.width = `${w}px`; cv.style.height = `${h}px`;
    const g = cv.getContext("2d")!;
    g.fillStyle = "#1a1a19";
    g.fillRect(0, 0, cv.width, cv.height);
    const { k, tx, ty } = viewRef.current;
    // radius relative to fit zoom (≈4.4px dots at fit, gentle growth), quantized
    const zr = Math.max(1, k / fitKRef.current);
    const r = Math.round(Math.min(7, 2.2 * Math.pow(zr, 0.3)) * 2) / 2;
    const dimmed = !!selId;
    const sprites = spriteRef.current;
    const skey = (c: string) => `${c}@${r}`;
    g.globalAlpha = dimmed ? 0.28 : 0.85;
    const sz = Math.ceil((r + 1) * 2 * dpr);
    for (const p of pts) {
      const x = (p[1] * k + tx) * dpr, y = (p[2] * k + ty) * dpr;
      if (x < -sz || y < -sz || x > cv.width + sz || y > cv.height + sz) continue;
      let sp = sprites.get(skey(p[8]));
      if (!sp) { sp = makeSprite(p[8], r, dpr); sprites.set(skey(p[8]), sp); }
      g.drawImage(sp, Math.round(x - sz / 2), Math.round(y - sz / 2));
    }
    g.globalAlpha = 1;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    // hover ring
    if (hover) {
      const hx = hover.p[1] * k + tx, hy = hover.p[2] * k + ty;
      g.strokeStyle = "rgba(255,255,255,.65)"; g.lineWidth = 1.2;
      g.beginPath(); g.arc(hx, hy, r + 2.5, 0, Math.PI * 2); g.stroke();
    }
    // constellation: neighbors + edges over the dimmed cloud
    const sp = selId ? index.current.get(selId) : undefined;
    if (sp) {
      const sx = sp[1] * k + tx, sy = sp[2] * k + ty;
      for (const nid of similarIds) {
        const np = index.current.get(nid);
        if (!np) continue;
        const nx = np[1] * k + tx, ny = np[2] * k + ty;
        g.strokeStyle = "rgba(255,255,255,.30)"; g.lineWidth = 1.5;
        g.beginPath(); g.moveTo(sx, sy); g.lineTo(nx, ny); g.stroke();
        g.fillStyle = "#d55181";
        g.beginPath(); g.arc(nx, ny, 5, 0, Math.PI * 2); g.fill();
        g.strokeStyle = "#1a1a19"; g.lineWidth = 1.5;
        g.beginPath(); g.arc(nx, ny, 5, 0, Math.PI * 2); g.stroke();
      }
      g.fillStyle = "#fab219";
      g.beginPath(); g.arc(sx, sy, 6.5, 0, Math.PI * 2); g.fill();
      g.strokeStyle = "#1a1a19"; g.lineWidth = 1.5;
      g.beginPath(); g.arc(sx, sy, 6.5, 0, Math.PI * 2); g.stroke();
      g.strokeStyle = "rgba(250,178,25,.55)"; g.lineWidth = 1.5;
      g.beginPath(); g.arc(sx, sy, 12, 0, Math.PI * 2); g.stroke();
    }
    g.setTransform(1, 0, 0, 1, 0, 0);
  });

  const nearest = (mx: number, my: number, maxPx: number): Pt | null => {
    if (!pts) return null;
    const { k, tx, ty } = viewRef.current;
    let best: Pt | null = null, bd = maxPx * maxPx;
    for (const p of pts) {
      const dx = p[1] * k + tx - mx, dy = p[2] * k + ty - my;
      const d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = p; }
    }
    return best;
  };

  return (
    <div ref={wrapRef} className="constel"
      onWheel={e => {
        const v = viewRef.current;
        const rect = wrapRef.current!.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        const f = Math.exp(-e.deltaY * 0.0015);
        viewRef.current = {
          k: v.k * f, tx: mx - (mx - v.tx) * f, ty: my - (my - v.ty) * f,
        };
        setHover(null); redraw();
      }}
      onMouseDown={e => { dragRef.current = { x: e.clientX, y: e.clientY, moved: false }; }}
      onMouseMove={e => {
        const d = dragRef.current;
        const rect = wrapRef.current!.getBoundingClientRect();
        if (d) {
          const dx = e.clientX - d.x, dy = e.clientY - d.y;
          if (Math.abs(dx) + Math.abs(dy) > 2) d.moved = true;
          viewRef.current.tx += dx; viewRef.current.ty += dy;
          d.x = e.clientX; d.y = e.clientY;
          setHover(null); redraw();
        } else {
          const p = nearest(e.clientX - rect.left, e.clientY - rect.top, 8);
          setHover(p ? { x: e.clientX - rect.left, y: e.clientY - rect.top, p } : null);
        }
      }}
      onMouseUp={e => {
        const d = dragRef.current;
        dragRef.current = null;
        if (d && !d.moved) {
          const rect = wrapRef.current!.getBoundingClientRect();
          const p = nearest(e.clientX - rect.left, e.clientY - rect.top, 10);
          if (p) onSelect(p[0]);
        }
      }}
      onMouseLeave={() => { dragRef.current = null; setHover(null); }}
      onDoubleClick={e => {
        const rect = wrapRef.current!.getBoundingClientRect();
        const p = nearest(e.clientX - rect.left, e.clientY - rect.top, 10);
        if (p) onPlay(p[0]);
      }}
    >
      <canvas ref={cvRef} />
      {!pts && <div className="empty" style={{ position: "absolute", inset: 0 }}>loading the sound map…</div>}
      <div className="legend">
        {legend.map(([n, c]) => (
          <span key={n} className="leg"><span style={{ background: c }} />{n}</span>
        ))}
      </div>
      {hover && (
        <div className="tip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
          <div className="t">{hover.p[4] ?? "—"}</div>
          <div className="m">
            {[hover.p[5], hover.p[3], hover.p[6] ? `${hover.p[6]}` : null,
              hover.p[7] != null ? `E${hover.p[7]}` : null].filter(Boolean).join(" · ")}
          </div>
        </div>
      )}
      <div className="maphint">scroll zoom · drag pan · click select · double-click play</div>
    </div>
  );
}
