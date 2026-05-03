import React from "react";

export function Card({ className = "", ...props }) {
  return (
    <div
      className={`rounded-lg border bg-white text-slate-950 ${className}`}
      {...props}
    />
  );
}

export function CardContent({ className = "", ...props }) {
  return <div className={className} {...props} />;
}