import { useEffect, useRef, useState } from "react";
import {
  createChart, ColorType, CrosshairMode,
  type IChartApi, type ISeriesApi, type CandlestickData, type UTCTimestamp,
} from "lightweight-charts";
import { api } from "../api";

const RANGES = ["1W", "1M", "6M", "YTD", "1Y", "5Y"];
const POS = "#24c07a";
const NEG = "#f2545b";

/** TradingView-style candlestick chart (lightweight-charts) with range selector. */
export function CandleChart({ symbol, height = 380 }: { symbol: string; height?: number }) {
  const [range, setRange] = useState("6M");
  const [chg, setChg] = useState<number | null>(null);
  const [status, setStatus] = useState<"loading" | "ok" | "empty">("loading");
  const box = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);

  // Create the chart once.
  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8b949e", fontSize: 11 },
      grid: { vertLines: { color: "rgba(255,255,255,.04)" }, horzLines: { color: "rgba(255,255,255,.04)" } },
      rightPriceScale: { borderColor: "#202839" },
      timeScale: { borderColor: "#202839", timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
      autoSize: true,
    });
    const s = c.addCandlestickSeries({
      upColor: POS, downColor: NEG, borderUpColor: POS, borderDownColor: NEG,
      wickUpColor: POS, wickDownColor: NEG,
    });
    chart.current = c; series.current = s;
    return () => { c.remove(); chart.current = null; series.current = null; };
  }, [height]);

  // Load data on symbol/range change.
  useEffect(() => {
    let alive = true;
    setStatus("loading"); setChg(null);
    api.marketHistory(symbol, range).then((r) => {
      if (!alive || !series.current) return;
      const data: CandlestickData[] = (r.bars ?? []).map((b) => ({
        time: (typeof b.t === "number" ? (b.t as UTCTimestamp) : b.t) as CandlestickData["time"],
        open: b.o, high: b.h, low: b.l, close: b.c,
      }));
      if (data.length < 2) { setStatus("empty"); series.current.setData([]); return; }
      series.current.setData(data);
      chart.current?.timeScale().fitContent();
      const first = data[0].close, last = data[data.length - 1].close;
      setChg(first ? ((last - first) / first) * 100 : null);
      setStatus("ok");
    }).catch(() => alive && setStatus("empty"));
    return () => { alive = false; };
  }, [symbol, range]);

  return (
    <div>
      <div style={{ display: "flex", gap: 4, marginBottom: 8, alignItems: "center" }}>
        {RANGES.map((r) => (
          <button key={r} type="button" onClick={() => setRange(r)}
            className={r === range ? "" : "secondary"} style={{ padding: "3px 11px", fontSize: 11 }}>{r}</button>
        ))}
        {chg != null && (
          <span style={{ marginLeft: "auto", fontSize: 13, fontWeight: 700, fontVariantNumeric: "tabular-nums", color: chg >= 0 ? POS : NEG }}>
            {chg >= 0 ? "▲" : "▼"} {chg >= 0 ? "+" : ""}{chg.toFixed(2)}% <span className="muted" style={{ fontWeight: 400 }}>({range})</span>
          </span>
        )}
      </div>
      <div style={{ position: "relative" }}>
        <div ref={box} style={{ width: "100%", height }} />
        {status !== "ok" && (
          <div className="muted" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", fontSize: 12, pointerEvents: "none" }}>
            {status === "loading" ? "indlæser candlesticks…" : `ingen kurshistorik for ${symbol}`}
          </div>
        )}
      </div>
    </div>
  );
}
