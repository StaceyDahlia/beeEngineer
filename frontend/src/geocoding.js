(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.BeelineGeocoding = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function validCoords(coords) {
    return Array.isArray(coords)
      && coords.length >= 2
      && Number.isFinite(Number(coords[0]))
      && Number.isFinite(Number(coords[1]))
      && Number(coords[0]) >= -90
      && Number(coords[0]) <= 90
      && Number(coords[1]) >= -180
      && Number(coords[1]) <= 180;
  }

  function buildForwardUrl(baseUrl, address, center) {
    const params = new URLSearchParams({
      q: String(address || "").trim(),
      limit: "5",
      lat: String(center[0]),
      lon: String(center[1]),
      zoom: "11",
      location_bias_scale: "0.25",
    });
    return `${String(baseUrl).replace(/\/$/, "")}/api/?${params.toString()}`;
  }

  function buildReverseUrl(baseUrl, coords) {
    if (!validCoords(coords)) throw new Error("INVALID_COORDINATES");
    const params = new URLSearchParams({
      lat: String(Number(coords[0])),
      lon: String(Number(coords[1])),
      limit: "1",
    });
    return `${String(baseUrl).replace(/\/$/, "")}/reverse?${params.toString()}`;
  }

  function candidateFromFeature(feature, fallback = "") {
    const props = feature?.properties || {};
    const street = props.street || props.name || "";
    const parts = [
      street && `${street}${props.housenumber ? `, ${props.housenumber}` : ""}`,
      props.district,
      props.city,
      props.state,
      props.country,
    ].filter(Boolean);
    const display = parts.join(", ") || String(fallback || "Выбранная точка");
    const raw = feature?.geometry?.coordinates;
    const coords = Array.isArray(raw) ? [Number(raw[1]), Number(raw[0])] : null;
    return validCoords(coords) ? { coords, display, raw: feature } : null;
  }

  function candidatesFromResponse(data, fallback = "") {
    const features = Array.isArray(data?.features) ? data.features : [];
    return features.map((feature) => candidateFromFeature(feature, fallback)).filter(Boolean);
  }

  function coordinateLabel(coords) {
    if (!validCoords(coords)) return "Координаты не выбраны";
    return `${Number(coords[0]).toFixed(6)}, ${Number(coords[1]).toFixed(6)}`;
  }

  function pointSelection(coords, display, source = "map") {
    if (!validCoords(coords)) throw new Error("INVALID_COORDINATES");
    const label = String(display || "").trim() || `Точка на карте: ${coordinateLabel(coords)}`;
    return { coords: [Number(coords[0]), Number(coords[1])], display: label, source, confirmed: true };
  }

  function buildEmergencyEvent({ eventId, eventTime, title, address, coords, requiredVehicle }) {
    const point = pointSelection(coords, address, "confirmed");
    return {
      event_id: String(eventId),
      type: "emergency_job",
      event_time: String(eventTime),
      title: String(title || "").trim(),
      address: point.display,
      coords: point.coords,
      required_vehicle: requiredVehicle || null,
    };
  }

  async function forwardGeocode(fetchJson, baseUrl, address, center) {
    const query = String(address || "").trim();
    if (!query) throw new Error("ADDRESS_REQUIRED");
    const data = await fetchJson(buildForwardUrl(baseUrl, query, center));
    const candidates = candidatesFromResponse(data, query);
    if (!candidates.length) throw new Error("GEOCODER_EMPTY");
    return { address: query, candidates };
  }

  async function reverseGeocode(fetchJson, baseUrl, coords) {
    const data = await fetchJson(buildReverseUrl(baseUrl, coords));
    const candidates = candidatesFromResponse(data, coordinateLabel(coords));
    if (!candidates.length) throw new Error("GEOCODER_EMPTY");
    return candidates[0];
  }

  return {
    validCoords,
    buildForwardUrl,
    buildReverseUrl,
    candidateFromFeature,
    candidatesFromResponse,
    coordinateLabel,
    pointSelection,
    buildEmergencyEvent,
    forwardGeocode,
    reverseGeocode,
  };
});
