/** Machine settings form helpers. */

import { getSelectedToolNumber } from "./tools.js";

export function readSettings(form) {
  const num = (id) => {
    const el = form.elements[id];
    return el ? Number(el.value) : NaN;
  };
  return {
    tool_number: getSelectedToolNumber(2),
    engraving_depth_mm: num("engraving_depth_mm"),
    feed_mm_min: num("feed_mm_min"),
    spindle_rpm: num("spindle_rpm"),
    safe_z_mm: num("safe_z_mm"),
    stock_thickness_mm: num("stock_thickness_mm"),
    plunge_mm_min: Number.isFinite(num("plunge_mm_min")) ? num("plunge_mm_min") : 200,
    coolant: true,
  };
}
