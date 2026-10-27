import React from "react";
import { VariationsStudio } from "./VariationsStudio";

export default {
  "Studio Controller (All Variations)": () => (
    <VariationsStudio initialVariation="v1-kyoto" initialTheme="dark" />
  ),
  "Variation 1: Kyoto Minimal (Dark)": () => (
    <VariationsStudio initialVariation="v1-kyoto" initialTheme="dark" />
  ),
  "Variation 1: Kyoto Minimal (Light)": () => (
    <VariationsStudio initialVariation="v1-kyoto" initialTheme="light" />
  ),
  "Variation 2: Tokyo Precision (Dark)": () => (
    <VariationsStudio initialVariation="v2-tokyo" initialTheme="dark" />
  ),
  "Variation 2: Tokyo Precision (Light)": () => (
    <VariationsStudio initialVariation="v2-tokyo" initialTheme="light" />
  ),
  "Variation 3: Pure Restraint (Dark)": () => (
    <VariationsStudio initialVariation="v3-muji" initialTheme="dark" />
  ),
  "Variation 3: Pure Restraint (Light)": () => (
    <VariationsStudio initialVariation="v3-muji" initialTheme="light" />
  ),
};
