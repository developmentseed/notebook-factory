/* Run wizard: map-based area picker + live summary. Plain JS, Leaflet from CDN. */
(function () {
  const form = document.getElementById("run-form");
  if (!form) return;
  const cfg = window.RUN_FORM || {};
  const requiresArea = form.dataset.requiresArea === "1";
  const allowedLevels = (form.dataset.areaLevels || "").split(",").filter(Boolean).map(Number);

  const areaIds = document.getElementById("area_ids");
  const countryEl = document.getElementById("country");
  const levelEl = document.getElementById("level");
  const allEl = document.getElementById("all_at_level");
  const allCount = document.getElementById("all-count");
  const selectionEl = document.getElementById("selection");
  const summaryEl = document.getElementById("summary");

  let selected = new Map(); // id -> name
  let layer = null;
  let map = null;
  let featureCount = 0;

  (areaIds && areaIds.value ? areaIds.value.split(",") : []).forEach((id) => { if (id) selected.set(String(id), "#" + id); });

  function styleFor(id) {
    const on = selected.has(String(id));
    return { color: on ? "#C2431F" : "#2E6FB0", weight: on ? 2 : 1, fillColor: on ? "#E4572E" : "#2E6FB0", fillOpacity: on ? 0.45 : 0.12 };
  }

  function renderSelection() {
    if (!selectionEl) return;
    if (allEl && allEl.checked) {
      selectionEl.textContent = `Every area at this level (${featureCount}).`;
    } else if (selected.size === 0) {
      selectionEl.textContent = "No area selected. Click areas on the map to select them.";
    } else {
      const names = Array.from(selected.values()).slice(0, 8).join(", ");
      selectionEl.textContent = `${selected.size} selected: ${names}${selected.size > 8 ? ", …" : ""}`;
    }
    if (areaIds) areaIds.value = Array.from(selected.keys()).join(",");
    renderSummary();
  }

  function renderSummary() {
    if (!summaryEl) return;
    const bits = [cfg.template];
    if (requiresArea && countryEl) {
      const country = countryEl.options[countryEl.selectedIndex]?.text;
      const level = levelEl ? levelEl.options[levelEl.selectedIndex]?.text : "";
      if (countryEl.value) {
        if (allEl && allEl.checked) bits.push(`${country} ${level} (all ${featureCount})`);
        else if (selected.size) bits.push(`${country} · ${selected.size === 1 ? Array.from(selected.values())[0] : selected.size + " areas"}`);
      }
    }
    form.querySelectorAll(".field").forEach((f) => {
      const name = f.dataset.param;
      const inputs = f.querySelectorAll("[name='" + name + "']");
      let value = [];
      inputs.forEach((i) => {
        if ((i.type === "checkbox" || i.type === "radio")) { if (i.checked) value.push(i.labels && i.labels[0] ? i.labels[0].textContent.trim() : i.value); }
        else if (i.tagName === "SELECT") { if (i.value) value.push(i.options[i.selectedIndex].text); }
        else if (i.value) value.push(i.value);
      });
      if (value.length) bits.push(`${f.dataset.title}: ${value.join(", ")}`);
    });
    summaryEl.textContent = bits.join(" · ");
  }

  async function loadAreas() {
    if (!map || !countryEl || !countryEl.value) return;
    const url = `${cfg.geojsonUrl}?country=${encodeURIComponent(countryEl.value)}&level=${encodeURIComponent(levelEl.value)}`;
    if (layer) { map.removeLayer(layer); layer = null; }
    selectionEl.textContent = "Loading boundaries…";
    const res = await fetch(url);
    if (!res.ok) { selectionEl.textContent = "Could not load boundaries for this selection."; return; }
    const gj = await res.json();
    featureCount = gj.features.length;
    if (allCount) allCount.textContent = featureCount ? `(${featureCount})` : "";
    // drop selections that are not part of this layer any more
    const ids = new Set(gj.features.map((f) => String(f.properties.id)));
    for (const id of Array.from(selected.keys())) if (!ids.has(id)) selected.delete(id);
    gj.features.forEach((f) => { if (selected.has(String(f.properties.id))) selected.set(String(f.properties.id), f.properties.name); });
    layer = L.geoJSON(gj, {
      style: (f) => styleFor(f.properties.id),
      onEachFeature: (f, l) => {
        l.bindTooltip(f.properties.name, { sticky: true });
        l.on("click", () => {
          const id = String(f.properties.id);
          if (allEl && allEl.checked) { allEl.checked = false; }
          if (selected.has(id)) selected.delete(id); else selected.set(id, f.properties.name);
          l.setStyle(styleFor(id));
          renderSelection();
        });
      },
    }).addTo(map);
    if (featureCount) map.fitBounds(layer.getBounds(), { padding: [10, 10] });
    renderSelection();
  }

  if (requiresArea && document.getElementById("map") && window.L) {
    map = L.map("map", { worldCopyJump: true }).setView([20, 0], 2);
    // Carto's raster tiles require an API key; its vector style does not. Attribution comes from the style.
    L.maplibreGL({ style: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json" }).addTo(map);
    if (allowedLevels.length && levelEl) {
      Array.from(levelEl.options).forEach((o) => { if (!allowedLevels.includes(Number(o.value))) o.disabled = true; });
      if (levelEl.selectedOptions[0]?.disabled) levelEl.value = String(allowedLevels[0]);
    }
    countryEl.addEventListener("change", () => { selected.clear(); loadAreas(); });
    levelEl.addEventListener("change", () => { selected.clear(); loadAreas(); });
    allEl.addEventListener("change", () => { if (allEl.checked) { selected.clear(); if (layer) layer.setStyle((f) => styleFor(f.properties.id)); } renderSelection(); });
    loadAreas();
  }
  form.addEventListener("input", renderSummary);
  form.addEventListener("change", renderSummary);
  renderSelection();
})();
