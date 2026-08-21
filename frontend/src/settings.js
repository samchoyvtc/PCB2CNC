/** Machine settings form helpers. */

export function readSettings(form) {
  const num = (id) => Number(form.elements[id].value);
  return {
    tool_number: 2,
    engraving_depth_mm: num("engraving_depth_mm"),
    feed_mm_min: num("feed_mm_min"),
    spindle_rpm: num("spindle_rpm"),
    safe_z_mm: num("safe_z_mm"),
    stock_thickness_mm: num("stock_thickness_mm"),
    plunge_mm_min: 200,
    coolant: true,
  };
}
