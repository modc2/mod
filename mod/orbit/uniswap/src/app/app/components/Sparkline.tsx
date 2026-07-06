"use client";

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
}

export default function Sparkline({ data, width = 80, height = 24, color }: SparklineProps) {
  if (!data || data.length < 2) return <div style={{ width, height }} />;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const final_val = data[data.length - 1];

  const strokeColor = color || (final_val > 0 ? "#4ade80" : final_val < 0 ? "#f87171" : "#8888aa");

  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  });

  const polyline = points.join(" ");

  // Area fill
  const areaPoints = [
    `0,${height}`,
    ...points,
    `${width},${height}`,
  ].join(" ");

  return (
    <svg width={width} height={height} className="inline-block">
      <polygon
        points={areaPoints}
        fill={strokeColor}
        fillOpacity={0.1}
      />
      <polyline
        points={polyline}
        fill="none"
        stroke={strokeColor}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* End dot */}
      <circle
        cx={width}
        cy={Number(points[points.length - 1].split(",")[1])}
        r={2}
        fill={strokeColor}
      />
    </svg>
  );
}
