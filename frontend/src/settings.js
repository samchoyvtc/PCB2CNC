/** Machine settings form helpers. */

import { getSelectedToolCuts, getSelectedToolNumber } from "./tools.js";

export function readSettings(form) {
  const num = (id) => {
    const el = form.elements[id];
    return el ? Number(el.value) : NaN;
  };
  const cuts = getSelectedToolCuts();
  return {
    tool_number: getSelectedToolNumber(2),
    engraving_depth_mm: num("engraving_depth_mm"),
    drill_depth_mm: Number.isFinite(num("drill_depth_mm")) ? num("drill_depth_mm") : 1.6,
    safe_z_mm: num("safe_z_mm"),
    ...cuts,
  };
}
