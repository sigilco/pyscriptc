import React from "react";
import "./styles/global.css";

export default function CosmosDecorator({ children }: { children: React.ReactNode }) {
  return (
    <div className="w-full min-h-screen bg-[#06080d] font-sans antialiased">
      {children}
    </div>
  );
}
