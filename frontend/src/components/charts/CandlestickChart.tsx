"use client";

import { useEffect, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type HistogramData,
  type Time,
} from "lightweight-charts";

interface Props {
  candles: CandlestickData<Time>[];
  volumes: HistogramData<Time>[];
  sma50?: { time: Time; value: number }[];
  sma200?: { time: Time; value: number }[];
  rsi?: { time: Time; value: number }[];
  height?: number;
}

export function CandlestickChart({
  candles,
  volumes,
  sma50 = [],
  sma200 = [],
  rsi = [],
  height = 690,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const sma50Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const sma200Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const rsiRef = useRef<ISeriesApi<"Line"> | null>(null);

  const [showRSI, setShowRSI] = useState(false);

  // Create chart + base series once
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0f172a" },
        textColor: "#94a3b8",
        fontFamily: "Inter, sans-serif",
        fontSize: 12,
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#475569", labelBackgroundColor: "#1e293b" },
        horzLine: { color: "#475569", labelBackgroundColor: "#1e293b" },
      },
      rightPriceScale: {
        borderColor: "#1e293b",
        scaleMargins: { top: 0.1, bottom: 0.25 },
      },
      timeScale: {
        borderColor: "#1e293b",
        timeVisible: true,
        secondsVisible: false,
      },
      width: containerRef.current.offsetWidth,
      height,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });

    const volSeries = chart.addHistogramSeries({
      color: "#334155",
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    chartRef.current = chart;
    candleRef.current = candleSeries;
    volumeRef.current = volSeries;

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.offsetWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      sma50Ref.current = null;
      sma200Ref.current = null;
      rsiRef.current = null;
    };
  }, [height]);

  // Update candle + volume data
  useEffect(() => {
    if (!candleRef.current || !volumeRef.current || !chartRef.current) return;
    if (candles.length === 0) return;
    candleRef.current.setData(candles);
    volumeRef.current.setData(volumes);
    chartRef.current.timeScale().fitContent();
  }, [candles, volumes]);

  // SMA 50 — create once, update data, remove when empty
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (sma50.length > 0) {
      if (!sma50Ref.current) {
        sma50Ref.current = chart.addLineSeries({
          color: "#f59e0b",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
      }
      sma50Ref.current.setData(sma50);
    } else if (sma50Ref.current) {
      chart.removeSeries(sma50Ref.current);
      sma50Ref.current = null;
    }
  }, [sma50]);

  // SMA 200 — create once, update data, remove when empty
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (sma200.length > 0) {
      if (!sma200Ref.current) {
        sma200Ref.current = chart.addLineSeries({
          color: "#8b5cf6",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
      }
      sma200Ref.current.setData(sma200);
    } else if (sma200Ref.current) {
      chart.removeSeries(sma200Ref.current);
      sma200Ref.current = null;
    }
  }, [sma200]);

  // RSI — add/remove pane based on showRSI toggle
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    if (showRSI && rsi.length > 0) {
      // Compress main chart area to make room for RSI panel
      chart.priceScale("right").applyOptions({
        scaleMargins: { top: 0.05, bottom: 0.42 },
      });
      // Volume stays inside the candle area (top 58%), pinned to its bottom
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.50, bottom: 0.42 },
      });

      if (!rsiRef.current) {
        const rsiSeries = chart.addLineSeries({
          color: "#22d3ee",
          lineWidth: 1,
          priceScaleId: "rsi",
          priceLineVisible: false,
          lastValueVisible: true,
          crosshairMarkerVisible: true,
        });
        chart.priceScale("rsi").applyOptions({
          scaleMargins: { top: 0.65, bottom: 0.02 },
          borderColor: "#1e293b",
        });
        // Overbought / oversold / midline markers
        rsiSeries.createPriceLine({
          price: 70,
          color: "#ef4444",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: "OB",
        });
        rsiSeries.createPriceLine({
          price: 30,
          color: "#10b981",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: "OS",
        });
        rsiSeries.createPriceLine({
          price: 50,
          color: "#475569",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: false,
          title: "",
        });
        rsiRef.current = rsiSeries;
      }
      rsiRef.current.setData(rsi);
    } else {
      // Restore original margins
      chart.priceScale("right").applyOptions({
        scaleMargins: { top: 0.1, bottom: 0.25 },
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      if (rsiRef.current) {
        chart.removeSeries(rsiRef.current);
        rsiRef.current = null;
      }
    }
  }, [showRSI, rsi]);

  return (
    <div className="relative">
      <div ref={containerRef} className="rounded-xl overflow-hidden" />
      {/* Toolbar — legend + RSI toggle */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-3 bg-black/40 px-2.5 py-1 rounded text-xs">
        <span className="flex items-center gap-1.5 pointer-events-none">
          <span className="w-5 h-0.5 bg-amber-400 inline-block rounded-full" />
          <span className="text-amber-400 font-medium">SMA 50</span>
        </span>
        <span className="flex items-center gap-1.5 pointer-events-none">
          <span className="w-5 h-0.5 bg-violet-400 inline-block rounded-full" />
          <span className="text-violet-400 font-medium">SMA 200</span>
        </span>
        {rsi.length > 0 && (
          <button
            onClick={() => setShowRSI((v) => !v)}
            className={`flex items-center gap-1.5 cursor-pointer transition-opacity ${showRSI ? "opacity-100" : "opacity-50 hover:opacity-80"}`}
          >
            <span className="w-5 h-0.5 bg-cyan-400 inline-block rounded-full" />
            <span className="text-cyan-400 font-medium">RSI 14</span>
          </button>
        )}
      </div>
    </div>
  );
}
